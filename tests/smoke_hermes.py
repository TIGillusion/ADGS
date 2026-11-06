"""hermes 冒烟测试：验证锁冲突解除后，真实 hermes 能正常启动并输出 session_id + 完成任务"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from config.config import config
from core.hermes import HermesInstance, ensure_profile

WS = str(ROOT / "data" / "smoke_ws")
Path(WS).mkdir(parents=True, exist_ok=True)

ensure_profile("ags_1", {
    "exec_model": config.worker_model,
    "base_url": config.worker_url,
    "api_key": config.worker_key,
})

h = HermesInstance("ags_1")
live = Path(WS) / ".adgs_logs" / "ags_1_live.log"
live.parent.mkdir(parents=True, exist_ok=True)

print("[smoke] 执行 hermes 短任务...", flush=True)
r = h.execute(
    f"在工作区 {WS} 创建 hello.txt，写入内容 'hello world'，然后结束。",
    model=config.worker_model, live_log=live,
    timeout=300, inactivity_timeout=120, cwd=WS)

print(f"[smoke] exit_code = {r.get('exit_code')}", flush=True)
print(f"[smoke] session_id = {r.get('session_id')}", flush=True)
print(f"[smoke] error = {str(r.get('error'))[:200]}", flush=True)
print(f"[smoke] output 尾部 = {r.get('output', '')[-800:]}", flush=True)

hello = Path(WS) / "hello.txt"
print(f"[smoke] hello.txt 存在 = {hello.exists()}", flush=True)
if hello.exists():
    print(f"[smoke] hello.txt 内容 = {hello.read_text(encoding='utf-8')!r}", flush=True)
