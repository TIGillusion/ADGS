import concurrent.futures
import json
import os
import shutil
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

from core import terminal_runtime as runtime


def test_incident_hold_stops_direct_launch_before_record_creation(tmp_path):
    (tmp_path / 'worker-hold').touch()
    store = runtime.TerminalStore(tmp_path)
    with pytest.raises(runtime.AttemptInterrupted) as error:
        runtime.run_hermes('ags_140', '这条指令不应启动', store=store)
    assert error.value.reason == 'worker_safety_hold'
    assert not (tmp_path / 'terminals.sqlite').exists()


def test_records_are_per_attempt_ordered_and_replayable(tmp_path):
    store = runtime.TerminalStore(tmp_path)
    for attempt in ("a", "b"):
        store.create(attempt_id=attempt, execution_id="e", profile="ags_140", run_id="r", task_id="t")
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda n: store.append("a", "data", {"text": str(n)}), range(40)))
    records = store.read_records("a")
    assert [row["seq"] for row in records] == list(range(1, 42))
    assert len(store.read_records("a", after=40)) == 1
    assert store.latest_seq("b") == 1
    assert len(store.get_execution("e")["attempts"]) == 2
    assert store.get_attempt("a")["node"] == "ags_140"
    assert store.list_attempts(run_id="other") == []


def test_repeated_provider_compression_failure_is_terminal_infrastructure():
    assert runtime._provider_failure_reason("compression summary failed: Connection error") == ""
    repeated = "compression summary failed: InternalServerError: Connection error\n" * 2
    assert runtime._provider_failure_reason(repeated) == "Provider repeatedly failed during Hermes request; stopped without retrying the task"
    assert runtime._provider_failure_reason("inference request expired while waiting for admission\n" * 2)


def test_exhausted_http_provider_error_preserves_actual_status():
    terminal = ("Response truncated — stream ended before completion\n"
                "Attempt 1/3 failed: HTTP 500: Internal Server Error\n"
                "Attempt 2/3 failed: HTTP 500: Internal Server Error\n"
                "API failed after 3 retries — HTTP 500: Internal Server Error\n")
    assert "HTTP 500" in runtime._provider_failure_reason(terminal)
    assert runtime._provider_failure_reason("Attempt 1/3 failed: HTTP 500: Internal Server Error") == ""
    reason, error = runtime._refine_exit_failure(1, Path("not-present-receipt"), "saved-session", terminal)
    assert reason == "provider_error"
    assert "HTTP 500" in error


def test_stale_provider_chatter_is_recognized(tmp_path):
    """2026-10-22 实测:opencode 网关无响应时 hermes 的新话术必须被识别,
    否则监控干等 CLI 挣扎十几分钟后 exit 1,还会被归成不可读的 process_error"""
    stale = ("⚠️ No response from provider for 240s (model: qwen3.8-27b). Reconnecting...\n" * 3
             + "❌ OpenCode Zen rejected the request and retrying won't help: "
             "Provider has been unresponsive (no response received) for 5 consecutive stale attempts\n")
    reason = runtime._provider_failure_reason(stale)
    assert reason and "无响应" in reason
    assert runtime._provider_failure_reason("No response from provider for 240s") == ""
    assert runtime._provider_failure_reason("") == ""


def test_exit_failure_classification(tmp_path):
    """exit 1 且无 receipt 时按终端尾部归类;receipt 命中仍是 iteration_limit"""
    receipt = tmp_path / "turn-receipt.json"
    tail_stale = "Provider has been unresponsive (no response received)\n" * 2 + "retrying won't help"
    reason, error = runtime._refine_exit_failure(1, receipt, "s1", tail_stale)
    assert reason == "provider_error" and "无响应" in error and "code 1" in error
    receipt.write_text(json.dumps({"termination_reason": "iteration_limit", "session_id": "s1"}), encoding="utf-8")
    reason, error = runtime._refine_exit_failure(1, receipt, "s1", tail_stale)
    assert reason == "iteration_limit"
    receipt.write_text(json.dumps({"termination_reason": "iteration_limit", "session_id": "other"}), encoding="utf-8")
    reason, error = runtime._refine_exit_failure(1, receipt, "s1", "")
    assert reason == "process_error" and error == "Hermes exited with code 1"
    reason, _ = runtime._refine_exit_failure(2, tmp_path / "missing.json", "s1", tail_stale)
    assert reason == "process_error"


