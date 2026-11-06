"""Regression cases for false failures, durable inspection and concurrent AGS work.

All writes use temporary stores. No live model, profile or business task is run.
"""
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.agent import AGENT
from core.contracts import validate_arguments
from core.execution_store import ExecutionStore
from core.node import NODE, DynamicSemaphore
from core.registry import Registry, Node
from core.scheduling import dependency_results, validate_dependencies
from core.terminal_runtime import TerminalStore
from web.event_bus import EventBus, event_scope


def report(passed=True, score=10):
    return {"tool_calls": [{"name": "accept_report", "arguments": {
        "passed": passed, "score": score, "passed_items": ["tests passed"],
        "failed_items": [] if passed else [{"criterion": "output", "why": "missing"}]}}]}


def test_bad_evaluation_report_repairs_only_report():
    agent = AGENT("http://fixture.invalid", "fixture", "fixture")
    agent.chat.call_tools = Mock(side_effect=[{"tool_calls": []}, report()])
    result = agent.evaluate("original criteria", "complete tests and final report", old_score=50)
    assert result["passed"] is True
    assert result["passed_items"] == ["tests passed"]
    assert result["raw_score"] == 10
    assert agent.chat.call_tools.call_count == 2
    correction = agent.chat.call_tools.call_args.args[0][-1]["content"]
    assert "不要求重做成果" in correction


@pytest.mark.parametrize("bad", ["false", 0, None, float("nan")])
def test_boolean_and_finite_contracts_reject_coercion(bad):
    assert validate_arguments(bad, {"type": "boolean"})
    assert validate_arguments(float("inf"), {"type": "number"})


def test_failed_report_is_unjudged_and_preserves_score():
    agent = AGENT("http://fixture.invalid", "fixture", "fixture")
    agent.chat.call_tools = Mock(return_value={"tool_calls": []})
    result = agent.evaluate("criteria", "complete", old_score=72)
    assert result["outcome"] == "protocol_error"
    assert result["unjudged"] and result["score"] == 72
    assert agent.chat.call_tools.call_count == 2


def test_acceptance_retains_original_contract_and_final_tail():
    agent = AGENT("http://fixture.invalid", "fixture", "fixture")
    agent.chat.call_tools = Mock(return_value=report())
    task = "DO NOT MODIFY shared.py; required A B C"
    agent.evaluate(task, "old failed attempts\n" * 40000 + "FINAL: all A B C verified", old_score=50)
    context = agent.chat.call_tools.call_args.args[0][1]["content"]
    assert task in context and "FINAL: all A B C verified" in context


def test_oversized_acceptance_contract_is_reviewed_in_chunks(monkeypatch):
    from config.config import config
    agent = AGENT("http://fixture.invalid", "fixture", "fixture")
    monkeypatch.setattr(config, "brain_ctx_window", 8192)
    agent.chat.call_tools = Mock(return_value=report())
    contract = "\n".join(f"## criterion-{i}\nrequired evidence {i}" for i in range(700))
    result = agent.evaluate(contract, "fixture evidence", old_score=50)
    assert result["passed"] is True
    assert result["outcome"] == "passed"
    assert len(result["chunks"]) > 1
    assert agent.chat.call_tools.call_count == len(result["chunks"])
    assert sum(len(chunk) for chunk in agent._acceptance_chunks(contract)) == len(contract)


def test_parallel_credits_are_atomic_and_idempotent_after_reload(tmp_path):
    reg = Registry(tmp_path / "registry.json")
    reg.add(Node("ags_fixture", "test", score=50))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: reg.record_result("ags_fixture", True, 2, f"execution-{i}"), range(20)))
    node = reg.get("ags_fixture")
    assert node.score == 90 and node.tasks_done == 20
    restored = Registry(reg.path)
    restored.record_result("ags_fixture", False, -5, "execution-1")
    assert restored.get("ags_fixture").score == 90
    assert restored.get("ags_fixture").tasks_done == 20
    assert node.success_rate > .99


def test_repaired_execution_replaces_credit_without_double_count(tmp_path):
    reg = Registry(tmp_path / "registry.json")
    reg.add(Node("a", "fixture", score=50))
    reg.record_result("a", False, -2, "repair", alpha=.2)
    reg.record_result("a", True, 2, "other", alpha=.2)
    assert reg.get("a").score == 50
    reg.record_result("a", True, 2, "repair", alpha=.2, revise=True)
    assert reg.get("a").score == 54
    assert reg.get("a").tasks_done == 2
    assert reg.get("a").success_rate == .68
    reg.record_result("a", True, 2, "repair", alpha=.2, revise=True)
    assert reg.get("a").score == 54 and reg.get("a").tasks_done == 2


