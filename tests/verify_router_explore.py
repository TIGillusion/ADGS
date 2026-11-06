"""历史探索公式模拟（非当前候选策略）；现行验证运行 verify_router_relevance.py。

路由探索机制验证：模拟连续派发，对比任务分布（富者愈富 vs 冷启动成长）

旧: base = sim×sr，纯利用 → 高分节点垄断 top-k
新: base = sim^1.5 × sr^0.5 × (1 + β×√ln(1+N)/(1+tasks_done))

模拟逻辑（对齐真实派发流程 ceo.py _dispatch_parallel → route → work）：
  - 每轮按 route 同款的 score_nodes 排序取 top-1 派发（temperature 低时实际
    也基本选 top-1；此处取 top-1 是最保守/最垄断的假设）
  - 派发到某节点后 tasks_done += 1（成功与否都算接过任务，对应 node.py:757）
  - 50 轮后统计：多少节点接到过任务、最多/最少任务数、冷启动节点（初始
    tasks_done=0）接到的任务占比

用法: .venv/Scripts/python.exe tests/verify_router_explore.py
"""
import os, sys, copy, json
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.registry import Registry, Node
from utils.embedding import get_embedding, cosine_sim

REG = Registry()
nodes = REG.active()
print(f"活动节点: {len(nodes)} 个")

# 子任务序列：贴近真实拆分产物（同一项目的大方向，节点 describe 大多沾边）
TASKS = [
    "用 Python 重构后端数据链路，做生产级 API 标准化并写单元测试",
    "开发 Flask Web 管理界面，暗色主题，含实时日志流展示",
    "写 CSV 数据清洗脚本，处理缺失值与异常值并输出报告",
    "在 Windows 环境排查 Python 生态底层缺陷并定位根因",
    "为项目搭建 CI 流水线并补充回归测试",
]

def base_scores(task_vec_cache, nodes_list, w_sim, w_sr, beta, task_vec):
    """复刻 score_nodes 的打分逻辑（独立实现便于传 beta 对比）"""
    out = {}
    for n in nodes_list:
        try:
            sim = cosine_sim(task_vec, get_embedding(n.describe))
        except Exception:
            sim = 0.0
        sim = max(0.0, min(1.0, sim))
        sr = max(0.0, min(1.0, getattr(n, "success_rate", 0.5)))
        base = (sim ** w_sim) * (sr ** w_sr)
        if beta > 0:
            done = max(0, int(getattr(n, "tasks_done", 0) or 0))
            import math
            base *= 1.0 + beta * math.sqrt(math.log(1 + len(nodes_list))) / (1 + done)
        out[n.id] = base
    return out

def simulate(nodes0, w_sim, w_sr, beta, rounds=50):
    """连续派发 rounds 轮，返回任务分布统计。返回深拷贝，不污染真实 registry。"""
    nodes = [copy.copy(n) for n in nodes0]
    # 预生成任务向量
    task_vecs = [get_embedding(t, query=True) for t in TASKS]
    counter = Counter()
    for r in range(rounds):
        task_vec = task_vecs[r % len(TASKS)]
        scores = base_scores(None, nodes, w_sim, w_sr, beta, task_vec)
        top1 = max(nodes, key=lambda n: scores[n.id])
        top1.tasks_done += 1
        counter[top1.id] += 1
    return counter

# 真实配置值
from config.config import config
print(f"配置: sim_w={config.router_sim_weight} sr_w={config.router_score_weight} beta={config.router_explore_beta}\n")

cold_ids = {n.id for n in nodes if n.tasks_done == 0}
print(f"初始冷启动节点（tasks_done=0）: {len(cold_ids)} 个")

ROUND = 50
old = simulate(nodes, 1.0, 1.0, 0.0, ROUND)        # 旧公式纯利用
new = simulate(nodes, config.router_sim_weight, config.router_score_weight, config.router_explore_beta, ROUND)

def report(name, counter):
    total = sum(counter.values())
    covered = len(counter)
    cold_hits = sum(c for i, c in counter.items() if i in cold_ids)
    top3 = counter.most_common(3)
    print(f"\n[{name}] {ROUND} 轮派发:")
    print(f"  覆盖节点数: {covered}/{len(nodes)}")
    print(f"  冷启动节点接到的任务: {cold_hits} ({cold_hits/total*100:.0f}%)")
    print(f"  前3名: " + ", ".join(f"{k}={v}" for k, v in top3))
    print(f"  单节点最大占比: {max(counter.values())/total*100:.0f}%")

report("旧 sim×sr 纯利用", old)
report("新 sim^1.5×sr^0.5 + PUCT探索", new)

# 成长曲线：新公式下冷启动节点前 10 轮 vs 后 10 轮的接单数
def growth_curve(nodes0, w_sim, w_sr, beta, rounds=50):
    nodes = [copy.copy(n) for n in nodes0]
    task_vecs = [get_embedding(t, query=True) for t in TASKS]
    cold_first = Counter(); cold_last = Counter()
    for r in range(rounds):
        tv = task_vecs[r % len(TASKS)]
        scores = base_scores(None, nodes, w_sim, w_sr, beta, tv)
        top1 = max(nodes, key=lambda n: scores[n.id])
        if top1.id in cold_ids and (top1.tasks_done == 0 and r < 10):
            cold_first[top1.id] += 1
        if top1.id in cold_ids and r >= 40:
            cold_last[top1.id] += 1
        top1.tasks_done += 1
    return cold_first, cold_last

cf, cl = growth_curve(nodes, config.router_sim_weight, config.router_score_weight, config.router_explore_beta)
print(f"\n成长曲线（新公式，冷启动节点）: 前10轮接到 {sum(cf.values())} 个 | 后10轮接到 {sum(cl.values())} 个")
print("[verify] 完成")