def test_output_utf8_offset_does_not_split_characters(tmp_path):
    store = runtime.TerminalStore(tmp_path)
    store.create(attempt_id="a", execution_id="e", profile="p")
    (store.paths("e", "a") / "output.vt").write_bytes("a中文b".encode())
    first = store.read_output("a", limit=5)
    second = store.read_output("a", offset=first["next_offset"], limit=5)
    assert first["data"] + second["data"] == "a中文b"


def test_terminal_store_uses_explicit_state_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("ADGS_STATE_DIR", str(tmp_path))
    assert runtime.TerminalStore().root == tmp_path


def test_scoped_publish_preserves_existing_files_and_rebases_manifest(tmp_path):
    staging = (tmp_path / "staging").resolve()
    output = (tmp_path / "output").resolve()
    staging.mkdir()
    output.mkdir()
    report = staging / "report.md"
    report.write_text("new report", encoding="utf-8")
    (staging / "DELIVERABLES.txt").write_text(str(report), encoding="utf-8")
    first = runtime._publish_recovery_artifacts(staging, output)
    assert first == [str(output / "report.md"), str(output / "DELIVERABLES.txt")]
    assert (output / "DELIVERABLES.txt").read_text(encoding="utf-8") == str(output / "report.md")
    assert runtime._publish_recovery_artifacts(staging, output) == first
    report.write_text("attempted overwrite", encoding="utf-8")
    with pytest.raises(FileExistsError):
        runtime._publish_recovery_artifacts(staging, output)
    assert (output / "report.md").read_text(encoding="utf-8") == "new report"


def test_scoped_database_does_not_replace_unrelated_profile_history(tmp_path):
    source = (tmp_path / "scoped.sqlite").resolve()
    destination = (tmp_path / "profile.sqlite").resolve()
    for path, session in ((source, "resumed"), (destination, "unrelated")):
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE sessions (id TEXT)")
            db.execute("INSERT INTO sessions VALUES (?)", (session,))
    assert not runtime._sync_recovery_database(source, destination, {"resumed"})
    with sqlite3.connect(destination) as db:
        assert db.execute("SELECT id FROM sessions").fetchall() == [("unrelated",)]


def test_resume_lease_is_per_session_not_profile(tmp_path):
    first = runtime.SessionLease(tmp_path, "s1")
    second = runtime.SessionLease(tmp_path, "s2")
    try:
        with pytest.raises(RuntimeError, match="SESSION_NOT_OWNED"):
            runtime.SessionLease(tmp_path, "s1")
    finally:
        first.close()
        second.close()
    runtime.SessionLease(tmp_path, "s1").close()


