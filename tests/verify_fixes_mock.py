"""修复验证（确定性 mock 测试，零 LLM 消耗）

覆盖 4 个 bug 的修复逻辑：
  A. kill+resume 修正循环（mock hermes.execute 失败 → 验证 resume 被调用）
  B. CEO 澄清后锁定节点（mock route → 验证 route 只调用 1 次）
  C. 拥挤度核函数 σ²（纯 numpy，精确断言）
  D. split_tasks 可达（mock decide 返回 split_tasks → work 委派给下层节点）
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import tempfile
import numpy as np

PASS = []


def check(name, ok, detail=""):
    PASS.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)


# ================================================================
# Bug C：拥挤度核函数应除以 σ² 而非 σ
# ================================================================
def test_c():
    from utils import embedding as emb
    sigma = 0.15
    v1 = np.array([1.0, 0.0])
    v2 = np.array([0.8, 0.6])  # 归一化，cos = 0.8 > threshold 0.6
    dens = emb.crowding_density([v1, v2], sigma=sigma, threshold=0.6)
    expected = float(np.exp(-(1.0 - 0.8) / (sigma * sigma)))
    wrong_old = float(np.exp(-(1.0 - 0.8) / sigma))  # 修复前公式
    d0 = dens[0]
    ok = abs(d0 - expected) < 1e-9 and abs(d0 - wrong_old) > 1e-6
    check("C 拥挤度核函数除以 σ²",
          ok,
          f"density={d0:.8f} 期望={expected:.8f} 旧公式={wrong_old:.8f}")
    return ok


# ================================================================
# Bug B：CEO 澄清后重派锁定首次选中的节点（route 只调 1 次）
# ================================================================
def test_b():
    import core.ceo as ceo_mod
    from core.ceo import CEO

    ceo = CEO.__new__(CEO)  # 跳过 __init__，不构造 CHAT
    ceo.workflow_log = []

    class FakeNode:
        id = "ags_test"

    route_calls = []

    def fake_route(task_md, reg=None):
        route_calls.append(task_md)
        return FakeNode()

    class FakeNODE:
        def __init__(self, node_id):
            self.node_id = node_id

        def work(self, task_md, upper="ceo"):
            # 始终 need_clarify → 循环 3 次全走澄清分支
            return {"success": False, "need_clarify": True,
                    "question": "q?", "need_info": [],
                    "node_id": self.node_id, "task_md": task_md}

    def fake_answer(result):
        return "上层补充说明"

    ceo_mod.route = fake_route
    ceo_mod.NODE = FakeNODE
    ceo._answer_clarify = fake_answer

    r = ceo._dispatch_single("任务书", "t")
    n_route = len(route_calls)
    n_clarified = sum(1 for w in ceo.workflow_log if w.get("status") == "clarified")
    ok = n_route == 1 and n_clarified == 3
    check("B CEO 澄清后锁定节点",
          ok,
          f"route调用={n_route}（期望1） 澄清次数={n_clarified}（期望3） 结果={r.get('error')}")
    return ok


# ================================================================
# Bug A：kill+resume 修正循环（execute 失败 → resume 被调用）
# ================================================================
def test_a():
    from config.config import config
    from core.node import NODE
    from core.registry import Node, Registry

    tmp_reg = Registry(path=Path(tempfile.mktemp(suffix=".json")))
    tmp_reg.add(Node("ags_test", "测试节点"))

    nd = NODE("ags_test", registry=tmp_reg)

    nd.agent.decide = lambda task_md: {
        "action": "plan_hermes", "goal": "g", "steps": [],
        "acceptance": [], "permissions": "", "deliverables": [],
        "task_md": "final_md"}

    calls = {"exec": 0, "resume": 0}

    def fake_run(instruction, timeout=None):
        calls["exec"] += 1
        return {"exit_code": -1, "session_id": "sess_x",
                "output": "", "error": "Wall timeout after 1800s"}

    def fake_resume(instruction, session_id, timeout=None):
        calls["resume"] += 1
        return {"exit_code": 0, "session_id": session_id,
                "output": "已修正并完成", "error": ""}

    nd._run_hermes = fake_run
    nd._resume_hermes = fake_resume
    nd.agent.evaluate = lambda *a, **k: {"passed": True, "score": 50.0}
    nd.evolve_describe = lambda *a, **k: None

    r = nd.work("任务书")
    ok = calls["resume"] == 1 and r.get("success") is True
    check("A kill+resume 修正循环",
          ok,
          f"execute={calls['exec']} resume={calls['resume']}（期望1） success={r.get('success')}")
    return ok


# ================================================================
# Bug A 补充：resume 上限 correction_limit 次
# ================================================================
def test_a_limit():
    from config.config import config
    from core.node import NODE
    from core.registry import Node, Registry

    tmp_reg = Registry(path=Path(tempfile.mktemp(suffix=".json")))
    tmp_reg.add(Node("ags_test2", "测试节点2"))

    nd = NODE("ags_test2", registry=tmp_reg)
    nd.agent.decide = lambda task_md: {
        "action": "plan_hermes", "goal": "g", "steps": [],
        "acceptance": [], "permissions": "", "deliverables": [],
        "task_md": "final_md"}

    calls = {"resume": 0}

    def always_fail(instruction, timeout=None):
        return {"exit_code": -1, "session_id": "sess_x",
                "output": "", "error": "timeout"}

    def fake_resume(instruction, session_id, timeout=None):
        calls["resume"] += 1
        return {"exit_code": -1, "session_id": session_id,
                "output": "", "error": "still timeout"}

    nd._run_hermes = always_fail
    nd._resume_hermes = fake_resume
    nd.agent.evaluate = lambda *a, **k: {"passed": False, "score": 0.0}
    nd.evolve_describe = lambda *a, **k: None

    r = nd.work("任务书")
    limit = config.correction_limit
    ok = calls["resume"] == limit and r.get("success") is False
    check("A resume 次数受 correction_limit 约束",
          ok,
          f"resume={calls['resume']}（期望上限={limit}） success={r.get('success')} error={r.get('error')}")
    return ok


# ================================================================
# Bug D：split_tasks 可达（decide 返回 split_tasks → work 委派给下层节点）
# ================================================================
def test_d():
    from core.node import NODE
    from core.registry import Node, Registry

    tmp_reg = Registry(path=Path(tempfile.mktemp(suffix=".json")))
    tmp_reg.add(Node("ags_test3", "测试节点3"))

    nd = NODE("ags_test3", registry=tmp_reg)
    nd.agent.decide = lambda task_md: {
        "action": "split_tasks", "reason": "任务太复杂",
        "subtasks": [{"name": "a", "goal": "子目标a"},
                     {"name": "b", "goal": "子目标b"}]}
    # mock 委派与打分（避免真实 route/embedding/hermes 依赖）
    nd._delegate = lambda *a, **k: {
        "success": True, "node_id": "ags_test3", "action": "delegate",
        "delegated": True, "reason": "任务太复杂",
        "children": [{"name": "a", "success": True},
                     {"name": "b", "success": True}],
        "summary": "两个子任务完成", "upper": "ceo"}
    nd._evaluate_delegation_and_update = lambda children: None

    r = nd.work("复杂任务书")
    ok = (r.get("action") == "delegate" and r.get("delegated") is True
          and r.get("success") is True)
    check("D split_tasks 可达（work 委派给下层节点）",
          ok,
          f"action={r.get('action')} delegated={r.get('delegated')} success={r.get('success')}")
    return ok


# ================================================================
# Bug A 补充：resume() 命令参数顺序正确（-q 紧跟 query，--resume 紧跟 session_id）
# ================================================================
def test_resume_cmd():
    from core.hermes import HermesInstance

    h = HermesInstance.__new__(HermesInstance)
    h.profile = "ags_x"
    captured = {}

    def fake_run_raw(cmd, live_log, timeout, inactivity_timeout, cwd):
        captured["cmd"] = cmd
        return {"output": "", "exit_code": 0, "error": "", "session_id": "s"}

    h._run_raw = fake_run_raw
    h.resume("修正指令内容", "SID_123", model="m")

    cmd = captured["cmd"]
    # -q 后必须紧跟 query（不能是 --resume）；--resume 后必须紧跟 session_id
    q_idx = cmd.index("-q")
    resume_idx = cmd.index("--resume")
    ok = (cmd[q_idx + 1] == "修正指令内容"
          and cmd[resume_idx + 1] == "SID_123"
          and resume_idx > q_idx)
    check("A resume() 命令参数顺序正确", ok,
          f"cmd={cmd}")
    return ok


if __name__ == "__main__":
    test_c()
    test_b()
    test_a()
    test_a_limit()
    test_d()
    test_resume_cmd()
    total = len(PASS)
    passed = sum(PASS)
    print(f"\n===== 结果：{passed}/{total} 通过 =====", flush=True)
    sys.exit(0 if passed == total else 1)
