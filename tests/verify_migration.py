"""验证 hermes home 迁移结果：路径 + 技能基因完整性"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from core.hermes import PROFILES_DIR, _HERMES_HOME, HermesInstance

print(f"HERMES_HOME  = {_HERMES_HOME}", flush=True)
print(f"PROFILES_DIR = {PROFILES_DIR}", flush=True)
print(f"PROFILES_DIR 存在 = {PROFILES_DIR.exists()}", flush=True)
print()

total = sum(f.stat().st_size for f in Path(_HERMES_HOME).rglob("*") if f.is_file())
print(f".hermes 总大小: {total / 1024 / 1024:.1f} MB", flush=True)
print()

for pid in ["ags_1", "ags_11", "ags_2", "ags_3"]:
    skills = HermesInstance.list_skills(pid)
    print(f"{pid} 自定义技能数: {len(skills)}", flush=True)
    for k, v in list(skills.items())[:8]:
        print(f"    - {k}: {v[:60]}", flush=True)
    print(flush=True)