@pytest.fixture
def fake_installation(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "hermes_frozen_exe", lambda: None)
    source = tmp_path / "installed"
    (source / "hermes_cli").mkdir(parents=True)
    (source / "hermes_cli" / "__init__.py").write_text("")
    (source / "hermes_cli" / "auth.py").write_text("_PROVIDER_ALIASES = {}\n")
    (source / "hermes_cli" / "oneshot.py").write_text("def _load_resume_target(db, resume): return resume, [], {}\n")
    (source / "hermes_state.py").write_text("class SessionDB:\n    def get_messages_as_conversation(self, *a, **kw): return []\n    def get_resume_conversations(self, *a): return [], []\n")
    (source / "providers.py").write_text("def list_providers(): return []\n")
    (source / "hermes_cli" / "models.py").write_text("def opencode_provider_family(value): return value\n")
    (source / "hermes_cli" / "main.py").write_text('''
import sys, os, json, sqlite3, time, subprocess
from pathlib import Path
assert sys.argv[0] == "hermes" and "--profile" in sys.argv, "Hermes argv must precede module import"
assert "hermes_cli.auth" not in sys.modules, "Profile resolution must precede provider imports"
def main():
    args = sys.argv
    assert "--yolo" in args and "--oneshot" in args and "-Q" not in args
    text = Path(args[args.index("--query-file") + 1]).read_text(encoding="utf-8")
    profile = args[args.index("--profile") + 1]
    home = Path(os.environ["HERMES_HOME"]) / "profiles" / profile
    sid = args[args.index("--resume") + 1] if "--resume" in args else "session-" + text
    if text == "child":
        directory = Path(args[args.index("--query-file") + 1]).parent
        code = "import subprocess, sys, time; from pathlib import Path; child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); Path(" + repr(str(directory / "grandchild.pid")) + ").write_text(str(child.pid)); time.sleep(30)"
        child = subprocess.Popen([sys.executable, "-c", code])
        (directory / "child.pid").write_text(str(child.pid))
    crumbs = home / "terminal-sessions"
    crumbs.mkdir(parents=True, exist_ok=True)
    (crumbs / ("term_session_id-" + os.environ["TERM_SESSION_ID"])).write_text(json.dumps({"session_id":sid,"ts":time.time()}))
    print("\\x1b[32m真实终端-" + text + "\\x1b[0m", flush=True)
    db = sqlite3.connect(home / "state.db", timeout=5)
    db.execute("CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, end_reason TEXT, parent_session_id TEXT, last_activity_at REAL)")
    db.execute("CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, session_id TEXT,role TEXT,content TEXT,tool_call_id TEXT,tool_calls TEXT,tool_name TEXT,timestamp REAL,finish_reason TEXT)")
    db.execute("INSERT OR REPLACE INTO sessions VALUES (?,NULL,NULL,?)", (sid,time.time()))
    db.execute("INSERT INTO messages(session_id,role,content,timestamp) VALUES (?,'assistant',?,?)", (sid,"RESULT-"+text,time.time()))
    db.commit()
    db.close()
    if text == "busy":
        subprocess.run([sys.executable, "-c", "import time; end=time.monotonic()+4; exec('while time.monotonic()<end: pass')"], check=True)
    time.sleep(30 if text == "long" else 1.2)
''', encoding="utf-8")
    (tmp_path / "core").mkdir()
    shutil.copy2(runtime.ROOT / "core" / "hermes_worker.py", tmp_path / "core" / "hermes_worker.py")
    shutil.copy2(runtime.ROOT / "core" / "provider_compat.py", tmp_path / "core" / "provider_compat.py")
    shutil.copy2(runtime.ROOT / "core" / "resume_projection.py", tmp_path / "core" / "resume_projection.py")
    monkeypatch.setattr(runtime, "ROOT", tmp_path)
    monkeypatch.setattr(runtime, "hermes_installation", lambda: (Path(sys.executable), source))
    events = []
    monkeypatch.setattr(runtime, "_emit", lambda name, **data: events.append((name, data)))
    return runtime.TerminalStore(tmp_path / "records"), events


def test_compression_does_not_replay_messages_from_stale_breadcrumb(fake_installation):
    store, events = fake_installation
    _, source = runtime.hermes_installation()
    main = source / "hermes_cli" / "main.py"
    main.write_text(main.read_text(encoding="utf-8").replace(
        '    time.sleep(30 if text == "long" else 1.2)',
        '''    if text == "compress":
        db = sqlite3.connect(home / "state.db", timeout=5)
        db.execute("UPDATE sessions SET end_reason='compression' WHERE id=?", (sid,))
        db.execute("INSERT INTO sessions VALUES ('compressed',NULL,?,?)", (sid,time.time()))
        db.execute("INSERT INTO messages(session_id,role,content,timestamp) VALUES ('compressed','assistant','AFTER-COMPRESSION',?)", (time.time(),))
        db.commit()
        db.close()
        time.sleep(3.5)
    else:
        time.sleep(30 if text == "long" else 1.2)'''), encoding="utf-8")
    result = runtime.run_hermes("ags_140", "compress", execution_id="e", attempt_id="a", backend="pipe", timeout=15, store=store)
    assert result["session_id"] == "compressed"
    messages = [data["message"] for name, data in events if name == "hermes.message" and data.get("session_id") == "compressed"]
    assert len(messages) == 1
    assert messages[0]["content"] == "AFTER-COMPRESSION"
    assert result["exit_code"] == 0


