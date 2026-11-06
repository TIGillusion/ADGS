"""真实端到端：节点间递归委派（真实 qwen 拆分 + 真实路由 + 真实 hermes 执行）"""
import sys
import time
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.config import config
from core.node import NODE

# 测试工作区（清空重建）
ws = config.PROJECT_ROOT / "data" / "delegation_ws"
if ws.exists():
    shutil.rmtree(ws)
config.set_workspace(str(ws))
print(f"[e2e] 工作区: {ws}", flush=True)

TASK = """# 任务书
## 总体任务
实现一个 Python 工具库，包含三个互不依赖的模块，每个模块都要有对应的单元测试并通过。
## 当前任务
一次性实现整个工具库的全部三个模块
## 需求明细
- 模块1: string_utils.py —— 字符串工具：reverse_string、capitalize_words、word_count
- 模块2: list_utils.py —— 列表工具：dedup_preserve_order、flatten、chunk
- 模块3: file_utils.py —— 文件工具：read_text、count_lines、list_by_suffix
- 每个模块配套 test_*.py（pytest 风格），运行 pytest 全部通过
## 验收标准
- 三个模块文件都存在且函数可正常导入调用
- 每个模块有对应测试文件
- pytest 运行全部通过（退出码 0）
## 交付物
- string_utils.py + test_string_utils.py
- list_utils.py + test_list_utils.py
- file_utils.py + test_file_utils.py
"""

node = NODE("ag_1")
print(f"[e2e] 节点 {node.node_id}：让 qwen 智能体拆分任务...", flush=True)

decision = node.agent.split(TASK)
if not decision or not decision.get("subtasks"):
    print("[e2e] split 返回空，改用 decide", flush=True)
    decision = node.agent.decide(TASK)

if decision.get("action") != "split_tasks":
    print(f"[e2e] 智能体未拆（action={decision.get('action')}），手动构造子任务兜底", flush=True)
    decision = {"action": "split_tasks", "reason": "拆分为三个独立模块", "subtasks": [
        {"name": "字符串工具", "goal": "实现 string_utils.py 及 test_string_utils.py，pytest 通过",
         "requirements": ["reverse_string", "capitalize_words", "word_count"],
         "acceptance_criteria": ["string_utils.py 存在", "test_string_utils.py 存在", "pytest 通过"],
         "deliverables": ["string_utils.py", "test_string_utils.py"]},
        {"name": "列表工具", "goal": "实现 list_utils.py 及 test_list_utils.py，pytest 通过",
         "requirements": ["dedup_preserve_order", "flatten", "chunk"],
         "acceptance_criteria": ["list_utils.py 存在", "test_list_utils.py 存在", "pytest 通过"],
         "deliverables": ["list_utils.py", "test_list_utils.py"]},
        {"name": "文件工具", "goal": "实现 file_utils.py 及 test_file_utils.py，pytest 通过",
         "requirements": ["read_text", "count_lines", "list_by_suffix"],
         "acceptance_criteria": ["file_utils.py 存在", "test_file_utils.py 存在", "pytest 通过"],
         "deliverables": ["file_utils.py", "test_file_utils.py"]},
    ]}
else:
    print(f"[e2e] qwen 智能体拆出 {len(decision.get('subtasks', []))} 个子任务:", flush=True)
    for st in decision.get("subtasks", []):
        print(f"    - {st.get('name')}: {str(st.get('goal',''))[:60]}", flush=True)

t0 = time.time()
result = node._delegate(decision, TASK, "ceo", None, 0)
elapsed = time.time() - t0

print("=" * 60, flush=True)
print(f"[e2e] 递归委派完成，耗时 {elapsed:.0f}s", flush=True)
print(f"[e2e] success={result.get('success')} action={result.get('action')} "
      f"delegated={result.get('delegated')}", flush=True)
for c in result.get("children", []):
    print(f"[e2e]   子任务 [{c.get('name')}] → 节点 {c.get('node_id')} "
          f"success={c.get('success')}", flush=True)
print("=" * 60, flush=True)

print("[e2e] 工作区产物:", flush=True)
for f in sorted(ws.rglob("*.py")):
    if f.name.endswith("_utils.py") or f.name.startswith("test_"):
        print(f"    {f.relative_to(ws)} ({f.stat().st_size}B)", flush=True)

print("[e2e] DONE", flush=True)
