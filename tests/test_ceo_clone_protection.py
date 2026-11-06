"""回归测试：CEO 同节点多任务独立会话保护 + 描述净化

A. _dispatch_parallel：同一节点被分配 2 个子任务时 → 2 个同 profile 独立会话，
   且每个子任务只路由一次（route 调用 = 子任务数）
B. _clean_describe：剥离代码块 / 引号 / 前缀；结构化残留返回空（保留旧描述）

用法：cd 到项目根，python tests/test_ceo_clone_protection.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import core.registry as reg_mod
import core.ceo as ceo_mod
from core.ceo import CEO
from core.registry import Node, Registry

PASS = []


def check(name, ok, detail=""):
    PASS.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)


# ================================================================
# A. CEO 同节点多任务独立会话保护
# ================================================================
def test_clone_protection():
    tmp = Registry(path=Path(tempfile.mktemp(suffix="_clone.json")))
    n1 = Node("ags_x", "测试节点")
    tmp.add(n1)
    orig_reg = reg_mod.registry
    orig_route = ceo_mod.route
    reg_mod.registry = tmp  # 临时替换单例，让 ceo 函数内 import 拿到 tmp
    ceo = CEO.__new__(CEO)
    ceo.workflow_log = []
    calls = {"route": 0, "single": 0, "clone": 0}

    def fake_route(md, reg=None):
        calls["route"] += 1
        return n1

    def fake_single(md, name, node=None, task_id=None, goal=None, **kwargs):
        calls["single"] += 1
        assert node is not None and node.id == "ags_x", \
            f"本体派发应锁定节点，实际 node={node}"
        assert task_id, f"应携带 task_id，实际 {task_id}"
        return {"success": True, "node": node.id}

    def fake_clone(nid, md, name, task_id=None):
        calls["clone"] += 1
        assert nid == "ags_x", f"克隆应基于本体 id，实际 {nid}"
        assert task_id, f"应携带 task_id，实际 {task_id}"
        return {"success": True, "node": nid}

    ceo_mod.route = fake_route
    ceo._dispatch_single = fake_single
    ceo._dispatch_clone = fake_clone
    try:
        subtasks = [
            {"name": "任务A", "goal": "a", "requirements": [],
             "acceptance_criteria": [], "deliverables": []},
            {"name": "任务B", "goal": "b", "requirements": [],
             "acceptance_criteria": [], "deliverables": []},
        ]
        r = ceo._dispatch_parallel("总目标", subtasks, [0, 1])
        ok = (calls["route"] == 2 and calls["single"] == 2
              and calls["clone"] == 0 and len(r) == 2)
        check("A 同节点2任务 → 同profile独立会话，不创建克隆",
              ok,
              f"route={calls['route']} single={calls['single']} "
              f"clone={calls['clone']}")
    finally:
        reg_mod.registry = orig_reg
        ceo_mod.route = orig_route
    return ok


def test_clone_single_node_task():
    """单任务不克隆（node_count==1 时走本体直接派发）"""
    tmp = Registry(path=Path(tempfile.mktemp(suffix="_clone.json")))
    n1 = Node("ags_y", "测试节点2")
    tmp.add(n1)
    orig_reg = reg_mod.registry
    orig_route = ceo_mod.route
    reg_mod.registry = tmp
    ceo = CEO.__new__(CEO)
    ceo.workflow_log = []
    calls = {"single": 0, "clone": 0}

    def fake_single(md, name, node=None, task_id=None, goal=None, **kwargs):
        calls["single"] += 1
        return {"success": True, "node": node.id if node else None}

    def fake_clone(nid, md, name):
        calls["clone"] += 1
        return {"success": True, "node": nid}

    ceo_mod.route = lambda md, reg=None: n1
    ceo._dispatch_single = fake_single
    ceo._dispatch_clone = fake_clone
    try:
        subtasks = [{"name": "任务A", "goal": "a", "requirements": [],
                     "acceptance_criteria": [], "deliverables": []}]
        ceo._dispatch_parallel("总目标", subtasks, [0])
        ok = calls["single"] == 1 and calls["clone"] == 0
        check("A 单任务不克隆（走本体）", ok,
              f"single={calls['single']} clone={calls['clone']}")
    finally:
        reg_mod.registry = orig_reg
        ceo_mod.route = orig_route
    return ok


# ================================================================
# B. 描述净化
# ================================================================
def test_clean_describe():
    from core.agent import _clean_describe
    cases = [
        # (输入, 期望输出或 "")
        ("```json\n{\"skill_name\": \"x\"}\n```", ""),
        ("```\n这是一段正常描述。\n```", "这是一段正常描述。"),
        ("```json\n正常描述内容。\n```", "正常描述内容。"),
        ("{\"a\": 1}", ""),
        ("[1, 2, 3]", ""),
        ("以下是节点描述：精通 Python 后端开发", "精通 Python 后端开发"),
        ("节点描述：擅长前端 React", "擅长前端 React"),
        ("\"带引号的描述文本\"", "带引号的描述文本"),
        ("正常描述文本，没有特殊格式。", "正常描述文本，没有特殊格式。"),
    ]
    fails = []
    for inp, exp in cases:
        got = _clean_describe(inp)
        if got != exp:
            fails.append(f"{inp[:30]!r} → {got!r}（期望 {exp!r}）")
    check("B 描述净化 9 例全部符合预期",
          not fails, "；".join(fails[:3]))
    return not fails


if __name__ == "__main__":
    test_clone_protection()
    test_clone_single_node_task()
    test_clean_describe()
    total = len(PASS)
    passed = sum(PASS)
    print(f"\n===== 结果：{passed}/{total} 通过 =====", flush=True)
    sys.exit(0 if passed == total else 1)