@pytest.mark.parametrize("stop_session,expected", [("current", "iteration_limit"), ("other-session", "process_error"), (None, "process_error")])
def test_turn_iteration_stop_is_resumable_only_with_matching_receipt(fake_installation, stop_session, expected):
    store, events = fake_installation
    _, source = runtime.hermes_installation()
    main = source / "hermes_cli" / "main.py"
    stop_code = "    import logging\n"
    if stop_session:
        session_expression = "sid" if stop_session == "current" else repr(stop_session)
        stop_code += "    logging.getLogger('agent.conversation_loop').warning('Turn ended: reason=max_iterations_reached(90/90) session=%s', " + session_expression + ")\n"
    stop_code += "    sys.exit(1)"
    main.write_text(main.read_text(encoding="utf-8").replace('    time.sleep(30 if text == "long" else 1.2)', stop_code), encoding="utf-8")
    result = runtime.run_hermes("ags_140", "limit", execution_id="e", attempt_id="a", backend="pipe", timeout=15, store=store)
    assert result["termination_reason"] == expected
    assert result["infra_failure"] is (expected == "iteration_limit")
    attempt = store.get_attempt("a")
    assert attempt["exit_code"] == 1
    assert attempt["status"] == ("interrupted" if expected == "iteration_limit" else "failed")
    assert result["session_id"] == "session-limit"


def test_same_profile_different_sessions_run_in_parallel(fake_installation):
    store, events = fake_installation
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        # Native Python/SDK startup can consume almost ten seconds under load;
        # this checks session isolation, not a ten-second wall-clock budget.
        futures = [pool.submit(runtime.run_hermes, "ags_140", text, execution_id="e-"+text, attempt_id="a-"+text, backend="pipe", timeout=30, store=store) for text in ("one", "two")]
        results = [future.result(timeout=45) for future in futures]
    assert [result["session_id"] for result in results] == ["session-one", "session-two"]
    assert all(result["exit_code"] == 0 for result in results)
    assert [result["output"] for result in results] == ["RESULT-one", "RESULT-two"]
    first, second = [store.get_attempt("a-"+text) for text in ("one", "two")]
    assert max(first["started"], second["started"]) < min(first["ended"], second["ended"])
    assert "\x1b[32m" in store.read_output("a-one")["data"]
    assert all(data.get("attempt_id") for name, data in events)


def test_reasoning_only_resume_does_not_return_previous_turn_answer(fake_installation):
    store, events = fake_installation
    first = runtime.run_hermes("ags_140", "one", execution_id="e", attempt_id="first", backend="pipe", timeout=15, store=store)
    assert first["output"] == "RESULT-one"
    _, source = runtime.hermes_installation()
    main = source / "hermes_cli" / "main.py"
    main.write_text(main.read_text(encoding="utf-8").replace('sid,"RESULT-"+text,time.time()', 'sid,"" if text == "reasoning" else "RESULT-"+text,time.time()'), encoding="utf-8")
    events.clear()
    resumed = runtime.run_hermes("ags_140", "reasoning", session_id=first["session_id"], execution_id="e", attempt_id="second", backend="pipe", timeout=15, store=store)
    assert resumed["exit_code"] == 0
    assert "真实终端-reasoning" in resumed["output"]
    assert "RESULT-one" not in resumed["output"]
    assert all(data["message"]["content"] != "RESULT-one" for name, data in events if name == "hermes.message")


@pytest.mark.skipif(os.name != "nt", reason="Native Windows terminal")
def test_conpty_preserves_ansi_and_real_exit_status(fake_installation):
    store, events = fake_installation
    result = runtime.run_hermes("ags_140", "tty", execution_id="e", attempt_id="a", backend="conpty", timeout=15, store=store)
    assert result["exit_code"] == 0, result
    assert result["session_id"] == "session-tty"
    assert "真实终端-tty" in store.read_output("a")["data"]
    assert store.get_attempt("a")["status"] == "completed"
    phases = {data["phase"]: data["elapsed_seconds"] for name, data in events if name == "terminal.stage"}
    assert phases["terminal_closed"] - phases["process_exited"] < 1.0
    assert not any(thread.name == "adgs-terminal-a" for thread in threading.enumerate())


