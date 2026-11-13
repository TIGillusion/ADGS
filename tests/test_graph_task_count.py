"""验证 graph.task 事件的 count 字段（并发数判定，2026-08-30 修复）。

场景：
1. 单发（stage 只有 1 个子任务）→ 前端序号节点 count=1 → 显示自身任务目标
2. 并发（stage 有 2 个子任务）→ 前端序号节点 count=2 → 显示并发总体任务

不触碰真实 LLM：mock 掉 route / render_task_md / NODE（work 直接返回成功）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# CONFIG() 构造时会 force=True 探测模型上下文（打真实 LLM），测试前替换掉
import utils.context_probe
utils.context_probe.probe_context_window = lambda *a, **k: None

import core.ceo as ceo_mod
from web.event_bus import bus


class FakeNode:
    def __init__(self, nid):
        self.id = nid


class FakeNODE:
    """mock 掉 NODE：work 直接成功返回，不碰 LLM/profile"""

    def __init__(self, *a, **k):
        pass

    def work(self, *a, **k):
        return {"success": True, "output": "ok", "score": 60}

    @staticmethod
    def spawn_parallel(*a, **k):
        return {"success": True, "output": "ok", "score": 60}


def _make_ceo():
    c = object.__new__(ceo_mod.CEO)
    c._active = "test"
    c._talk_session = None
    c._logs = {}
    c._histories = {}
    c._task_seq = 0
    c._round_no = 1
    c.workflow_log = []
    return c


def _capture():
    """收集指定类型事件的监听器"""
    events = []
    q = bus.subscribe()
    # 直接读队列太长尾，改用 history 快照对比
    before = bus.history()
    return events, q, before


def _run_dispatch(ceo, overall, subtasks, stage):
    """跑 _dispatch_parallel 并返回捕获的 graph.task / graph.dispatch"""
    # 捕获: 先记 history 长度, 跑完取新增
    n0 = len(bus.history())
    ceo_mod.route = lambda md, reg=None: FakeNode(f"ags_mock_{len(stage)}")
    ceo_mod.render_task_md = lambda *a, **k: "mock task md"
    ceo_mod.NODE = FakeNODE
    out = ceo._dispatch_parallel(overall, subtasks, stage)
    new_events = bus.history()[n0:]
    return out, new_events


def test_single_dispatch_count_1():
    ceo = _make_ceo()
    subtasks = [{"name": "子任务A", "goal": "目标A",
                 "requirements": [], "acceptance_criteria": [],
                 "deliverables": [], "permissions": ""}]
    out, events = _run_dispatch(ceo, "总任务：做一件事", subtasks, [0])
    tasks = [e["data"] for e in events if e.get("type") == "graph.task"]
    assert len(tasks) == 1, f"单发应只有 1 个 graph.task，实际 {len(tasks)}"
    assert tasks[0]["count"] == 1, f"单发 count 应为 1，实际 {tasks[0]['count']}"
    assert tasks[0]["goal"] == "目标A"
    assert tasks[0]["overall"] == "总任务：做一件事"
    assert all(e["count"] == 1 for e in tasks), "单发所有 graph.task count 应为 1"
    print(f"[1/2] 单发: graph.task count=1, goal={tasks[0]['goal'][:20]!r} "
          f"overall={tasks[0]['overall'][:20]!r} ✓")


def test_parallel_dispatch_count_2():
    ceo = _make_ceo()
    subtasks = [
        {"name": "子任务A", "goal": "目标A", "requirements": [],
         "acceptance_criteria": [], "deliverables": [], "permissions": ""},
        {"name": "子任务B", "goal": "目标B", "requirements": [],
         "acceptance_criteria": [], "deliverables": [], "permissions": ""},
    ]
    out, events = _run_dispatch(ceo, "总任务：做两件事", subtasks, [0, 1])
    tasks = [e["data"] for e in events if e.get("type") == "graph.task"]
    assert len(tasks) == 2, f"并发应有 2 个 graph.task，实际 {len(tasks)}"
    # 每个子任务保留独立 task id，stage_id 共享同一个序号批次节点
    assert len({t["id"] for t in tasks}) == 2, "并发子任务应保留独立 task id"
    assert len({t["stage_id"] for t in tasks}) == 1, "并发子任务应共享同一序号批次"
    for t in tasks:
        assert t["count"] == 2, f"并发 count 应为 2，实际 {t['count']}"
    goals = {t["goal"] for t in tasks}
    assert goals == {"目标A", "目标B"}, f"各自 goal 应保留，实际 {goals}"
    print(f"[2/2] 并发: 2 个 graph.task 共享序号, count=2, "
          f"各自 goal={sorted(goals)} ✓")


if __name__ == "__main__":
    test_single_dispatch_count_1()
    test_parallel_dispatch_count_2()
    print("PASS: graph.task.count 单发=1 / 并发=N，前端据此显示自身任务或并发总体任务")
