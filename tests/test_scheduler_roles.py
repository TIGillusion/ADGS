import sys
from types import ModuleType, SimpleNamespace

from core.delegation_runtime import ExecutionSlot
from core.hermes_roles import install_role_tools


def test_parent_reacquires_slot_without_wall_deadline():
    calls = []
    semaphore = SimpleNamespace(release=lambda: calls.append("release"),
                                acquire=lambda **kwargs: calls.append(kwargs) or True)
    slot = ExecutionSlot(semaphore, deadline=0)
    with slot.lend():
        assert not slot.held
    assert slot.held
    assert "timeout" not in calls[-1]
    slot.close()


def test_coordinator_blocks_code_but_can_wait_and_read(tmp_path, monkeypatch):
    flag = tmp_path / "coordinator.flag"
    monkeypatch.delenv("ADGS_CONTROL_TOOLS", raising=False)
    monkeypatch.setenv("ADGS_COORDINATOR_FLAG", str(flag))
    model = ModuleType("model_tools")
    agent = ModuleType("agent")
    calls = []
    executor = SimpleNamespace(_dispatch_authorized_once=lambda *args, **kwargs: calls.append(kwargs))
    agent.tool_executor = executor
    monkeypatch.setitem(sys.modules, "model_tools", model)
    monkeypatch.setitem(sys.modules, "agent", agent)
    install_role_tools()
    invoke = lambda name: executor._dispatch_authorized_once(None, None, SimpleNamespace(name=name), scope_block=None)
    invoke("terminal")
    assert calls[-1]["scope_block"] is None
    flag.touch()
    invoke("terminal")
    assert "协调" in calls[-1]["scope_block"]
    for name in ("read_file", "mcp__mesh__wait_delegation", "mcp__mesh__delegate_to_node"):
        invoke(name)
        assert calls[-1]["scope_block"] is None


def test_old_task_has_no_total_deadline(tmp_path, monkeypatch):
    import core.node as module
    from core.execution_store import ExecutionStore
    node = object.__new__(module.NODE)
    node.node_id = node.profile = "timer-fixture"
    node.reg = SimpleNamespace(get=lambda _: SimpleNamespace(score=50))
    store = ExecutionStore(tmp_path / "execution.sqlite")
    monkeypatch.setattr(module, "execution_store", store)
    node._prepare_execution("a task", "t", "timer-fixture", timeout=1, workspace=str(tmp_path))
    try:
        assert node._deadline == float("inf")
        assert store.get("timer-fixture")["deadline_at"] is None
    finally:
        with module._ACTIVE_EXECUTIONS_LOCK:
            module._ACTIVE_EXECUTIONS.pop("timer-fixture", None)


def test_five_nested_coordinators_lend_slots_to_descendants():
    import threading
    from core.node import DynamicSemaphore
    from core.delegation_runtime import (DelegationThreads, execution_is_waiting,
                                         lend_execution_slot, owned_execution_slot)

    semaphore = DynamicSemaphore(5)
    pool = DelegationThreads()
    barriers = [threading.Barrier(5) for _ in range(3)]
    lock = threading.Lock()
    finished, peaks = [], []

    def work(index, depth):
        eid = f"nested-{index}-{depth}"
        assert semaphore.acquire(timeout=5)
        with owned_execution_slot(eid, semaphore, deadline=0):
            with lock:
                peaks.append(semaphore._held)
            barriers[depth].wait(timeout=5)
            if depth < 2:
                with lend_execution_slot(eid):
                    assert execution_is_waiting(eid)
                    pool.submit(work, index, depth + 1).result(timeout=10)
                assert not execution_is_waiting(eid)
            with lock:
                finished.append(eid)

    roots = [pool.submit(work, index, 0) for index in range(5)]
    for future in roots:
        future.result(timeout=15)
    assert len(finished) == 15
    assert max(peaks) == 5
    assert semaphore._held == 0


def test_resumed_coordinator_delegates_acceptance_repairs(tmp_path, monkeypatch):
    import core.node as module
    node = object.__new__(module.NODE)
    node._workspace, node._work_task_id = str(tmp_path), "parent-task"
    node.node_id = node.profile = "parent"
    node._summarize_children = lambda children: "existing results"
    node._run_hermes = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("parent executed code"))
    repairs = []

    def delegate(decision, *args):
        repairs.append(decision)
        return {"children": [{"success": True}], "subtasks": decision["subtasks"]}

    node._delegate = delegate
    node._evaluate_delegation_and_update = lambda children, task: {"passed": len(children) == 2}
    node._emit_terminal_status = lambda *args, **kwargs: None
    monkeypatch.setattr(module, "dependency_results", lambda tasks, submit, *a, **kw: [submit(0, {})])
    result = node._continue_delegation({"task_md": "contract", "result": {
        "subtasks": [{"name": "done"}], "children": [{"success": True}],
        "evaluation": {"failed_items": ["missing aggregate report"]}}}, "continue")
    assert result["success"]
    assert len(result["children"]) == len(result["subtasks"]) == 2
    assert "missing aggregate report" in repairs[0]["subtasks"][0]["goal"]