def test_missing_backend_is_infrastructure_failure(fake_installation):
    store, _ = fake_installation
    result = runtime.run_hermes("p", "x", execution_id="e", attempt_id="a", backend="unknown", store=store)
    assert result["infra_failure"]
    assert result["termination_reason"] == "runtime_error"
    assert store.get_attempt("a")["status"] == "interrupted"


def test_cancel_before_launch_and_foreign_resume_are_explicit(fake_installation):
    store, _ = fake_installation
    cancelled = threading.Event()
    cancelled.set()
    result = runtime.run_hermes("p", "x", execution_id="e", attempt_id="cancel", cancel=cancelled, store=store)
    assert result["termination_reason"] == "cancelled"
    assert result["infra_failure"]
    home = runtime.ROOT / ".hermes" / "profiles" / "p"
    home.mkdir(parents=True)
    with sqlite3.connect(home / "state.db") as conn:
        conn.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO sessions VALUES ('own')")
    result = runtime.run_hermes("p", "x", execution_id="e", attempt_id="foreign", session_id="other-profile-session", store=store)
    assert result["termination_reason"] == "session_missing"
    assert result["session_id"] == "other-profile-session"


def test_classified_skill_lookup_delete_and_complete_inheritance(tmp_path, monkeypatch):
    from core import hermes
    monkeypatch.setattr(hermes, "PROFILES_DIR", tmp_path)
    skill = tmp_path / "parent" / "skills" / "category" / "directory"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: real-name\ndescription: skill\n---\nFull instructions", encoding="utf-8")
    (skill / "scripts" / "helper.py").write_text("print('working')")
    assert "Full instructions" in hermes.HermesInstance.get_skill("parent", "real-name")
    package = hermes.HermesInstance.get_skill_package("parent", "real-name")
    assert hermes.HermesInstance.install_skill("child", "real-name", "skill", content=package) is True
    assert (tmp_path / "child" / "skills" / "real-name" / "scripts" / "helper.py").read_text() == "print('working')"
    assert hermes.HermesInstance.delete_skill("parent", "real-name")
    assert not skill.exists()
    with pytest.raises(ValueError):
        hermes.HermesInstance.get_skill("child", "../../outside")


def test_waiting_provider_heartbeat_is_not_progress():
    assert not runtime._activity_is_progress("waiting for non-streaming API response")
    assert not runtime._activity_is_progress("waiting for stream response (180s, no chunks yet)")
    assert runtime._activity_is_progress("receiving stream response")
    assert runtime._activity_is_progress("tool completed: terminal (2.0s)")
    assert not runtime._activity_is_progress("⏳ waiting on deepseek-flash — no stream output for 60s (provider may be slow)")
    assert not runtime._activity_is_progress("⏳ Retrying in 3.0s (attempt 1/3)...")


def test_waiting_heartbeats_cannot_prevent_inactivity_timeout(fake_installation, monkeypatch):
    store, events = fake_installation
    snapshot = runtime._session_snapshot
    def waiting(home, session_id, cursor, activity_details=None):
        sid, next_cursor, _, messages = snapshot(home, session_id, cursor, activity_details)
        if activity_details is not None:
            activity_details["last_activity_description"] = "waiting for non-streaming API response"
        return sid, next_cursor, time.time(), messages
    monkeypatch.setattr(runtime, "_session_snapshot", waiting)
    result = runtime.run_hermes("ags_140", "long", execution_id="idle", attempt_id="waiting", backend="pipe", timeout=20, inactivity_timeout=2, store=store)
    assert result["termination_reason"] == "inactivity_timeout"
    assert any(data.get("activity_description") == "waiting for non-streaming API response" for name, data in events if name == "terminal.activity")


def test_quiet_computing_child_is_progress_without_total_deadline(fake_installation):
    store, _ = fake_installation
    started = time.monotonic()
    result = runtime.run_hermes("ags_140", "busy", execution_id="computing", attempt_id="busy",
                               backend="pipe", timeout=None, inactivity_timeout=2.5, store=store)
    assert result["exit_code"] == 0, result
    assert time.monotonic() - started > 4


