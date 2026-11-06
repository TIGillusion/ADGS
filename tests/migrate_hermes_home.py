"""一次性迁移：把旧 hermes profile 数据迁到项目内 .hermes（HERMES_HOME 重定向后）

迁移内容（精简，跳过可重建的运行痕迹 logs/cache/sessions）：
  1. 15 个 active 节点的 profile：skills(技能基因) + state.db(记忆/会话) + config
  2. 主 skills 的 .bundled_manifest（内置 skills 排除名单，供 list_skills 使用）

旧数据仍在 %LOCALAPPDATA%\\hermes 下，不会被删除（作为备份）。
迁移完成后，ADGS 启动的 hermes 会通过 HERMES_HOME 自动使用项目内 .hermes。
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from core.hermes import PROFILES_DIR, _HERMES_HOME
from backup_training import SLIM_SKIP_DIRS, SLIM_KEEP_FILES, load_registry

OLD_HOME = Path.home() / "AppData" / "Local" / "hermes"
OLD_PROFILES = OLD_HOME / "profiles"
OLD_SKILLS = OLD_HOME / "skills"


def copy_slim(src: Path, dst: Path):
    """精简复制 profile：保留 skills/state.db/config，跳过 logs/cache/sessions"""
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.is_dir():
            if item.name in SLIM_SKIP_DIRS:
                continue
            shutil.copytree(item, dst / item.name, dirs_exist_ok=True)
        else:
            if item.name not in SLIM_KEEP_FILES:
                continue
            shutil.copy2(item, dst / item.name)


def main():
    print(f"目标 HERMES_HOME: {_HERMES_HOME}", flush=True)
    print(f"目标 PROFILES_DIR: {PROFILES_DIR}", flush=True)

    active, _ = load_registry()
    ids = sorted(active)
    print(f"待迁移 active 节点: {len(ids)} 个", flush=True)

    migrated, skipped, missing = [], [], []
    for pid in ids:
        src = OLD_PROFILES / pid
        dst = PROFILES_DIR / pid
        if not src.exists():
            missing.append(pid)
            continue
        if dst.exists():
            skipped.append(pid)
            continue
        copy_slim(src, dst)
        migrated.append(pid)
        print(f"  [迁移] {pid}", flush=True)

    # 主 skills 的 bundled manifest（内置 skills 排除名单）
    dst_skills = _HERMES_HOME / "skills"
    dst_skills.mkdir(parents=True, exist_ok=True)
    mf = OLD_SKILLS / ".bundled_manifest"
    if mf.exists():
        shutil.copy2(mf, dst_skills / ".bundled_manifest")
        print("  [迁移] skills/.bundled_manifest", flush=True)

    print(f"\n完成：迁移 {len(migrated)}，已存在跳过 {len(skipped)}，"
          f"源缺失 {len(missing)}", flush=True)
    if missing:
        print(f"  源缺失: {missing}（这些节点的 profile 会在执行时由 ensure_profile 自愈重建）",
              flush=True)


if __name__ == "__main__":
    main()
