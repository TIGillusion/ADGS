"""节点间递归委派 mock 单测（覆盖 depends_on 分组/注入、同节点克隆、委派打分）"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.registry import Registry, Node
from core.node import NODE
from core.router import resolve_stages
from config.config import config

reg = Registry(path=Path(tempfile.mktemp(suffix=".json")))
for i in range(5):
    reg.add(Node(f"ag_{i+1}", f"节点{i+1} 领域{i%3}", gen=0, birth_type="initial"))


class FakeChat:
    def get_full_response(self, messages, max_tokens=None):
        return "（自动澄清）上层补充说明"


class FakeAgent:
    def __init__(self):
        self.history = []
        self.describe = ""
        self.chat = FakeChat()

    def reset_history(self):
        self.history = []

    def append_history(self, role, content):
        if content:
            self.history.append({"role": role, "content": content})

    def compress_history_if_needed(self):
        return False

    def history_brief(self):
        return ""


def make_node(nid):
    n = NODE.__new__(NODE)
    n.node_id = nid
    n.reg = reg
    n.profile = nid
    n.agent = FakeAgent()
    n.hermes = None
    return n


# ==================== 1) resolve_stages 纯函数 ====================
print("== 1) resolve_stages ==")
st = [{"depends_on": []}, {"depends_on": []}, {"depends_on": [0, 1]}]
stages = resolve_stages(st)
print("线性依赖:", stages)
assert len(stages) == 2 and set(stages[0]) == {0, 1} and stages[1] == [2], "依赖分组错误"

st_ring = [{"depends_on": [1]}, {"depends_on": [0]}]
stages_ring = resolve_stages(st_ring)
print("环依赖:", stages_ring)
assert sum(len(s) for s in stages_ring) == 2, "环依赖应落入最后 stage"

st_nodep = [{}, {}, {}]
assert resolve_stages(st_nodep) == [[0, 1, 2]], "无依赖应单 stage"
print("1) 通过 ✓")


# ==================== 2) _evaluate_delegation 委派打分 ====================
print("\n== 2) _evaluate_delegation ==")
n = make_node("ag_1")
# 全成功：应加分（子任务平均分 95 > 旧分 50）
r = n._evaluate_delegation([{"success": True, "evaluation": {"score": 95}},
                            {"success": True, "evaluation": {"score": 90}}], 50)
print("全成功:", r)
assert r["passed"] is True and r["score"] > 50 and r["score"] <= 65, "全成功应加分(≤+15)"
# 有失败：passed=False，分数下降
r2 = n._evaluate_delegation([{"success": True, "evaluation": {"score": 90}},
                             {"success": False}], 50)
print("有失败:", r2)
assert r2["passed"] is False and r2["score"] < 50, "有失败应降分"
# 空子任务
r3 = n._evaluate_delegation([], 50)
assert r3["passed"] is False and r3["score"] == 50
print("2) 通过 ✓")


# ==================== 3) _delegate 依赖注入 + 同节点克隆 ====================
print("\n== 3) _delegate ==")
import core.router as router_mod

injected = {}


def fake_route(task_md, reg=None, verbose=False, **kw):
    if "聚合" in task_md:
        return reg.get("ag_3")
    return reg.get("ag_2")  # 前 2 个都到 ags_2


router_mod.route = fake_route

child_calls = []


def fake_run_child(child_id, sub_md, depth, upper, timeout):
    child_calls.append(("child", child_id, depth))
    if "前驱子任务" in sub_md:
        injected["hit"] = True
    return {"success": True, "node_id": child_id, "action": "plan_hermes",
            "summary": f"完成 {child_id}", "work_log": f"log {child_id}"}


def fake_spawn(node_id, task_md, registry=None, upper=None, depth=0):
    child_calls.append(("clone", node_id, depth))
    return {"success": True, "node_id": node_id, "action": "plan_hermes",
            "summary": f"完成 {node_id}", "work_log": f"log {node_id}"}


n3 = make_node("ag_1")
n3._run_child = fake_run_child
n3.spawn_parallel = fake_spawn

decision = {"action": "split_tasks", "reason": "测试", "subtasks": [
    {"name": "子任务0", "goal": "g0", "requirements": [], "acceptance_criteria": [], "deliverables": []},
    {"name": "子任务1", "goal": "g1", "requirements": [], "acceptance_criteria": [], "deliverables": []},
    {"name": "后端聚合", "goal": "g2", "requirements": [], "acceptance_criteria": [], "deliverables": [], "depends_on": [0, 1]},
]}
task_md = "# 任务书\n## 总体任务\n测试\n## 当前任务\nx"

result = n3._delegate(decision, task_md, "ceo", None, 0)
print("children:", [(c.get("name"), c.get("success")) for c in result["children"]])
print("child_calls:", child_calls)
print("依赖注入命中:", injected.get("hit", False))

assert result["success"] is True and len(result["children"]) == 3
# 前 2 个子任务都到 ags_2（node_count=2>1）→ 应克隆；第 3 个到 ags_3 → child
clone_cnt = sum(1 for c in child_calls if c[0] == "clone")
child_cnt = sum(1 for c in child_calls if c[0] == "child")
assert clone_cnt == 2, f"同节点多任务应克隆2个，实际 {clone_cnt}"
assert child_cnt == 1, f"第3个应直接 child，实际 {child_cnt}"
assert injected.get("hit"), "依赖子任务应注入前驱结果上下文"
print("3) 通过 ✓（依赖注入 + 同节点克隆生效）")

print("\n=== 递归委派 mock 全部通过 ===")
