import json
import sqlite3
import sys
from types import ModuleType, SimpleNamespace

from core.ceo_policy import coordinator_guidance, sync_ceo_role


def test_large_saved_ledger_resumes_on_smaller_model_without_losing_execution_facts(tmp_path, monkeypatch):
    import core.hermes as hermes
    import core.terminal_runtime as runtime
    from core.ceo import CEO, config
    from core.compress import TASK_FACTS_MARKER, _count_tokens

    monkeypatch.setattr(config, "ceo_ctx_window", 262144)
    monkeypatch.setattr(config, "compress_budget", 0.25)
    profile = tmp_path / "ceo"
    profile.mkdir()
    monkeypatch.setattr(hermes, "HermesInstance", lambda name: SimpleNamespace(profile_dir=profile))
    monkeypatch.setattr(hermes, "_configure_profile", lambda *a, **kw: True)
    monkeypatch.setattr(runtime, "terminal_store", SimpleNamespace(paths=lambda *a: tmp_path))
    monkeypatch.setenv("ADGS_HERMES_RESIDENT", "1")
    with sqlite3.connect(profile / "state.db") as db:
        db.execute("CREATE TABLE sessions (id TEXT, model TEXT)")
        db.execute("CREATE TABLE messages (session_id TEXT, content TEXT, tool_calls TEXT)")
        db.execute("INSERT INTO sessions VALUES ('old-native', 'deepseek-flash')")
        db.execute("INSERT INTO messages VALUES ('old-native', ?, NULL)", ("旧" * 300000,))

    ceo = CEO()
    ceo.chat.reconfigure(url="http://fixture.invalid", model="qwen-fixture", key="fixture")
    ceo._run_id = "run-original"
    ceo._hermes_session_id = "old-native"
    ceo._hermes_execution_id = "ceo-original"
    ceo.history.extend([{"role": "user", "content": "旧任务说明" * 75000},
                        {"role": "user", "content": "继续原任务"}])
    ledger = ceo._facts_for()
    for i in range(118):
        ledger["tasks"][f"execution-{i}"] = {
            "task_id": f"task-{i}", "run_id": ceo._run_id, "outcome": "interrupted",
            "goal": f"原目标 {i}", "evaluation": {"detail": "验收细节" * 1200},
        }
    messages = ceo._model_messages()
    view = json.loads(messages[0]["content"].split(TASK_FACTS_MARKER)[1])["facts"]
    assert view["tasks"].keys() == ledger["tasks"].keys()
    assert _count_tokens(messages[:1]) < 32768

    def run(profile, query, **kwargs):
        assert kwargs["session_id"] is None
        assert kwargs["execution_id"] == "ceo-original"
        assert kwargs["model"] == "qwen-fixture"
        assert "继续原任务" in query and "execution-117" in query
        assert _count_tokens([{"role": "user", "content": query}]) < 65536
        decision = {"name": "report", "arguments": {"summary": "ready"}}
        (tmp_path / "ceo-decision.json").write_text(json.dumps(decision), encoding="utf-8")
        return {"exit_code": 0, "session_id": "new-native", "control_decision": decision}

    monkeypatch.setattr(runtime, "run_hermes", run)
    result = ceo.chat.call_tools(messages, [{"function": {"name": "report"}}])
    assert result.get("tool_calls"), result
    assert ceo._hermes_session_id == "new-native"
    assert len(ceo.history[1]["content"]) == len("旧任务说明" * 75000)
    assert "验收细节" in ledger["tasks"]["execution-117"]["evaluation"]["detail"]


def test_ceo_role_sync_preserves_previous_persona_and_worker(tmp_path):
    ceo = tmp_path / "ceo"
    worker = tmp_path / "worker"
    ceo.mkdir()
    worker.mkdir()
    (ceo / "SOUL.md").write_text("generic agent", encoding="utf-8")
    (worker / "SOUL.md").write_text("worker persona", encoding="utf-8")
    sync_ceo_role(ceo)
    assert "固定 CEO" in (ceo / "SOUL.md").read_text(encoding="utf-8")
    assert (ceo / "skills/adgs-ceo/SKILL.md").is_file()
    assert list((ceo / "backups/adgs-role").iterdir())[0].read_text() == "generic agent"
    sync_ceo_role(ceo)
    assert len(list((ceo / "backups/adgs-role").iterdir())) == 1
    assert (worker / "SOUL.md").read_text() == "worker persona"
    assert "single_task" in coordinator_guidance()


