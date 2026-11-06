"""恢复 registry：合并存档节点到当前 registry

策略：保留当前 15 个节点（含最新经验），从存档补回缺失的历史节点。
存档取每个 id 的最新版本。
"""
import sys
import json
import glob
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.registry import Node, ARCHIVE_DIR, registry


def collect_archive():
    """收集存档：每个 id 的最新版本"""
    files = glob.glob(str(ARCHIVE_DIR / "*.json"))
    latest = {}
    for f in files:
        try:
            d = json.loads(open(f, encoding="utf-8").read())
            nid = d.get("id", "")
            m = re.search(r"_(\d+)\.json$", f)
            ts = int(m.group(1)) if m else 0
            if nid and (nid not in latest or ts > latest[nid][0]):
                latest[nid] = (ts, d)
        except Exception:
            continue
    return latest


def main():
    cur_ids = {n.id for n in registry.active()}
    archive = collect_archive()
    print(f"当前活动: {len(cur_ids)} | 存档节点: {len(archive)}")

    missing = {k: v for k, v in archive.items() if k not in cur_ids}
    print(f"待补回: {len(missing)} 个")

    restored = 0
    for nid, (ts, data) in sorted(missing.items(),
                                  key=lambda x: int(x[0].split("_")[1])):
        try:
            n = Node.from_dict(data)
            n.status = "active"
            registry._nodes[nid] = n
            restored += 1
        except Exception as e:
            print(f"  !! {nid} 恢复失败: {e}")

    # 更新 seq（确保 next_id 不会重复）
    max_id = max((int(x.split("_")[1]) for x in registry._nodes
                  if "_" in x and x.split("_")[1].isdigit()), default=0)
    registry._seq = max(registry._seq, max_id)
    registry.save()

    print(f"✅ 已补回 {restored} 个节点，种群: {len(registry.active())}")
    print(f"   seq: {registry._seq}")
    # 技能统计
    from collections import Counter
    skills = Counter()
    for n in registry.active():
        pass  # skill 在 profile 目录，这里只统计 registry
    print("   活动节点分数分布:",
          sorted([round(n.score) for n in registry.active()], reverse=True)[:10])


if __name__ == "__main__":
    main()