def test_revised_credit_respects_zero_score_floor(tmp_path):
    reg = Registry(tmp_path / "registry.json")
    reg.add(Node("a", "fixture", score=0))
    reg.record_result("a", False, -1, "same")
    assert reg.get("a").score == 0
    reg.record_result("a", True, 1, "same", revise=True)
    assert reg.get("a").score == 1
    reg.record_result("a", True, 1, "same", revise=True)
    assert reg.get("a").score == 1 and reg.get("a").tasks_done == 1


def test_explicit_timeout_is_not_expanded_by_long_task_words():
    node = object.__new__(NODE)
    timeout, _ = node._compute_timeouts("写50万字长篇文档", 15)
    assert timeout == 15


def test_parent_cancellation_reaches_descendants_without_reverse_cancel():
    from core.node import _Cancellation
    parent = _Cancellation()
    child = _Cancellation(parent)
    sibling = _Cancellation(parent)
    child.set()
    assert child.is_set() and not parent.is_set() and not sibling.is_set()
    parent.set()
    assert sibling.is_set()


def test_pass_with_advisory_failed_items_is_accepted_in_one_call():
    """passed=True + failed_items = 总体通过、带非阻断瑕疵——一次通过，不再打回
    （2026-10-24 T13 实测：验收官给"通过但扣分"被旧规则误判协议失败）。"""
    agent = AGENT("http://fixture.invalid", "fixture", "fixture")
    advisory = report()
    advisory["tool_calls"][0]["arguments"]["failed_items"] = [
        {"criterion": "报告附原始JSON", "why": "非阻断瑕疵，证据可追溯性略降"}]
    agent.chat.call_tools = Mock(return_value=advisory)
    result = agent.evaluate("original", "evidence", old_score=50, timeout=5)
    assert result["passed"] and agent.chat.call_tools.call_count == 1
    assert result["failed_items"]  # 瑕疵保留在结果里供上级参考


def test_fail_without_failed_items_repairs_report():
    agent = AGENT("http://fixture.invalid", "fixture", "fixture")
    invalid = report()
    invalid["tool_calls"][0]["arguments"]["passed"] = False
    invalid["tool_calls"][0]["arguments"]["failed_items"] = []
    agent.chat.call_tools = Mock(side_effect=[invalid, report()])
    result = agent.evaluate("original", "evidence", old_score=50, timeout=5)
    assert result["passed"] and agent.chat.call_tools.call_count == 2
    assert agent.chat.call_tools.call_args.kwargs["timeout"] <= 5


def test_leaf_continuation_retains_new_acceptance_contract(tmp_path, monkeypatch):
    import core.node as nodes
    store = ExecutionStore(tmp_path / "executions.sqlite")
    monkeypatch.setattr(nodes, "execution_store", store)
    monkeypatch.setattr(nodes, "TASK_DIR", tmp_path / "contracts")
    reg = Registry(tmp_path / "registry.json")
    reg.add(Node("worker", "fixture"))
    node = object.__new__(NODE)
    node.node_id = node.profile = "worker"
    node.reg = reg
    node.agent = SimpleNamespace(history=[])
    node._schedule_maintenance = lambda *a: None
    original = "只允许修改 mesh.js；边界边必须为 0\n" + "已保留的历史要求\n" * 4000
    store.put("leaf", node_id="worker", profile="worker", task_id="leaf-task",
              task_md=original, session_id="original-session")
    node._resume_hermes = Mock(return_value={"output": "existing closure report"})
    def accept(task_md, log, upper, record_score):
        # An old closure-only report cannot satisfy the new orientation contract.
        passed = "inconsistentDirectedEdges" not in task_md
        return {"success": passed, "task_md": task_md, "session_id": "original-session",
                "evaluation": {"passed": passed}}
    node._complete_execution = Mock(side_effect=accept)
    extra = "inconsistentDirectedEdges 必须为 0"
    result = node.continue_execution("leaf", extra)
    assert not result["success"]
    revised = store.get("leaf")["task_md"]
    assert original in revised and extra in revised
    prompt, session = node._resume_hermes.call_args.args
    assert session == "original-session" and extra in prompt
    assert len(prompt) < 1000
    from pathlib import Path
    assert Path(store.get("leaf")["contract_path"]).read_text(encoding="utf-8") == revised
    assert store.get("leaf")["contract_path"] in prompt
    assert node._complete_execution.call_args.args[0] == revised
    node.continue_execution("leaf", "还要提供独立复验报告")
    latest = store.get("leaf")["task_md"]
    assert all(text in latest for text in (original, extra, "还要提供独立复验报告"))
    assert node._complete_execution.call_args.args[0] == latest


