"""生态位竞争验证 — 拥挤度淘汰是否优先清除技能相似的节点

验证点：
1. 计算所有活动节点描述向量的两两相似度 + 拥挤度密度
2. 检查"技能相同/相似"的节点是否拥挤度更高（生态位重叠）
3. 模拟淘汰：被淘汰的节点是否集中在拥挤区域
4. 淘汰后技能多样性是否保留（独特技能节点存活）
"""
import sys
import random
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.registry import registry
from core.hermes import HermesInstance
from core.evolution import (crowding_scores, elimination_probabilities,
                            select_eliminations, elimination_count,
                            base_pressure)
from utils import embedding as emb
import numpy as np

rng = random.Random(42)
nodes = registry.active()
print(f"活动节点: {len(nodes)}")

# ============ 1. 节点技能画像 ============
print("\n════ 1. 节点技能画像 ════")
skill_map = {}   # node_id -> [skills]
for n in nodes:
    sk = HermesInstance.list_skills(n.id)
    skill_map[n.id] = list(sk.keys())

# 技能持有统计
skill_holders = {}
for nid, skills in skill_map.items():
    for s in skills:
        skill_holders.setdefault(s, []).append(nid)

print("技能分布（持有节点数 ≥2 的基因）:")
for s, holders in sorted(skill_holders.items(), key=lambda x: -len(x[1])):
    if len(holders) >= 2:
        print(f"  {s}: {len(holders)} 节点 {holders[:6]}")
print(f"  （共 {len(skill_holders)} 种基因，{len([s for s,h in skill_holders.items() if len(h)>=2])} 种有重复持有）")

# ============ 2. 拥挤度计算 ============
print("\n════ 2. 拥挤度密度（生态位重叠度）════")
density = crowding_scores(nodes)
ranked = sorted(nodes, key=lambda n: density[n.id], reverse=True)

print("最拥挤 TOP10（生态位重叠最严重）:")
for n in ranked[:10]:
    print(f"  {n.id:8s} density={density[n.id]:7.2f} | 技能={skill_map[n.id][:4]} | {n.describe[:45]}")
print("\n最独特 TOP10（生态位最独立）:")
for n in ranked[-10:]:
    print(f"  {n.id:8s} density={density[n.id]:7.2f} | 技能={skill_map[n.id][:4]} | {n.describe[:45]}")

# ============ 3. 模拟淘汰 ============
print("\n════ 3. 模拟淘汰（当前压力）════")
n = len(nodes)
pressure = base_pressure(n)
E = elimination_count(n)
print(f"种群 {n} | 基础压力={pressure:.4f} | 本轮淘汰目标 E={E}")

probs = elimination_probabilities(nodes)
elims = select_eliminations(nodes, E, rng)
elim_ids = {e.id for e in elims}
surv_ids = {x.id for x in nodes} - elim_ids

print(f"\n被淘汰 {len(elims)} 个节点:")
for e in elims:
    print(f"  {e.id:8s} p={probs[e.id]:.4f} density={density[e.id]:7.2f} | 技能={skill_map[e.id][:4]}")

# ============ 4. 验证：淘汰是否偏向拥挤区 ============
print("\n════ 4. 生态位竞争验证 ════")
elim_density = [density[e.id] for e in elims]
surv_density = [density[x.id] for x in nodes if x.id in surv_ids]
print(f"被淘汰节点平均拥挤度: {np.mean(elim_density):.2f} (n={len(elim_density)})")
print(f"存活节点平均拥挤度:   {np.mean(surv_density):.2f} (n={len(surv_density)})")
if np.mean(elim_density) > np.mean(surv_density):
    print("✅ 淘汰偏向拥挤区 —— 生态位竞争生效")
else:
    print("⚠️ 淘汰未明显偏向拥挤区")

# 5. 技能多样性保留验证
print("\n════ 5. 淘汰前后技能多样性 ════")
def skill_set(ids):
    s = set()
    for i in ids:
        s.update(skill_map.get(i, []))
    return s

before_skills = skill_set([x.id for x in nodes])
after_skills = skill_set(surv_ids)
print(f"淘汰前技能种数: {len(before_skills)}")
print(f"淘汰后技能种数: {len(after_skills)}")
lost = before_skills - after_skills
print(f"丢失的技能: {list(lost) if lost else '无（独特技能全部保留）'}")

# 6. 同技能节点淘汰率对比
print("\n════ 6. 同技能 vs 独特技能节点的淘汰率 ════")
for s, holders in sorted(skill_holders.items(), key=lambda x: -len(x[1])):
    if len(holders) < 2:
        continue
    elim_in = [h for h in holders if h in elim_ids]
    rate = len(elim_in) / len(holders)
    print(f"  {s:40s} 持有{len(holders)} 淘汰{len(elim_in)} ({rate:.0%})")
