"""真实 kill+resume 链路验证（Bug A）

用短超时强制 hermes 超时被 kill，验证：
  1. 超时场景下能从真实 hermes 的 stderr 提取 session_id
  2. 修正循环触发 resume（打印 resume 修正日志）
  3. hermes --resume 命令真实执行（live.log 出现 === RESUME: 标记）
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

WS = str(ROOT / "data" / "e2e_resume_ws")
config.set_workspace(WS)
print(f"[e2e-resume] 工作区: {WS}", flush=True)

# 不含长任务关键词（否则超时会被放宽到 7200s），但足够复杂使 60s 内无法完成
task_md = render_task_md(
    "实现一个命令行待办事项管理器",
    "写 todo.py（支持 add/list/done/remove 子命令，JSON 持久化）+ test_todo.py + README.md",
    [
        "实现 todo.py：argparse 子命令 add/list/done/remove，数据存 todos.json",
        "写 test_todo.py：用 pytest 覆盖增删查改与持久化",
        "写 README.md：说明用法与命令示例",
    ],
    [
        "todo.py 可运行 python todo.py add '任务' 且不报错",
        "test_todo.py 用 pytest 全部通过",
    ],
    ["todo.py", "test_todo.py", "README.md", "report.md"],
    f"只允许修改工作区 {WS} 内的文件",
    report_path=f"{WS}/report.md",
)

node = route(task_md, reg=registry)
if not node:
    print("[e2e-resume] 无可用节点", flush=True)
    sys.exit(2)
print(f"[e2e-resume] 选中节点: {node.id}", flush=True)

# 短超时：强制触发 wall timeout → kill → resume
# 150s：超过 hermes 启动+建立会话时间（~90s，先输出 session_id），但不足以完成整个 todo 工程
t0 = time.time()
result = NODE(node.id).work(task_md, upper="ceo", timeout=150)
dt = time.time() - t0

print("\n" + "=" * 60, flush=True)
print(f"[e2e-resume] 耗时 {dt:.0f}s", flush=True)
print(f"[e2e-resume] success     = {result.get('success')}", flush=True)
print(f"[e2e-resume] session_id  = {result.get('session_id')}", flush=True)
print(f"[e2e-resume] error       = {str(result.get('error'))[:200]}", flush=True)

# 检查 live.log 是否出现 resume 标记
log = Path(WS) / ".adgs_logs" / f"{node.id}_live.log"
if log.exists():
    text = log.read_text(encoding="utf-8", errors="replace")
    n_resume = text.count("=== RESUME:")
    n_session = text.count("session_id:")
    print(f"[e2e-resume] live.log 中 '=== RESUME:' 出现 {n_resume} 次", flush=True)
    print(f"[e2e-resume] live.log 中 'session_id:' 出现 {n_session} 次", flush=True)
    # 打印最后一段，人工核对 resume 是否真实执行
    tail = text[-1500:]
    print("\n[e2e-resume] live.log 尾部：", flush=True)
    print(tail, flush=True)
else:
    print("[e2e-resume] 未找到 live.log", flush=True)