def test_delegation_continuation_preserves_passed_children(tmp_path, monkeypatch):
    import core.node as nodes
    from core.execution_store import ExecutionStore
    store = ExecutionStore(tmp_path / "executions.sqlite")
    monkeypatch.setattr(nodes, "execution_store", store)
    reg = Registry(tmp_path / "registry.json")
    for name in ("parent", "finished", "unfinished"):
        reg.add(Node(name, "fixture"))
    parent = object.__new__(NODE)
    parent.node_id = parent.profile = "parent"
    parent.reg = reg
    parent.agent = SimpleNamespace(history=[])
    parent._schedule_maintenance = lambda *a: None
    parent._evaluate_delegation_and_update = Mock(wraps=parent._evaluate_delegation_and_update)
    children = [{"success": True, "node_id": "finished", "execution_id": "good",
                 "task_id": "task-good", "task_md": "finished contract", "evaluation": {"passed": True, "raw_score": 10}},
                {"success": False, "node_id": "unfinished", "execution_id": "bad",
                 "task_id": "task-bad", "task_md": "original contract"}]
    store.put("parent-exec", node_id="parent", profile="parent", task_id="parent-task", task_md="parent contract",
              result={"action": "split_tasks", "children": children,
                      "subtasks": [{"name": "finished", "permissions": "read only"},
                                   {"name": "unfinished", "permissions": "read only", "depends_on": [0]}]})
    store.put("bad", node_id="unfinished", profile="unfinished", task_id="task-bad", task_md="original contract",
              session_id="same-original-session")
    calls = []
    class Child:
        def __init__(self, name, **kwargs):
            assert name == "unfinished", "completed children must not rerun"
        def continue_execution(self, execution_id, instruction, **kwargs):
            calls.append((execution_id, instruction, kwargs))
            return {"success": True, "outcome": "passed", "execution_id": execution_id,
                    "evaluation": {"passed": True, "raw_score": 10}}
    monkeypatch.setattr(nodes, "NODE", Child)
    result = parent.continue_execution("parent-exec", "只补最后一步")
    assert result["success"] and len(calls) == 1 and calls[0][0] == "bad"
    assert result["children"][0]["execution_id"] == "good"
    assert store.get("parent-exec")["state"] == "passed"
    assert reg.get("parent").tasks_done == 1
    assert "只补最后一步" in parent._evaluate_delegation_and_update.call_args.args[1]
    # A leaf completed separately; stale parent snapshots must not execute it again.
    store.put("parent-exec", result={"action": "split_tasks", "children": children,
          "subtasks": [{"name": "finished", "permissions": "read only"},
                       {"name": "unfinished", "permissions": "read only", "depends_on": [0]}]})
    store.put("bad", result={"success": True, "evaluation": {"passed": True, "raw_score": 10}})
    assert parent.continue_execution("parent-exec", "聚合最新成果")["success"]
    assert len(calls) == 1
    assert reg.get("parent").tasks_done == 1
    contract = parent._evaluate_delegation_and_update.call_args.args[1]
    assert all(text in contract for text in ("parent contract", "只补最后一步", "聚合最新成果"))


def test_parent_acceptance_checks_original_contract_even_if_all_children_pass(tmp_path):
    reg = Registry(tmp_path / "registry.json")
    reg.add(Node("a", "fixture", score=50))
    node = object.__new__(NODE)
    node.reg, node.node_id, node.profile = reg, "a", "a"
    node._score_base, node._execution_id, node._deadline = 50, "parent-e", time.monotonic() + 10
    node.agent = AGENT("http://fixture.invalid", "fixture", "fixture")
    node.agent.evaluate = Mock(return_value={"passed": False, "raw_score": -2, "score": 49.6,
          "failed_items": [{"criterion": "integration", "why": "missing"}], "passed_items": []})
    result = node._evaluate_delegation_and_update([{"success": True, "name": "child", "evaluation": {
                "passed": True, "raw_score": 10}}], "ORIGINAL integration requirement")
    assert not result["passed"] and reg.get("a").score == 49.6
    assert node.agent.evaluate.call_args.args[0] == "ORIGINAL integration requirement"


