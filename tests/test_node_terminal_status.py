"""验证 NODE 闭环所有路径都发实例终态 node.status（2026-09-03 用户报告修复）。

背景：web3 图谱实例状态完全由事件驱动（waiting→running/delegating→done/failed）。
此前闭环内任何路径跳过后半程（委派完成无终态、决策失败直接返回、闭环异常
中断）→ 前端实例永久卡在 running/delegating（"任务明明完成，还有节点运行中"）。

修复：NODE.work() 统一终态保证——正常验收 / 委派完成 / 决策失败 / 早期失败 /
内部异常五路径都在返回（或 re-raise）前发一次带 task 的终态 node.status
（done/failed 由 passed 映射，与前端降态规则同口径）。

不触碰真实 LLM/hermes：object.__new__ 构造裸 NODE + 假 agent/registry/hermes，
只测事件契约（与 test_task_seq_reset.py 同构）。
"""
import queue
import sys
import tempfile
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# CONFIG() 构造时会 force=True 探测模型上下文（打真实 LLM），测试前替换掉
import utils.context_probe
utils.context_probe.probe_context_window = lambda *a, **k: None

import core.node as node_mod
from config.config import config
from web.event_bus import bus

ID = "t_node"


@pytest.fixture(autouse=True)
def isolated_legacy_runtime(tmp_path, monkeypatch):
    from core.execution_store import ExecutionStore
    # These cases exercise the decision/evaluation path, independent of the
    # installation's current merged-brain setting and persistent executions.
    monkeypatch.setattr(config, "node_merged_brain", False)
    monkeypatch.setattr(node_mod, "execution_store", ExecutionStore(tmp_path / "executions.sqlite"))
    monkeypatch.setattr(node_mod.NODE, "_schedule_maintenance", lambda *a, **k: None)


class FakeNode:
    def __init__(self, node_id):
        self.id = node_id
        self.describe = "测试节点"
        self.score = 70.0
        self.success_rate = 0.7
        self.tasks_done = 0


class FakeReg:
    def __init__(self, node_id):
        self._nodes = {node_id: FakeNode(node_id)}

    def get(self, node_id):
        return self._nodes.get(node_id)

    def update(self, node_id, **kw):
        n = self._nodes.get(node_id)
        if n:
            for k, v in kw.items():
                setattr(n, k, v)


class FakeAgent:
    def __init__(self):
        self.describe = ""

    def reset_history(self):
        pass

    def append_history(self, role, text):
        pass

    def compress_history_if_needed(self):
        pass

    def history_brief(self):
        return ""


class FakeHermes:
    def __init__(self, result):
        self._result = result

    def execute(self, instruction, **kw):
        return self._result

    def resume(self, instruction, session_id, **kw):
        return self._result


def make_node(agent, hermes):
    n = object.__new__(node_mod.NODE)
    n.node_id = ID
    n.profile = ID
    n.reg = FakeReg(ID)
    n.agent = agent
    n.hermes = hermes
    n._work_task_id = None
    n.evolve_describe = lambda *a, **k: None
    n._ensure_live_log = lambda: Path(tempfile.gettempdir()) / "t_live.log"
    return n


def drain():
    q = bus.subscribe()
    events = []
    try:
        while True:
            try:
                events.append(q.get_nowait())
            except queue.Empty:
                break
    finally:
        bus.unsubscribe(q)
    return events


def run_work(task_id, decide, evaluate=None, hermes_result=None):
    """订阅事件 → 跑 work() → 返回 (结果或异常, 事件列表)"""
    agent = FakeAgent()
    agent.decide = decide
    if evaluate is not None:
        agent.evaluate = evaluate
    n = make_node(agent, FakeHermes(hermes_result or {}))
    q = bus.subscribe()
    try:
        try:
            res = n.work("任务书", task_id=task_id)
            exc = None
        except Exception as e:  # noqa: BLE001
            res, exc = None, e
    finally:
        bus.unsubscribe(q)
    events = []
    while True:
        try:
            events.append(q.get_nowait())
        except queue.Empty:
            break
    return res, exc, [e for e in events if e["type"] == "node.status"]


def terminal(events, task_id):
    return [e for e in events if e["data"].get("task") == task_id
            and e["data"].get("status") in ("done", "failed", "interrupted")]


def test_delegate_path_emits_terminal():
    """委派路径：子任务全部成功 → 委派实例终态 done + 委派评估分数"""
    agent = FakeAgent()
    agent.decide = lambda t: {"action": "split_tasks", "subtasks": [{"name": "子1", "goal": "g"}]}
    n = make_node(agent, FakeHermes({}))
    # 假 _delegate：跳过路由/LLM，直接返回委派汇总
    n._delegate = lambda decision, task_md, upper, timeout, depth: {
        "success": True,
        "children": [{"success": True, "name": "子1", "evaluation": {"score": 90.0, "raw_score": 10}}],
    }
    q = bus.subscribe()
    try:
        res = n.work("任务书", task_id="t-delegate")
    finally:
        bus.unsubscribe(q)
    events = []
    while True:
        try:
            events.append(q.get_nowait())
        except queue.Empty:
            break
    statuses = [e for e in events if e["type"] == "node.status"]
    term = [e for e in statuses if e["data"].get("task") == "t-delegate"]
    assert res["success"] is True, "委派成功应返回 success=True"
    assert len(term) == 1, f"应恰好一个终态事件，实际 {len(term)}"
    d = term[0]["data"]
    assert d["status"] == "done", f"全子任务成功应为 done，实际 {d['status']}"
    assert d["passed"] is True
    expected = 70.0 + max(-10.0, min(10.0, 90.0 - 70.0)) * config.delta_coef
    assert abs(d["score"] - expected) < 0.01, f"分数应为委派评估分，实际 {d['score']}"
    print("[1/5] 委派完成 → 终态 done + 委派评估分 ✓")


