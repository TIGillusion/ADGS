"""端到端验证：HERMES_HOME 重定向后，hermes 真实执行并把数据写到项目内 .hermes"""
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
from core.hermes import HermesInstance, ensure_profile, _HERMES_HOME

WS = str(ROOT / "data" / "verify_ws")
Path(WS).mkdir(parents=True, exist_ok=True)

# 记录迁移后项目内 profile 的 logs 状态
proj_log = _HERMES_HOME / "profiles" / "ags_1" / "logs" / "agent.log"
old_log = Path.home() / "AppData" / "Local" / "hermes" / "profiles" / "ags_1" / "logs" / "agent.log"

def mtime(p):
    return p.stat().st_mtime if p.exists() else None

proj_before = mtime(proj_log)
old_before = mtime(old_log)

ensure_profile("ags_1", {
    "exec_model": config.worker_model,
    "base_url": config.worker_url,
    "api_key": config.worker_key,
})

h = HermesInstance("ags_1")
live = Path(WS) / ".adgs_logs" / "ags_1_live.log"
live.parent.mkdir(parents=True, exist_ok=True)

print(f"[verify] HERMES_HOME = {_HERMES_HOME}", flush=True)
print(f"[verify] 执行前 项目内 agent.log mtime = {proj_before}", flush=True)
print(f"[verify] 执行前 旧位置 agent.log mtime = {old_before}", flush=True)
print("[verify] 执行 hermes 真实任务...", flush=True)

t0 = time.time()
r = h.execute(
    f"在工作区 {WS} 创建 hello.txt，写入 'hello world'，然后结束。",
    model=config.worker_model, live_log=live,
    timeout=300, inactivity_timeout=150, cwd=WS)
dt = time.time() - t0

print(f"[verify] 耗时 {dt:.0f}s, exit_code = {r.get('exit_code')}", flush=True)
print(f"[verify] session_id = {r.get('session_id')}", flush=True)
print(f"[verify] error = {str(r.get('error'))[:200]}", flush=True)

hello = Path(WS) / "hello.txt"
print(f"[verify] hello.txt 存在 = {hello.exists()}，内容 = "
      f"{hello.read_text(encoding='utf-8')!r}" if hello.exists() else "[verify] hello.txt 不存在",
      flush=True)

proj_after = mtime(proj_log)
old_after = mtime(old_log)
print("\n[verify] 数据写入位置判定：", flush=True)
print(f"  项目内 .hermes agent.log: {proj_before} -> {proj_after} "
      f"({'已更新 ✓' if proj_after != proj_before else '未变'})", flush=True)
print(f"  旧位置 agent.log:        {old_before} -> {old_after} "
      f"({'未变 ✓（hermes 不再写旧位置）' if old_after == old_before else '仍被写入 ✗'})", flush=True)

ok = (hello.exists() and r.get("exit_code") == 0
      and (proj_after != proj_before) and (old_after == old_before))
print(f"\n[verify] 结果: {'PASS ✓' if ok else 'FAIL ✗'}", flush=True)
sys.exit(0 if ok else 1)