def test_loading_running_session_uses_live_history_and_graph_events(tmp_path, monkeypatch):
    import web_app
    from config.config import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    directory = tmp_path / "sessions"
    directory.mkdir()
    (directory / "live.json").write_text(json.dumps({"history": [{"role": "user", "content": "disk"}], "graph": []}), encoding="utf-8")
    ceo = SimpleNamespace(_talk_session="live", _histories={"live": [{"role": "user", "content": "latest"}]},
                          load_conversation=lambda name: "preserved")
    monkeypatch.setattr(web_app, "get_ceo", lambda: ceo)
    monkeypatch.setitem(web_app._busy, "state", True)
    event = {"id": 1, "session": "live", "type": "graph.dispatch", "data": {"execution_id": "e"}}
    monkeypatch.setattr(web_app.bus, "history", lambda tail: [event])
    result = web_app.app.test_client().post("/api/session/load", json={"name": "live"}).get_json()
    assert result["messages"][0]["text"] == "latest" and result["events"] == [event]


def test_web_runner_uses_real_readonly_workspace_property(tmp_path, monkeypatch):
    import web_app
    from config.config import config
    seen = []
    ceo = SimpleNamespace(_states={"runner-fixture": {"_task_done": True}}, _histories={},
                         activate_session=lambda name: None, talk=lambda text: seen.append(config.workspace),
                         mark_interrupted=lambda *a: pytest.fail("runner unexpectedly interrupted"))
    monkeypatch.setattr(web_app, "get_ceo", lambda: ceo)
    monkeypatch.setattr(web_app, "registry", SimpleNamespace(count=lambda: 1))
    monkeypatch.setattr(web_app, "_save_current_session", lambda *a, **kw: None)
    monkeypatch.setattr(web_app, "_queue", [])
    monkeypatch.setitem(web_app._busy, "state", False)
    old_workspace = config.workspace
    result = web_app._start_task("fixture", "runner-fixture", str(tmp_path))
    deadline = time.monotonic() + 2
    while web_app._busy["state"] and time.monotonic() < deadline:
        time.sleep(.01)
    assert result["ok"] and seen == [str(tmp_path)]
    assert not web_app._busy["state"] and config.workspace == old_workspace


def test_web_preparation_failure_preserves_pending_queue(monkeypatch):
    import web_app
    pending = [{"text": "pending", "session": "queued-fixture", "workspace": "fixture"}]
    monkeypatch.setattr(web_app, "_queue", pending.copy())
    monkeypatch.setitem(web_app._busy, "state", False)
    monkeypatch.setattr(web_app, "_evolve_lock", SimpleNamespace(acquire=lambda **kw: False))
    monkeypatch.setattr(web_app, "get_ceo", lambda: pytest.fail("preparation failed before CEO activation"))
    runs = []

    class ImmediateThread:
        def __init__(self, target, **kw):
            self.target = target

        def start(self):
            runs.append(1)
            self.target()

    monkeypatch.setattr(web_app.threading, "Thread", ImmediateThread)
    web_app._start_task("first", "preparation-fixture", "fixture")
    assert len(runs) == 1
    assert web_app._queue == pending
    assert not web_app._busy["state"]


def test_web_runner_exception_preserves_fifo_with_real_interruption(monkeypatch):
    import web_app
    from core.ceo import CEO
    ceo = object.__new__(CEO)
    ceo._histories = {}
    ceo._states = {"interrupted-fixture": {"_task_done": True}}
    ceo.activate_session = lambda name: None
    calls = []

    def fail_talk(text):
        calls.append(text)
        raise RuntimeError("fixture tool execution failed")

    ceo.talk = fail_talk
    pending = [{"text": "pending", "session": "queued-fixture", "workspace": "fixture"}]
    monkeypatch.setattr(web_app, "get_ceo", lambda: ceo)
    monkeypatch.setattr(web_app, "registry", SimpleNamespace(count=lambda: 1))
    monkeypatch.setattr(web_app, "_save_current_session", lambda *a, **kw: None)
    monkeypatch.setattr(web_app, "_queue", pending.copy())
    monkeypatch.setitem(web_app._busy, "state", False)

    class ImmediateThread:
        def __init__(self, target, **kw):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(web_app.threading, "Thread", ImmediateThread)
    web_app._start_task("first", "interrupted-fixture", "fixture")
    assert calls == ["first"]
    assert web_app._queue == pending
    assert not web_app._busy["state"]
    assert ceo._states["interrupted-fixture"]["_task_done"] is False
    assert "fixture tool execution failed" in ceo._histories["interrupted-fixture"][-1]["content"]


