"""历史幂次公式对照（非当前候选策略）；现行验证运行 verify_router_relevance.py。

路由权重调整验证：真实 registry + 本地 bge 嵌入，对比新旧公式的 top-k

旧: score = sim × sr                     （sim、sr 等权）
新: score = sim^1.5 × sr^0.5             （专精主导，分数次级）

用法: .venv/Scripts/python.exe tests/verify_router_weights.py
"""
import os, sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.registry import Registry, Node
from utils.embedding import get_embedding, cosine_sim
from config.config import config

REG = Registry()
nodes = REG.active()
print(f"活动节点: {len(nodes)} 个")

# 选取几个真实任务描述（贴近用户日常任务）
TASKS = [
    "在 Windows 环境下用 Python 重构后端数据链路，要求生产级 API 标准化与单元测试",
    "开发一个 Flask Web 管理界面，暗色主题，含实时日志流展示",
    "写一个 CSV 数据清洗脚本，处理缺失值与异常值并输出报告",
]

def rank(task, w_sim, w_sr, topk=5):
    tvec = get_embedding(task, query=True)
    rows = []
    for n in nodes:
        try:
            sim = cosine_sim(tvec, get_embedding(n.describe))
        except Exception:
            sim = 0.0
        sim = max(0.0, min(1.0, sim))
        sr = max(0.0, min(1.0, getattr(n, "success_rate", 0.5)))
        rows.append((n.id, sim, sr, (sim ** w_sim) * (sr ** w_sr)))
    rows.sort(key=lambda r: r[3], reverse=True)
    return rows[:topk]

ok = True
for task in TASKS:
    old = rank(task, 1.0, 1.0)
    new = rank(task, config.router_sim_weight, config.router_score_weight)
    print(f"\n任务: {task[:40]}...")
    print(f"  {'节点':8s} {'sim':>7s} {'sr':>7s} | {'旧分':>8s} {'新分':>8s} | 旧排名→新排名")
    # 排名映射
    old_rank = {r[0]: i + 1 for i, r in enumerate(rank(task, 1.0, 1.0, topk=20))}
    new_rank = {r[0]: i + 1 for i, r in enumerate(new, 0)}
    # 按新分展示，附旧排名
    for i, (nid, sim, sr, new_s) in enumerate(new):
        old_s = sim * sr
        orank = old_rank.get(nid, ">20")
        print(f"  {nid:8s} {sim:7.3f} {sr:7.3f} | {old_s:8.4f} {new_s:8.4f} | {str(orank):>4s}→{i+1}")

# 关键检查：新旧 top-1 是否更偏向"高 sim"节点
print("\n=== 结论 ===")
for task in TASKS:
    old1 = rank(task, 1.0, 1.0)[0]
    new1 = rank(task, config.router_sim_weight, config.router_score_weight)[0]
    print(f"任务: {task[:30]}...")
    print(f"  旧 top-1: {old1[0]}  sim={old1[1]:.3f} sr={old1[2]:.3f}")
    print(f"  新 top-1: {new1[0]}  sim={new1[1]:.3f} sr={new1[2]:.3f}")

print("\n[verify] 完成（权重生效值 sim_w=%.1f sr_w=%.1f）"
      % (config.router_sim_weight, config.router_score_weight))
