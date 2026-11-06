"""为 registry 中所有 active 节点补齐缺失的 hermes profile

场景：hermes 节点 profile 存在项目内 .hermes/profiles/ags_N
（HERMES_HOME 已重定向到项目内，随项目一起迁移）。
新电脑首次运行若节点 profile 缺失，可跑本脚本批量补建
（新版代码在节点执行时也会自动补建，此脚本用于显式批量补齐 / 预建）。

用法（在项目根目录）：
  .venv\\Scripts\\python tests\\ensure_node_profiles.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.config import config
from core.hermes import ensure_profile, PROFILES_DIR
from core.registry import registry


def main():
    nodes = registry.active()
    src = config.PROJECT_ROOT / "skills" / "anysearch" / "SKILL.md"
    worker_cfg = {
        "exec_model": config.worker_model,
        "base_url": config.worker_url,
        "api_key": config.worker_key,
    }

    created, skipped, failed = [], [], []
    for n in nodes:
        if (PROFILES_DIR / n.id).exists():
            skipped.append(n.id)
            continue
        try:
            if ensure_profile(n.id, worker_cfg, anysearch_src=src):
                created.append(n.id)
                print(f"  补建 {n.id} OK")
            else:
                failed.append(n.id)
                print(f"  !! {n.id} 失败")
        except Exception as e:
            failed.append(n.id)
            print(f"  !! {n.id} 失败: {e}")

    print(f"\n完成：补建 {len(created)}，已存在跳过 {len(skipped)}，失败 {len(failed)}")
    if failed:
        print("失败节点可能 hermes 未装好或未在 PATH，请先跑 setup_new_pc.bat")


if __name__ == "__main__":
    print(f"目标: {len(registry.active())} 个 active 节点")
    main()
