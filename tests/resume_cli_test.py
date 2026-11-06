"""验证 HermesInstance.resume() 命令在真实 hermes CLI 上可执行

用 E2E-1 已提取到的真实 session_id 调 resume，观察 hermes --resume 是否真实执行：
  - live.log 出现 "=== RESUME:" 标记（resume 被调用）
  - hermes 进程正常启动并对 --resume 指令做出响应（不报 CLI 语法错误）
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

from config.config import config
from core.hermes import HermesInstance, ensure_profile

SID = "20260815_223038_7950ca"  # E2E-1 真实提取的 session_id
WS = str(ROOT / "data" / "resume_cli_ws")
Path(WS).mkdir(parents=True, exist_ok=True)

ensure_profile("ags_2", {
    "exec_model": config.worker_model,
    "base_url": config.worker_url,
    "api_key": config.worker_key,
})

h = HermesInstance("ags_2")
live = Path(WS) / ".adgs_logs" / "ags_2_live.log"
live.parent.mkdir(parents=True, exist_ok=True)

print(f"[resume-cli] 对 session {SID} 执行 resume...", flush=True)
r = h.resume(
    "继续：确认 lru_cache.py 与 test_lru_cache.py 已就绪，无需改动，直接回复确认即可。",
    session_id=SID, model=config.worker_model, live_log=live,
    timeout=150, inactivity_timeout=120, cwd=WS)

print(f"[resume-cli] exit_code = {r.get('exit_code')}", flush=True)
print(f"[resume-cli] session_id = {r.get('session_id')}", flush=True)
print(f"[resume-cli] error = {str(r.get('error'))[:300]}", flush=True)

if live.exists():
    text = live.read_text(encoding="utf-8", errors="replace")
    print(f"[resume-cli] live.log '=== RESUME:' 出现 {text.count('=== RESUME:')} 次", flush=True)
    print(f"[resume-cli] live.log 前 12 行：", flush=True)
    for line in text.splitlines()[:12]:
        print("   " + line[:100], flush=True)
