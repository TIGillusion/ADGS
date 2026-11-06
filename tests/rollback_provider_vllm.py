# 回滚：hermes 节点 model.provider opencode → vllm（2026-09-04 伪装方案撤销用）
#
# 优先从迁移前备份还原（profiles-backup-opencode-20260904/，迁移脚本改动前
# 逐 profile 完整拷贝，含 ags_318/ags_320 的无 api_key 原样结构）；
# 备份缺失时做文本回滚（provider: opencode→vllm + 删除 providers.opencode 块）。
#
# 用法：.venv\Scripts\python.exe tests\rollback_provider_vllm.py

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILES = ROOT / ".hermes" / "profiles"
BACKUP = ROOT / ".hermes" / "profiles-backup-opencode-20260904"

restored, textrolled, missing = 0, 0, 0
for pdir in sorted(PROFILES.iterdir()):
    cfg = pdir / "config.yaml"
    if not pdir.is_dir() or not cfg.exists():
        continue
    text = cfg.read_text(encoding="utf-8")
    if "  provider: opencode" not in text:
        continue  # 已是 vllm 或其它，不动

    bcfg = BACKUP / pdir.name / "config.yaml"
    if bcfg.exists():
        shutil.copy2(bcfg, cfg)
        restored += 1
        continue

    # 备份缺失 → 文本回滚
    out = text.replace("  provider: opencode", "  provider: vllm")
    out = re.sub(
        r"(?m)^providers:\r?\n  opencode:\r?\n    base_url: \S+\r?\n    api_key: \S*\r?\n?",
        "providers: {}\n",
        out,
    )
    cfg.write_text(out, encoding="utf-8")
    textrolled += 1
    print(f"TEXT-ROLLBACK {pdir.name} (backup missing)")

left = 0
for pdir in PROFILES.iterdir():
    cfg = pdir / "config.yaml"
    if cfg.is_file() and "  provider: opencode" in cfg.read_text(encoding="utf-8"):
        left += 1
        print(f"STILL-OPENCODE {pdir.name}")

print(f"\nrollback: restored_from_backup={restored} text_rolled={textrolled} "
      f"backup_missing={missing} still_opencode={left}")