def test_explicit_workspace_overrides_inherited_launch_paths(fake_installation, monkeypatch):
    store, _ = fake_installation
    workspace = runtime.ROOT / "actual-project"
    workspace.mkdir()
    _, source = runtime.hermes_installation()
    main = source / "hermes_cli/main.py"
    body = main.read_text(encoding="utf-8")
    body = body.replace('    text = Path(args[args.index("--query-file") + 1]).read_text(encoding="utf-8")',
        '''    target = Path(args[args.index("--in") + 1])
    assert "--no-restore-cwd" in args
    assert Path.cwd() == target
    assert os.environ["HERMES_CWD"] == os.environ["TERMINAL_CWD"] == str(target)
    assert os.environ["HERMES_DISABLE_CWD_FOLLOW"] == "1"
    text = Path(args[args.index("--query-file") + 1]).read_text(encoding="utf-8")
    assert str(target) in text''')
    main.write_text(body, encoding="utf-8")
    monkeypatch.setenv("HERMES_CWD", str(runtime.ROOT))
    monkeypatch.setenv("TERMINAL_CWD", str(runtime.ROOT))
    result = runtime.run_hermes("ags_140", "workspace check", cwd=str(workspace),
                               execution_id="workspace", attempt_id="workspace", backend="pipe", timeout=15, store=store)
    assert result["exit_code"] == 0, result


def test_waiting_for_children_pauses_inactivity_watchdog(fake_installation, monkeypatch):
    from core import delegation_runtime
    store, _ = fake_installation
    monkeypatch.setattr(delegation_runtime, "execution_is_waiting", lambda _: True)
    result = runtime.run_hermes("ags_140", "long", execution_id="parent-wait",
                               attempt_id="parent-wait", backend="pipe", timeout=3,
                               inactivity_timeout=0.3, store=store)
    assert result["termination_reason"] == "wall_timeout"


def test_session_snapshot_exposes_waiting_activity(tmp_path):
    import sqlite3
    with sqlite3.connect(tmp_path / "state.db") as conn:
        conn.execute("CREATE TABLE sessions(id TEXT, end_reason TEXT, parent_session_id TEXT, last_activity_at REAL, last_activity_description TEXT, last_activity_provenance TEXT)")
        conn.execute("INSERT INTO sessions VALUES ('s',NULL,NULL,123,'waiting for non-streaming API response','unknown')")
        conn.execute("CREATE TABLE messages(id INTEGER, session_id TEXT, role TEXT, content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, timestamp REAL, finish_reason TEXT)")
    details = {}
    sid, cursor, activity, messages = runtime._session_snapshot(tmp_path, "s", 0, details)
    assert (sid, cursor, activity, messages) == ("s", 0, 123, [])
    assert not runtime._activity_is_progress(details["last_activity_description"])


def test_provider_env_sync_preserves_existing_credentials(tmp_path, monkeypatch):
    from core import hermes
    monkeypatch.setattr(hermes, "PROFILES_DIR", tmp_path)
    profile = tmp_path / "p"
    profile.mkdir()
    env = profile / ".env"
    original = "OTHER=value\nOPENCODE_API_KEY=placeholder\nOPENCODE_BASE_URL=http://localhost\n"
    env.write_text(original)
    assert hermes._sync_opencode_env("p", base_url="http://different", api_key="other-placeholder")
    assert env.read_text() == original


def test_existing_profile_migrates_unattended_settings_without_replacing_model(tmp_path, monkeypatch):
    import yaml
    from config.config import config as _cfg
    monkeypatch.setattr(_cfg, "node_merged_brain", False)  # 隔离全局 settings.yaml 的 merged_brain
    from core import hermes
    monkeypatch.setattr(hermes, "PROFILES_DIR", tmp_path / "profiles")
    home = hermes.PROFILES_DIR / "old"
    home.mkdir(parents=True)
    original = {"model": {"provider": "openai", "default": "pinned-old", "api_key": "placeholder"},
                "agent": {"disabled_toolsets": "other"}, "display": {"custom": "keep"}}
    (home / "config.yaml").write_text(yaml.safe_dump(original))
    assert hermes.ensure_profile("old", {"exec_model": "new-model"})
    data = yaml.safe_load((home / "config.yaml").read_text())
    assert data["model"] == original["model"]
    assert data["display"] == original["display"]
    assert data["agent"]["disabled_toolsets"] == ["other", "clarify"]
    assert data["session"]["terminal_continue"] is True
    assert data["approvals"]["single_query_mode"] == "approve"
    backups = list((home / "backups/adgs-runtime").glob("before-trusted-query-*.yaml"))
    assert len(backups) == 1
    assert yaml.safe_load(backups[0].read_text()) == original
    assert hermes._profile_ready("old")