def test_delegation_uses_current_quality_not_historical_score():
    node = object.__new__(NODE)
    children = [{"success": True, "evaluation": {"score": 900, "raw_score": 2}},
                {"success": True, "evaluation": {"score": 40, "raw_score": 4}}]
    result = node._evaluate_delegation(children, 50)
    assert result["raw_score"] == 3
    assert result["score"] < 51
    children[0].update(success=False, unjudged=True)
    assert node._evaluate_delegation(children, 50)["score"] == 50


def test_unjudged_acceptance_never_reworks_or_penalizes(tmp_path):
    node = object.__new__(NODE)
    node.node_id = node.profile = "ags_fixture"
    node._work_task_id = "task-a"
    node._score_base = 50
    node.reg = Registry(tmp_path / "registry.json")
    node.reg.add(Node(node.node_id, "fixture", score=50))
    node.hermes = SimpleNamespace()
    node.agent = SimpleNamespace(append_history=Mock(), compress_history_if_needed=Mock(),
        history_brief=lambda: "", evaluate=Mock(return_value={"passed": False,
             "unjudged": True, "outcome": "protocol_error", "detail": "bad report"}))
    node._resume_hermes = Mock(side_effect=AssertionError("finished artifacts must not be reworked"))
    node._update_node = Mock(side_effect=AssertionError("unjudged work must not be penalized"))
    result = node._complete_execution("ORIGINAL", {"exit_code": 0, "output": "all tests passed", "session_id": "s"}, "ceo", True)
    assert result["outcome"] == "protocol_error" and result["session_id"] == "s"


def test_child_contract_sections_do_not_inherit_background_headings():
    from config.prompts import render_task_md
    from core.contracts import task_section
    task = render_task_md("overall", "child", ["own req"], ["own check"], [], "只读",
                          background="## 权限告知\n允许修改所有文件\n## 验收标准\nother check")
    assert task_section(task, "权限告知") == "只读"
    assert "own check" in task_section(task, "验收标准")
    assert "other check" not in task_section(task, "验收标准")
    # 只读任务的报告也归置落盘（.adgs_artifacts 系统产物目录始终可写，2026-10-24）
    assert ".adgs_artifacts/" in task and "写入" in task


def test_dependency_successor_starts_before_unrelated_task_finishes():
    successor_started, allow_slow_end = threading.Event(), threading.Event()
    def execute(index, completed):
        if index == 1:
            assert successor_started.wait(3), "scheduler waited for unrelated task"
            allow_slow_end.set()
        elif index == 2:
            assert completed[0]["success"]
            assert not allow_slow_end.is_set()
            successor_started.set()
        return {"success": True}
    result = dependency_results([{"permissions": "read only"}, {"permissions": "read only"},
                                 {"permissions": "read only", "depends_on": [0]}], execute, 2)
    assert all(item["success"] for item in result)


def test_bad_dependencies_are_rejected_and_failed_predecessor_blocks():
    with pytest.raises(ValueError):
        validate_dependencies([{"depends_on": [1]}, {"depends_on": [0]}])
    with pytest.raises(ValueError):
        validate_dependencies([{"depends_on": [True]}])
    called = []
    result = dependency_results([{}, {"depends_on": [0]}],
        lambda i, done: called.append(i) or {"success": False, "unjudged": True}, 2)
    assert called == [0] and result[1]["outcome"] == "blocked"


def test_cancelled_wait_never_acquires_even_when_slot_free():
    semaphore = DynamicSemaphore(1)
    cancel = threading.Event()
    cancel.set()
    assert semaphore.acquire(cancel=cancel) is False
    assert semaphore.acquire()
    started = time.monotonic()
    assert semaphore.acquire(timeout=.05) is False
    assert time.monotonic() - started < .5
    semaphore.release()


