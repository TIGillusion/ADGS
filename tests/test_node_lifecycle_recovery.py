"""Resource claims and optional maintenance must not poison resumable work."""
import queue
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.execution_store import ExecutionStore
from core.node import NODE


@pytest.fixture
def node_runtime(tmp_path, monkeypatch):
    import core.node as runtime
    node = object.__new__(NODE)
    node.node_id = node.profile = "fixture"
    node.reg = SimpleNamespace(get=lambda _: SimpleNamespace(score=50))
    node.agent = SimpleNamespace(history=[])
    node._compute_timeouts = lambda *a: (30, 20)
    store = ExecutionStore(tmp_path / "executions.sqlite")
    monkeypatch.setattr(runtime, "execution_store", store)
    monkeypatch.setattr(runtime, "TASK_DIR", tmp_path / "contracts")
    monkeypatch.setattr(runtime, "_ACTIVE_EXECUTIONS", {})
    monkeypatch.setattr(runtime, "_MAINTENANCE_QUEUE", queue.Queue())
    monkeypatch.setattr(runtime, "_MAINTENANCE_STARTED", False)
    monkeypatch.setattr(runtime.bus, "emit", lambda *a, **kw: None)
    return runtime, node, store


@pytest.mark.parametrize("failure", ["budget", "checkpoint"])
def test_prepare_failure_releases_execution_claim_for_same_id_retry(node_runtime, monkeypatch, failure):
    runtime, node, store = node_runtime
    original_put = store.put
    if failure == "budget":
        node._compute_timeouts = Mock(side_effect=ValueError("bad budget"))
    else:
        monkeypatch.setattr(store, "put", Mock(side_effect=OSError("checkpoint unavailable")))
    with pytest.raises((OSError, ValueError)):
        node._prepare_execution("contract", "task", "same-id", workspace="fixture")
    assert "same-id" not in runtime._ACTIVE_EXECUTIONS
    node._compute_timeouts = lambda *a: (30, 20)
    monkeypatch.setattr(store, "put", original_put)
    node._prepare_execution("contract", "task", "same-id", workspace="fixture")
    assert store.get("same-id")["state"] == "running"


def test_duplicate_prepare_does_not_release_actual_owner(node_runtime):
    runtime, node, _ = node_runtime
    owner = object()
    runtime._ACTIVE_EXECUTIONS["same-id"] = owner
    with pytest.raises(RuntimeError, match="已在运行"):
        node._prepare_execution("contract", "task", "same-id", workspace="fixture")
    assert runtime._ACTIVE_EXECUTIONS["same-id"] is owner


@pytest.mark.parametrize("phase", ["correction", "rework"])
def test_all_resume_paths_reference_complete_contract_without_reinjecting_it(node_runtime, monkeypatch, phase):
    runtime, node, store = node_runtime
    contract = "原始权限与验收\n" + "historical requirement\n" * 10000
    node._prepare_execution(contract, "task", "e", workspace="fixture")
    node.agent = SimpleNamespace(append_history=Mock(), compress_history_if_needed=Mock(),
        history_brief=lambda: "brief", evaluate=Mock(side_effect=
            ([{"passed": False, "failed_items": ["missing output"]}] if phase == "rework" else [])
            + [{"passed": True}]))
    node._execution_evidence = lambda *a: "evidence"
    node._emit_terminal_status = Mock()
    node._resume_hermes = Mock(return_value={"exit_code": 0, "session_id": "same", "output": "done"})
    monkeypatch.setattr(runtime.config, "correction_limit", 1)
    monkeypatch.setattr(runtime.config, "rework_limit", 1)
    result = node._complete_execution(contract, {"exit_code": 1 if phase == "correction" else 0,
        "session_id": "same", "error": "local script failed"}, "ceo", False)
    assert result["success"]
    query, sid, _ = node._resume_hermes.call_args.args
    assert sid == "same" and len(query) < 2500 and "historical requirement" not in query
    from pathlib import Path
    assert Path(store.get("e")["contract_path"]).read_text(encoding="utf-8") == contract
    assert node.agent.evaluate.call_args.args[0] == contract


