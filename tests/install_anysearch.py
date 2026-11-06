"""批量安装 anysearch 搜索 skill 到所有 ags_N profile

让每个节点的 hermes 默认拥有 anysearch 搜索工具（SKILL.md 触发式加载）
"""
import sys
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.hermes import PROFILES_DIR, _extract_skill_name
from core.registry import registry

SKILL_SRC = Path(__file__).resolve().parent.parent / "skills" / "anysearch"


def install_to_all():
    """安装 anysearch 到所有活动节点 profile"""
    nodes = registry.active()
    installed, skipped = [], []
    for n in nodes:
        dest = PROFILES_DIR / n.id / "skills" / "anysearch"
        if dest.exists():
            skipped.append(n.id)
            continue
        try:
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SKILL_SRC / "SKILL.md", dest / "SKILL.md")
            installed.append(n.id)
        except Exception as e:
            print(f"  !! {n.id} 安装失败: {e}")
    print(f"已安装到 {len(installed)} 个节点" + (f"（{len(skipped)} 个已存在跳过）" if skipped else ""))
    if installed:
        print("示例:", installed[:8], ("..." if len(installed) > 8 else ""))


def verify():
    """抽查几个 profile 确认 skill 已就位"""
    nodes = registry.active()[:5]
    for n in nodes:
        p = PROFILES_DIR / n.id / "skills" / "anysearch" / "SKILL.md"
        name = _extract_skill_name(p.read_text(encoding="utf-8"), p) if p.exists() else "缺失"
        print(f"  {n.id}: anysearch={name}")


if __name__ == "__main__":
    print(f"目标: {len(registry.active())} 个活动节点")
    install_to_all()
    print("\n抽查验证:")
    verify()
