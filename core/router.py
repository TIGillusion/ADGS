"""Task fit first; generalists cover unfamiliar work, quality breaks close ties."""
import math
import random
from typing import Dict, List, Optional, Tuple

from config.config import config
from core.registry import GENERALIST_DESCRIPTION, Node, Registry, registry as _registry
from core.contracts import task_section
from utils import embedding as emb


# Policy baseline, not a claim that cosine .5 means a task is understood.
GENERALIST_FIT = 0.50
FIT_WINDOW = 0.08
SPECIALIST_MARGIN = 0.05


def routing_query(task_desc: str) -> str:
    """Put this task before long inherited background / permission boilerplate."""
    current = next((text for title in ('当前任务','任务目标','本次任务','目标','子任务')
                    if (text := task_section(task_desc, title))), '')
    if not current:
        return str(task_desc or "").strip()
    sections = [current[:1000]]
    for title in ("需求明细", "验收标准"):
        text = task_section(task_desc, title)
        if text:
            sections.append(text[:500])
    return "\n".join(sections)


def is_generalist(node: Node) -> bool:
    text = (node.describe or "").strip()
    return not text or text in {GENERALIST_DESCRIPTION, "通用智能体节点", "通用智能体", "初始节点，待分化"}


def _unit(value, default=0.0):
    try:
        value = float(value)
        return max(0.0, min(1.0, value)) if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def explore_boost(node: Node, count: int) -> float:
    beta = max(0.0, config.router_explore_beta)
    done = max(0, int(getattr(node, "tasks_done", 0) or 0))
    # Exploration cannot grow without bound as the population grows.
    return 1.0 + min(beta, beta * math.sqrt(math.log(1 + count)) / (1 + done))


def routing_details(task_desc: str, nodes: List[Node]) -> Dict[str, dict]:
    query = routing_query(task_desc)
    task_vec = None
    if query:
        try:
            task_vec = emb.get_embedding(query, query=True)
        except Exception as exc:
            print(f"[router] 任务向量化不可用，使用中性适配与有限质量修正: {type(exc).__name__}")
    result = {}
    for node in nodes:
        similarity = None
        if task_vec is not None:
            try:
                similarity = _unit(emb.cosine_sim(task_vec, emb.get_embedding(node.describe)))
            except Exception:
                pass
        generalist = is_generalist(node)
        relevance = (GENERALIST_FIT if task_vec is None else similarity or 0.0)
        if generalist:
            relevance = max(GENERALIST_FIT, relevance)
        # A perfect historical record is at most 2**score_weight better than
        # zero successes, so an old score cannot buy unrelated work.
        quality = 0.5 + 0.5 * _unit(getattr(node, "success_rate", 0.5), 0.5)
        boost = explore_boost(node, len(nodes))
        score = relevance ** config.router_sim_weight * quality ** config.router_score_weight * boost
        result[node.id] = dict(similarity=similarity, relevance=relevance, generalist=generalist,
                               quality=quality, explore_boost=boost, score=score,
                               embedding_available=task_vec is not None)
    return result


def score_nodes(task_desc: str, nodes: List[Node]) -> Dict[str, float]:
    return {key: row["score"] for key, row in routing_details(task_desc, nodes).items()}


def ranked_candidates(task_desc: str, nodes: List[Node], exclude: set = None) -> List[Tuple[Node, dict]]:
    candidates = [n for n in nodes if n.id not in (exclude or set())]
    details = routing_details(task_desc, candidates)
    if not candidates:
        return []
    if any(row["embedding_available"] for row in details.values()):
        general = [n for n in candidates if details[n.id]["generalist"]]
        baseline = max((details[n.id]["relevance"] for n in general), default=0.0)
        specialist = max((details[n.id]["relevance"] for n in candidates if not details[n.id]["generalist"]), default=0.0)
        if general and specialist < baseline:
            candidates = general
        elif general and specialist >= baseline + SPECIALIST_MARGIN:
            # Several identical generalists must not collectively dilute a
            # clearly matched specialist just because top-k has spare places.
            candidates = [n for n in candidates if not details[n.id]['generalist']]
        best = max(details[n.id]["relevance"] for n in candidates)
        candidates = [n for n in candidates if details[n.id]["relevance"] >= best - FIT_WINDOW]
    return sorted(((n, details[n.id]) for n in candidates), key=lambda pair: pair[1]["score"], reverse=True)


def top_k_nodes(task_desc: str, nodes: List[Node], k: int = None,
                exclude: set = None) -> List[Tuple[Node, float]]:
    """按综合得分取前 k（排除指定节点）"""
    k = k or config.top_k
    return [(n, row["score"]) for n, row in ranked_candidates(task_desc, nodes, exclude)[:k]]