@pytest.mark.parametrize("legacy", [False, True])
def test_failed_leader_before_worker_resumes_same_execution_instead_of_new_session(node_runtime, monkeypatch, legacy):
    runtime, node, store = node_runtime
    from core.terminal_runtime import TerminalStore
    monkeypatch.setattr("core.terminal_runtime.terminal_store", TerminalStore(store.path.parent / "terminals"))
    saved = dict(node_id="fixture", profile="fixture", task_id="task", run_id="run", task_md="original permission",
        state="infra_failure", phase="finished", workspace="original-ws", session_name="original-chat",
        result={"outcome": "infra_failure", "error": "provider unavailable"})
    if not legacy:
        saved["decision_pending"] = True
    store.put("same-e", **saved)
    node._work_inner = Mock(return_value={"success": False, "outcome": "infra_failure"})
    node._resume_hermes = Mock(side_effect=AssertionError("No worker session exists"))
    node.continue_execution("same-e", "retry after provider recovery")
    args = node._work_inner.call_args.args
    assert args[-1] == "task" and "original permission" in args[0] and "retry after" in args[0]
    assert node._execution_id == "same-e" and node._workspace == "original-ws"
    assert store.get("same-e")["run_id"] == "run"
    assert not node._resume_hermes.called


def test_missing_session_after_actual_attempt_is_not_guessed(node_runtime, monkeypatch):
    runtime, node, store = node_runtime
    from core.terminal_runtime import TerminalStore
    terminals = TerminalStore(store.path.parent / "terminals")
    monkeypatch.setattr("core.terminal_runtime.terminal_store", terminals)
    store.put("started", node_id="fixture", profile="fixture", task_id="task", task_md="contract",
        state="interrupted", decision_pending=True)
    terminals.create(execution_id="started", attempt_id="real-attempt", profile="fixture")
    node._work_inner = Mock(side_effect=AssertionError("Cannot restart a real worker with unknown session"))
    result = node.continue_execution("started", "continue")
    assert result["outcome"] == "protocol_error" and not node._work_inner.called


def test_optional_maintenance_launch_failure_preserves_passed_result_and_can_restart(node_runtime, monkeypatch):
    runtime, node, store = node_runtime
    node._prepare_execution("contract", "task", "passed-id", workspace="fixture")
    node.evolve_describe = lambda *a: None
    def fail_start():
        raise RuntimeError("cannot start maintenance thread")
    monkeypatch.setattr(runtime.threading, "Thread", lambda **kw: SimpleNamespace(start=fail_start))
    result = node._save_result({"success": True, "evaluation": {"passed": True},
                               "session_id": "hermes-session"})
    assert result["outcome"] == "passed"
    assert store.get("passed-id")["state"] == "passed"
    assert store.get("passed-id")["maintenance_state"] == "pending"
    assert not runtime._MAINTENANCE_STARTED
    queued = runtime._MAINTENANCE_QUEUE.qsize()
    started = []
    monkeypatch.setattr(runtime.threading, "Thread", lambda **kw:
        SimpleNamespace(start=lambda: started.append(kw["target"])))
    node._schedule_maintenance({"evaluation": {"passed": True}})
    assert len(started) == 1 and runtime._MAINTENANCE_STARTED
    assert runtime._MAINTENANCE_QUEUE.qsize() == queued + 1


def test_resume_sends_delta_and_full_contract_reference_without_losing_history(node_runtime):
    from pathlib import Path
    import hashlib
    _, node, store = node_runtime
    node._prepare_execution("base", "task", "resume-id", workspace="fixture")
    full = "原始权限和验收条件\n" + "历史补充\n" * 20000 + "最近的旧补充"
    prompt = node._continuation_instruction(full, "新补充：只修单位三角 fixture")
    saved = store.get("resume-id")
    path = Path(saved["contract_path"])
    assert len(prompt) < 1000 and "新补充：只修单位三角 fixture" in prompt
    assert str(path) in prompt and path.read_text(encoding="utf-8") == full
    assert saved["contract_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    node._continuation_instruction(full + "下一条补充", "下一步")
    assert Path(store.get("resume-id")["contract_path"]) != path
    assert path.read_text(encoding="utf-8") == full  # Previous evidence is immutable.