def test_ceo_native_tools_cannot_execute_code_or_write_files(tmp_path, monkeypatch):
    from core.hermes_roles import install_role_tools
    schemas = tmp_path / "schemas.json"
    schemas.write_text(json.dumps([{"function": {"name": n, "parameters": {"type": "object"}}}
                                   for n in ("read_file", "split_tasks", "continue_task")]))
    monkeypatch.setenv("ADGS_CONTROL_TOOLS", str(schemas))
    monkeypatch.setenv("ADGS_CONTROL_DECISION", str(tmp_path / "receipt.json"))
    module = ModuleType("model_tools")
    agent = ModuleType("agent")
    invocations = []
    executor = SimpleNamespace(_dispatch_authorized_once=lambda *a, **kw: invocations.append(kw))
    agent.tool_executor = executor
    registry = ModuleType("tools.registry")
    registry.registry = SimpleNamespace(register=lambda *a, **kw: None)
    for name, value in (("model_tools", module), ("agent", agent), ("tools.registry", registry)):
        monkeypatch.setitem(sys.modules, name, value)
    install_role_tools()
    for name in ("terminal", "write_file", "delegate_task", "browser", "skill_manage"):
        executor._dispatch_authorized_once(None, None, SimpleNamespace(name=name), scope_block=None)
        assert "协调" in invocations[-1]["scope_block"]
    for name in ("read_file", "split_tasks", "continue_task"):
        executor._dispatch_authorized_once(None, None, SimpleNamespace(name=name), scope_block=None)
        assert invocations[-1]["scope_block"] is None


def test_activity_is_persistent_per_chat_and_does_not_create_work(tmp_path, monkeypatch):
    import core.ceo as module
    monkeypatch.setattr(module.config, "DATA_DIR", tmp_path)
    ceo = module.CEO()
    ceo.activate_session("coordinator-a")
    ceo._hermes_execution_id = "ceo-a"
    ceo._set_activity("waiting_children")
    ceo.save_conversation("coordinator-a")
    restored = module.CEO()
    restored.load_conversation("coordinator-a")
    assert restored._ceo_activity["phase"] == "waiting_children"
    assert restored._ceo_activity["execution_id"] == "ceo-a"
    restored._active = "coordinator-b"
    assert restored._ceo_activity is None
    assert not restored._facts_for()["tasks"]


def test_normal_hermes_prose_enters_bounded_ceo_protocol_repair(tmp_path, monkeypatch):
    import core.hermes as hermes
    import core.terminal_runtime as runtime
    from core.hermes_ceo import HermesCEOChat
    profile = tmp_path / "ceo"
    profile.mkdir()
    monkeypatch.setattr(hermes, "HermesInstance", lambda name: SimpleNamespace(profile_dir=profile))
    monkeypatch.setattr(hermes, "_configure_profile", lambda *a, **kw: True)
    monkeypatch.setattr(runtime, "terminal_store", SimpleNamespace(paths=lambda *a: tmp_path))
    monkeypatch.setattr(runtime, "run_hermes", lambda *a, **kw: {
        "exit_code": 0, "output": "现在续接 B 负责人", "session_id": "original-session"})
    owner = SimpleNamespace(_hermes_input_count=0, _hermes_input_hash="", _hermes_session_id="original-session",
                            _hermes_execution_id="ceo-original", _run_id="run-original",
                            _set_activity=lambda *a, **kw: None)
    chat = HermesCEOChat(owner, url="http://fixture.invalid", model="fixture", key="fixture")
    result = chat.call_tools([{"role":"user", "content":"continue"}], [])
    assert result["error_kind"] == "protocol_error"
    assert result["content"] == "现在续接 B 负责人"
    assert owner._hermes_input_count == 1
    assert owner._hermes_session_id == "original-session"