def temperature_select(task_desc: str, nodes: List[Node],
                       temperature: float = None, exclude: set = None,
                       rng: random.Random = None) -> Optional[Node]:
    """从 top-k 中按温度采样选一个节点执行任务"""
    temperature = temperature if temperature is not None else config.temperature
    rng = rng or random.Random()
    ranked = top_k_nodes(task_desc, nodes, exclude=exclude)
    if not ranked:
        return None
    if len(ranked) == 1 or temperature <= 0:
        return ranked[0][0]

    return _sample_ranked(ranked, temperature, rng)


def _sample_ranked(ranked, temperature, rng):
    if len(ranked) == 1 or temperature <= 0:
        return ranked[0][0]
    # softmax(log(score)/T): invariant to the absolute scale of the scores.
    scores = [s for _, s in ranked]
    t = max(temperature, 1e-6)
    scaled = [math.log(max(s, 1e-300)) / t for s in scores]
    m = max(scaled)
    exps = [math.exp(x - m) for x in scaled]  # 数值稳定
    total = sum(exps)
    probs = [e / total for e in exps]

    chosen = rng.choices(ranked, weights=probs, k=1)[0][0]
    return chosen


def route(task_desc: str, reg: Registry = None,
          temperature: float = None, exclude: set = None,
          verbose: bool = True) -> Optional[Node]:
    """路由入口：返回选中的节点（含 top-k 明细打印）"""
    reg = reg or _registry
    nodes = reg.active()
    if not nodes:
        return None
    details = ranked_candidates(task_desc, nodes, exclude)[:config.top_k]
    ranked = [(n, row['score']) for n, row in details]
    if verbose and ranked:
        print("[router] 任务适配候选（累计分不参与路由）:")
        for n, row in details:
            similarity = f"{row['similarity']:.3f}" if row['similarity'] is not None else 'unknown'
            print(f"  {n.id:8s} fit={row['relevance']:.3f} sim={similarity} "
                  f"route_score={row['score']:.4f} generalist={row['generalist']}  {n.describe[:40]}")
    return _sample_ranked(ranked, config.temperature if temperature is None else temperature, random.Random()) if ranked else None


def resolve_stages(subtasks: list) -> List[List[int]]:
    """Kahn 拓扑排序：按 depends_on 依赖分组，组内并行、组间串行。

    subtasks: 子任务 dict 列表（每个可含 depends_on，0 基索引）。
    返回 [[idx, idx, ...], ...]，每个内层列表是一个可并行的 stage。
    环依赖（自指/成环）会被过滤（越界索引），残余丢到最后 stage。
    """
    n = len(subtasks)
    deps = [set(s.get("depends_on", []) or []) for s in subtasks]
    # 过滤越界依赖（0 <= d < n），避免 IndexError
    for i in range(n):
        deps[i] = {d for d in deps[i] if isinstance(d, int) and 0 <= d < n}
    indeg = [len(d) for d in deps]
    children = [[] for _ in range(n)]
    for i in range(n):
        for d in deps[i]:
            children[d].append(i)
    stages: List[List[int]] = []
    ready = [i for i in range(n) if indeg[i] == 0]
    done = set()
    while ready:
        stage = [i for i in ready]
        stages.append(stage)
        next_ready = []
        for i in stage:
            done.add(i)
            for c in children[i]:
                indeg[c] -= 1
                if indeg[c] == 0 and c not in done:
                    next_ready.append(c)
        ready = [i for i in next_ready if i not in done]
    # 环检测：没进 stages 的（成环依赖）丢到最后 stage 串行处理
    if len(done) < n:
        leftover = [i for i in range(n) if i not in done]
        stages.append(leftover)
    return stages


if __name__ == "__main__":
    # 自测：构造 3 个不同描述的节点，看路由偏向
    import tempfile
    from pathlib import Path
    from core.registry import Registry
    tmp = Registry(path=Path(tempfile.mktemp(suffix=".json")))
    n1 = Node("ags_1", "精通 Python 后端开发与 Flask Web 框架")
    n1.success_rate = 0.9
    n2 = Node("ags_2", "擅长前端 React 与 CSS 布局")
    n2.success_rate = 0.7
    n3 = Node("ags_3", "精通 Python 后端开发与 API 设计")
    n3.success_rate = 0.3
    for n in (n1, n2, n3):
        tmp.add(n)
    print(">>> 任务: 用 Python 写一个 Flask 后端接口")
    selected = route("用 Python 写一个 Flask 后端接口", reg=tmp, temperature=0.1)
    print(">>> 选中:", selected.id if selected else None)
