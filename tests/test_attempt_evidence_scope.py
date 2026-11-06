"""A resumed attempt must not inherit an older tool process's termination."""
import sqlite3
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock

from core.node import NODE


def test_attempt_evidence_excludes_old_kill_and_old_final(tmp_path, monkeypatch):
    import core.terminal_runtime as terminals
    store = Mock()
    store.get_attempt.return_value = {"started": 100.0}
    monkeypatch.setattr(terminals, "terminal_store", store)
    with sqlite3.connect(tmp_path / "state.db") as conn:
        conn.execute("CREATE TABLE messages (id INTEGER, session_id TEXT, role TEXT, content TEXT, timestamp REAL)")
        conn.executemany("INSERT INTO messages VALUES (?, ?, ?, ?, ?)", [
            (1, "same", "tool", "OLD_TOOL_KILLED", 50),
            (2, "same", "assistant", "OLD_ALL_DELIVERABLES_DONE", 80),
            (3, "same", "assistant", "CURRENT_PLAN_ONLY", 110),
            (4, "other", "tool", "OTHER_SESSION_KILLED", 120),
        ])
    node = NODE.__new__(NODE)
    node.hermes = SimpleNamespace(profile_dir=tmp_path)
    node._workspace = str(tmp_path)
    evidence = node._execution_evidence("original task", {
        "attempt_id": "current", "session_id": "same", "output": "current planning",
        "exit_code": 0, "termination_reason": "completed", "error": "",
    })
    assert "CURRENT_PLAN_ONLY" in evidence
    assert not any(old in evidence for old in ("OLD_TOOL_KILLED", "OLD_ALL_DELIVERABLES_DONE", "OTHER_SESSION_KILLED"))
    assert '"exit_code": 0' in evidence and '"termination_reason": "completed"' in evidence
    assert "本轮会话消息" in evidence
    store.get_attempt.assert_called_once_with("current")


def test_legacy_log_labels_unscoped_messages_as_history(tmp_path):
    with sqlite3.connect(tmp_path / "state.db") as conn:
        conn.execute("CREATE TABLE messages (id INTEGER, session_id TEXT, role TEXT, content TEXT)")
        conn.execute("INSERT INTO messages VALUES (1, 'same', 'tool', 'historical tool result')")
    node = NODE.__new__(NODE)
    node.hermes = SimpleNamespace(profile_dir=tmp_path)
    node._workspace = str(tmp_path)
    evidence = node._execution_evidence("task", {"session_id": "same", "exit_code": 0})
    assert "historical tool result" in evidence
    assert "未限定 attempt，需核对发生时间" in evidence


def test_current_selftest_json_is_visible_before_old_success_report(tmp_path, monkeypatch):
    import core.terminal_runtime as terminals
    store = Mock()
    store.get_attempt.return_value = {"started": 100.0}
    monkeypatch.setattr(terminals, "terminal_store", store)
    report = tmp_path / "report.md"
    report.write_text("ALL CHANNELS DONE", encoding="utf-8")
    os.utime(report, (80, 80))
    result = tmp_path / "result.json"
    result.write_text(json.dumps({"mode": "selftest", "stages": {"channels": "pending"}, "channels": None}), encoding="utf-8")
    os.utime(result, (110, 110))
    node = NODE.__new__(NODE)
    node.hermes = SimpleNamespace()
    node._workspace = str(tmp_path)
    evidence = node._execution_evidence(f"{report.as_posix()}\n{result.as_posix()}", {"attempt_id": "current", "exit_code": 0})
    assert '"channels": null' in evidence and '"channels": "pending"' in evidence
    assert '"mode": "selftest"' in evidence
    assert "早于本轮；仅能证明历史成果" in evidence
    assert evidence.index("当前 JSON 产物") < evidence.index("ALL CHANNELS DONE")


def test_structured_artifact_summary_counts_verdicts_without_dumping_mesh_data(tmp_path):
    result = tmp_path / "result.json"
    result.write_text(json.dumps({"channels": {"canals": [{"verdict": "unverified", "vertices": list(range(10000))}, {"verdict": "through"}]}}), encoding="utf-8")
    outside = tmp_path.parent / "outside-evidence.json"
    outside.write_text('{"private": "OUTSIDE_MUST_NOT_APPEAR"}', encoding="utf-8")
    node = NODE.__new__(NODE)
    node.hermes = SimpleNamespace()
    node._workspace = str(tmp_path)
    evidence = node._execution_evidence(f"{result.as_posix()}\n{outside.as_posix()}", {"exit_code": 0})
    assert '"length": 2' in evidence
    assert '"unverified": 1' in evidence and '"through": 1' in evidence
    assert "9999" not in evidence and "OUTSIDE_MUST_NOT_APPEAR" not in evidence