def test_delegate_preflight_error_is_not_masked_as_empty_children():
    """保留写权限等委派预检的真实 protocol_error，不能改成无有效子任务。"""
    agent = FakeAgent()
    agent.decide = lambda t: {"action": "split_tasks", "subtasks": [{"name": "子1", "goal": "g"}]}
    n = make_node(agent, FakeHermes({}))
    n._delegate = lambda *a, **k: n._unjudged("任务书", {}, "protocol_error", "子任务写路径超出上层权限")
    q = bus.subscribe()
    try:
        direct = n.work("任务书", task_id="t-preflight-direct")
    finally:
        bus.unsubscribe(q)
    assert direct["outcome"] == "protocol_error"
    assert direct["error"] == "子任务写路径超出上层权限"


def test_decision_failure_emits_terminal():
    """决策失败（非 plan_hermes/split/ask）→ 终态 failed"""
    res, exc, statuses = run_work(
        "t-decision",
        decide=lambda t: {"action": "unknown_action", "detail": "bad"},
    )
    assert exc is None
    assert res["success"] is False
    term = terminal(statuses, "t-decision")
    assert len(term) == 1, f"应恰好一个终态事件，实际 {len(term)}"
    assert term[0]["data"]["status"] == "interrupted"
    assert term[0]["data"]["passed"] is False
    print("[2/5] 决策失败 → 终态 failed ✓")


def test_early_failure_emits_terminal():
    """hermes 早期失败（非零退出且无输出）→ 终态 failed + 原因"""
    res, exc, statuses = run_work(
        "t-early",
        decide=lambda t: {"action": "plan_hermes", "task_md": "执行"},
        hermes_result={"output": "", "exit_code": 1, "error": "spawn failed",
                       "session_id": None},
    )
    assert exc is None
    assert res["success"] is False
    term = terminal(statuses, "t-early")
    assert len(term) == 1, f"应恰好一个终态事件，实际 {len(term)}"
    d = term[0]["data"]
    assert d["status"] == "interrupted"
    assert "spawn failed" in d.get("detail", ""), f"detail 应含错误原因：{d}"
    print("[3/5] 早期失败 → 终态 failed + 原因 ✓")


def test_eval_success_emits_terminal_with_score():
    """正常验收通过 → 终态 done + 验收分数 + 返工次数"""
    res, exc, statuses = run_work(
        "t-ok",
        decide=lambda t: {"action": "plan_hermes", "task_md": "执行"},
        evaluate=lambda *a, **k: {"passed": True, "score": 95.0, "detail": "ok"},
        hermes_result={"output": "输出", "exit_code": 0, "session_id": "s1", "error": ""},
    )
    assert exc is None
    assert res["success"] is True
    term = terminal(statuses, "t-ok")
    assert len(term) == 1, f"应恰好一个终态事件，实际 {len(term)}"
    d = term[0]["data"]
    assert d["status"] == "done"
    assert d["passed"] is True
    assert abs(d["score"] - 95.0) < 0.01
    assert d["reworks"] == 0
    print("[4/5] 验收通过 → 终态 done + 分数 ✓")


def test_exception_emits_failed_and_reraises():
    """闭环内异常（如验收 LLM 挂了）→ 补发 failed 终态后再 re-raise"""
    def boom(*a, **k):
        raise RuntimeError("LLM down")

    agent = FakeAgent()
    agent.decide = lambda t: {"action": "plan_hermes", "task_md": "执行"}
    agent.evaluate = boom
    n = make_node(agent, FakeHermes(
        {"output": "输出", "exit_code": 0, "session_id": "s1", "error": ""}))
    q = bus.subscribe()
    exc = None
    try:
        n.work("任务书", task_id="t-crash")
    except RuntimeError as e:
        exc = e
    finally:
        bus.unsubscribe(q)
    events = []
    while True:
        try:
            events.append(q.get_nowait())
        except queue.Empty:
            break
    assert exc is not None and str(exc) == "LLM down", "异常必须原样 re-raise"
    statuses = [e for e in events if e["type"] == "node.status"]
    term = terminal(statuses, "t-crash")
    assert len(term) == 1, f"异常路径应补发一个终态事件，实际 {len(term)}"
    d = term[0]["data"]
    assert d["status"] == "interrupted"
    assert d["passed"] is False
    print("[5/5] 内部异常 → 补发 failed 终态 + 原样 re-raise ✓")


if __name__ == "__main__":
    test_delegate_path_emits_terminal()
    test_decision_failure_emits_terminal()
    test_early_failure_emits_terminal()
    test_eval_success_emits_terminal_with_score()
    test_exception_emits_failed_and_reraises()
    print("PASS: NODE 闭环五路径终态保证（委派/决策失败/早期失败/验收/异常）")