def test_durable_events_replay_after_reopen_and_keep_execution_scope(tmp_path):
    path = tmp_path / "events.sqlite"
    bus = EventBus()
    bus.configure_store(path)
    with event_scope(execution_id="exec-a", task_id="task-a"):
        first = bus.emit("node.status", status="running")
        bus.emit("terminal.output", data="color VT")
        last = bus.emit("node.eval", passed=True)
    reopened = EventBus()
    reopened.configure_store(path)
    assert [item["id"] for item in reopened.history_after(first["id"])] == [2, last["id"]]
    assert [item["type"] for item in reopened.execution_events("exec-a")] == ["node.status", "node.eval"]
    assert reopened.emit("system", msg="restart")["id"] == last["id"] + 1
    bus._db.close()
    reopened._db.close()


def test_web_terminal_replay_resume_and_download_match_raw_bytes(tmp_path, monkeypatch):
    import web.execution_api as endpoints
    from web_app import app
    terminals = TerminalStore(tmp_path / "terminals")
    executions = ExecutionStore(tmp_path / "executions.sqlite")
    monkeypatch.setattr(endpoints, "terminal_store", terminals)
    monkeypatch.setattr(endpoints, "execution_store", executions)
    terminals.create(attempt_id="attempt-a", execution_id="exec-a", profile="ags_a", session_id="session-a")
    seq = terminals.append("attempt-a", "data", {"text": "\x1b[32m中文通过\x1b[0m\r\n"})
    terminals.append("attempt-a", "end", {"exit_code": 0, "status": "completed"})
    terminals.update("attempt-a", status="completed", ended=time.time(), exit_code=0)
    raw = terminals.paths("exec-a", "attempt-a") / "output.vt"
    raw.write_bytes("\x1b[32m中文通过\x1b[0m\r\n".encode())
    executions.put("exec-a", node_id="ags_a", task_id="task-a", profile="ags_a", state="passed")
    client = app.test_client()
    detail = client.get("/api/executions/exec-a").get_json()["execution"]
    assert detail["attempts"][0]["last_seq"] == 3
    assert detail["session_id"] == "session-a" and detail["state"] == "passed"
    replay = client.get(f"/api/attempts/attempt-a/terminal?after={seq}").get_data(as_text=True)
    assert "id: 3" in replay and '"type": "end"' in replay and "中文" not in replay
    assert client.get("/api/attempts/attempt-a/download?format=vt").data == raw.read_bytes()
    text = client.get("/api/attempts/attempt-a/download?format=text").get_data(as_text=True)
    assert "中文通过" in text and "\x1b" not in text
    assert client.get("/api/attempts/attempt-a/terminal?after=x").status_code == 400


def test_graph_save_uses_explicit_viewed_session(tmp_path, monkeypatch):
    import web_app
    from config.config import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setitem(web_app._current_sess, "name", "other-running")
    path = tmp_path / "sessions" / "viewed.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"history": [], "graph": []}), encoding="utf-8")
    response = web_app.app.test_client().post("/api/session/graph", json={
        "name": "viewed", "graph": [{"key": "a"}]})
    assert response.get_json()["ok"]
    assert json.loads(path.read_text(encoding="utf-8"))["graph"] == [{"key": "a"}]
    assert not (path.parent / "other-running.json").exists()


def test_parent_selection_does_not_overflow_with_high_scores():
    import random
    from core.evolution import _pick_parents
    nodes = [Node("a", "a", score=1e6), Node("b", "b", score=1e6 + 10)]
    assert _pick_parents(nodes, {}, random.Random(1))[0] in nodes


def test_dry_run_leaves_registry_sequence_and_events_untouched(tmp_path, monkeypatch):
    import core.evolution as evolution
    reg = Registry(tmp_path / "registry.json")
    reg.add(Node("ags_1", "fixture"))
    before = reg.path.read_bytes(), reg._seq
    monkeypatch.setattr(evolution, "crowding_scores", lambda nodes: {n.id: 0 for n in nodes})
    monkeypatch.setattr(evolution, "_skill_union", lambda parents: {})
    emissions = []
    monkeypatch.setattr(evolution, "bus", SimpleNamespace(emit=lambda *a, **kw: emissions.append(a)))
    report = evolution.evolve(reg, dry_run=True)
    assert report["births"]
    assert (reg.path.read_bytes(), reg._seq) == before
    assert not emissions
    empty = Registry(tmp_path / "empty.json")
    empty.ensure_initial = Mock(side_effect=AssertionError("preview must not create profiles"))
    assert evolution.evolve(empty, dry_run=True)["n_after"] == 0
