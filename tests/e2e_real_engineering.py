"""真实端到端工程测试：路由 → 节点闭环（决策→hermes执行→验收→产物）

任务：在工作区实现一个线程安全的 LRU 缓存模块 + pytest 测试 + README
走真实 CEO 无（本脚本直接 NODE.work），验证节点完整闭环与真实产物生成。
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from config.config import config
from config.prompts import render_task_md
from core.registry import registry
from core.router import route
from core.node import NODE
from utils.chat import set_debug_level

set_debug_level(2)

WS = str(ROOT / "data" / "e2e_ws")
config.set_workspace(WS)
print(f"[e2e] 工作区: {WS}", flush=True)

task_md = render_task_md(
    "实现一个线程安全的 LRU 缓存模块，含完整单元测试与使用文档",
    "在工作区实现 LRUCache 类（get/put、容量淘汰、线程安全），写 pytest 测试并全部通过，输出 README",
    [
        "实现 lru_cache.py：LRUCache 类，构造传入 capacity；get(key)/put(key,value)；超出容量时淘汰最久未使用项",
        "线程安全：内部用 threading.Lock 保护读写，多线程并发 put/get 不崩溃、结果一致",
        "写 test_lru_cache.py：覆盖基本读写、容量淘汰顺序、get 命中刷新、线程并发读写一致性",
        "写 README.md：包含安装说明、用法示例、API 说明",
    ],
    [
        "python -m pytest test_lru_cache.py 全部通过（无 FAILED）",
        "lru_cache.py 可被 python -c 'import lru_cache' 正常导入",
        "README.md 存在且含用法示例",
    ],
    ["lru_cache.py", "test_lru_cache.py", "README.md", "report.md"],
    f"只允许修改工作区 {WS} 内的文件，禁止修改其他目录",
    report_path=f"{WS}/report.md",
)

print("\n[e2e] 路由中...", flush=True)
node = route(task_md, reg=registry)
if not node:
    print("[e2e] 无可用节点", flush=True)
    sys.exit(2)
print(f"[e2e] 选中节点: {node.id} | score={node.score} | sr={node.success_rate}",
      flush=True)
print(f"[e2e] 描述: {node.describe[:80]}", flush=True)

t0 = time.time()
result = NODE(node.id).work(task_md, upper="ceo")
dt = time.time() - t0

print("\n" + "=" * 60, flush=True)
print(f"[e2e] 耗时 {dt:.0f}s", flush=True)
print(f"[e2e] success     = {result.get('success')}", flush=True)
print(f"[e2e] action      = {result.get('action')}", flush=True)
print(f"[e2e] delegated  = {result.get('delegated')}", flush=True)
print(f"[e2e] session_id  = {result.get('session_id')}", flush=True)
ev = result.get("evaluation") or {}
print(f"[e2e] 验收 passed = {ev.get('passed')} score={ev.get('score')}", flush=True)
print(f"[e2e] 验收 detail = {str(ev.get('detail'))[:200]}", flush=True)

print("\n[e2e] 工作区产物：", flush=True)
for f in sorted(Path(WS).iterdir()):
    if f.is_file():
        print(f"  - {f.name}  ({f.stat().st_size} bytes)", flush=True)

# 判定：success=True 且核心产物存在
core_ok = result.get("success") is True
files = {f.name for f in Path(WS).iterdir() if f.is_file()}
artifact_ok = "lru_cache.py" in files and "test_lru_cache.py" in files
print(f"\n[e2e] 结果：success={core_ok} 核心产物齐全={artifact_ok}", flush=True)
sys.exit(0 if (core_ok and artifact_ok) else 1)