def test_profile_setup_is_atomic_preserves_fields_and_avoids_cli(tmp_path, monkeypatch):
    import yaml
    from config.config import config as _cfg
    monkeypatch.setattr(_cfg, "node_merged_brain", False)  # 隔离全局 settings.yaml 的 merged_brain
    from core import hermes
    monkeypatch.setattr(hermes, "PROFILES_DIR", tmp_path / "profiles")
    plugin = tmp_path / "plugin-source"
    plugin.mkdir()
    (plugin / "plugin.yaml").write_text("name: opencode")
    (plugin / "__init__.py").write_text("")
    monkeypatch.setattr(hermes, "_OPENCODE_PLUGIN_SRC", plugin)
    home = hermes.PROFILES_DIR / "p"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text("display:\n  custom: keep\nagent:\n  disabled_toolsets: [other]\n")
    monkeypatch.setattr(hermes.subprocess, "run", lambda *a, **kw: pytest.fail("No config CLI processes"))
    worker = {"exec_model": "pinned", "base_url": "http://localhost", "api_key": "placeholder", "worker_ctx_window": 50000}
    assert hermes.HermesInstance.setup_newborn("p", {"models": {"hermes_default": worker}})
    config_file = home / "config.yaml"
    data = yaml.safe_load(config_file.read_text())
    assert data["model"]["context_length"] == 50000
    assert data["display"]["custom"] == "keep"
    assert data["agent"]["disabled_toolsets"] == ["other", "clarify"]
    original = config_file.read_bytes()
    assert hermes.ensure_profile("p", {**worker, "exec_model": "newer"})
    assert config_file.read_bytes() == original
    assert hermes.HermesInstance._read_model_default("p") == "pinned"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows Job Object")
@pytest.mark.parametrize("backend", ["pipe", "conpty"])
def test_normal_exit_reaps_background_descendants(fake_installation, backend):
    import win32api
    import win32con
    store, _ = fake_installation
    result = runtime.run_hermes("ags_140", "child", execution_id="e", attempt_id="a", backend=backend, timeout=30, store=store)
    assert result["exit_code"] == 0, result
    for name in ("child.pid", "grandchild.pid"):
        pid = int((store.paths("e", "a") / name).read_text())
        try:
            handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION, False, pid)
        except Exception:
            continue  # OS has already reclaimed the process object.
        try:
            assert win32api.GetExitCodeProcess(handle) != 259
        finally:
            handle.Close()


def test_outer_cancellation_stops_a_running_attempt(fake_installation):
    store, _ = fake_installation
    cancel = threading.Event()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runtime.run_hermes, "ags_140", "long", execution_id="e", attempt_id="a", backend="pipe", timeout=20, cancel=cancel, store=store)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if (store.get_attempt("a") or {}).get("session_id"):
                break
            time.sleep(0.1)
        cancel.set()
        result = future.result(timeout=10)
    assert result["termination_reason"] == "cancelled"
    assert result["infra_failure"]


def test_running_new_session_also_prevents_concurrent_resume(fake_installation):
    store, _ = fake_installation
    cancel = threading.Event()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runtime.run_hermes, "ags_140", "long", execution_id="e", attempt_id="first", backend="pipe", timeout=20, cancel=cancel, store=store)
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if (store.get_attempt("first") or {}).get("session_id"):
                    break
                time.sleep(0.1)
            second = runtime.run_hermes("ags_140", "again", execution_id="e", attempt_id="second", backend="pipe", session_id="session-long", store=store)
            assert second["termination_reason"] == "session_busy"
        finally:
            cancel.set()
            future.result(timeout=10)
