"""ADGS v3 达尔文进化引擎 — 两个力量 + 繁衍 + 淘汰

力量一：基础淘汰率（数量压力，S 型）
    P_base(n) = 1 / (1 + e^((K - n)/τ))
    K=50 承载量, τ=8。节点越多压力越大 → 淘汰数量 E = round(R + (n-K)*coef)

力量二：种类竞争（拥挤度，生态位压制）
    density_i = Σ_j exp(-(1-cos(v_i,v_j))/σ²)，个体淘汰概率 ∝ density

繁衍（每轮固定 R 个）
    亲代选择：按"生存压力小"加权随机（拥挤度低 → 更容易繁衍）
    基因重组(1-whiteboard_ratio)：2 亲代 → skill 并集随机抽 数量=平均值 → 无记忆
    白板(whiteboard_ratio)：无 skill 无记忆的全新节点
"""
import math
import random
from typing import Dict, List, Optional, Tuple

from config.config import config, PROJECT_ROOT
from core.registry import Node, Registry, registry as _registry
from utils import embedding as emb
from web.event_bus import bus

# ---------------- 力量一：基础淘汰率 ----------------

def base_pressure(n: int, K: int = None, tau: float = None) -> float:
    """S 型生存压力：n 越多压力越大。返回 0~1"""
    K = K or config.capacity
    tau = tau or config.tau
    return 1.0 / (1.0 + math.exp((K - n) / tau))


def elimination_count(n: int, capacity: float = None) -> int:
    """本轮淘汰数量：E = round(R + (n-K)*coef)，clamp [0, n]

    基于繁衍前的种群 n 计算（生存压力由当前种群决定）
    capacity: 动态承载量 K_eff（力量四）；None 用 config.capacity
    """
    R = config.reproduction_rate
    K = capacity if capacity is not None else config.capacity
    coef = config.recovery_coef
    E = int(R + (n - K) * coef + 0.5)  # 四舍五入（避免 banker's rounding）
    return max(0, min(n, E))


# ---------------- 力量二：种类竞争（拥挤度） ----------------

def _is_blank_node(node: Node) -> bool:
    """空节点判断：描述仍为初始统一文本（无任何任务经验分化）

    覆盖 initial 初始节点、从未做过任务的 recombine/whiteboard 节点。
    这类节点没有技能生态位，不应因"描述撞车"被优先淘汰。
    """
    text = (node.describe or "").strip()
    # 初始统一描述（main.py INIT_DESCRIBE / evolution _default_describe 同源文本）
    # 2026-08-30 P3 修复：用【相等】而非 startswith——recombine 子代描述 =
    # 初始文本 + 技能摘要（"通用智能体节点… 继承了技能基因：…"），以相同
    # 前缀开头但已分化，startswith 会误判为空节点导致压力被拉成均值。
    if text == _default_describe().strip():
        return True
    # 描述过短且无实际内容
    if len(text) < 15:
        return True
    return False


def crowding_scores(nodes: List[Node]) -> Dict[str, float]:
    """计算每个节点的拥挤度（按描述向量）

    空节点（描述未分化，见 _is_blank_node）：种类压力强制 = 所有节点压力的平均值。
    原因：空节点描述相同（初始统一文本），直接算会互相挤到最高位，
    但它们没有技能生态位，不应因"描述撞车"被优先淘汰。
    """
    if len(nodes) <= 1:
        return {n.id: 0.0 for n in nodes}
    try:
        vectors = [emb.get_embedding(n.describe) for n in nodes]
        densities = emb.crowding_density(vectors,
                                         sigma=config.crowding_sigma,
                                         threshold=config.sim_threshold)
        result = {n.id: d for n, d in zip(nodes, densities)}
        # 空节点：压力 = 平均值（不参与拥挤竞争）
        blank_ids = [n.id for n in nodes if _is_blank_node(n)]
        if blank_ids:
            avg = sum(result.values()) / len(result)
            for bid in blank_ids:
                result[bid] = avg
        return result
    except Exception as e:
        print(f"[evolution] 拥挤度计算失败（回退 0）: {e}")
        return {n.id: 0.0 for n in nodes}


def effective_capacity(nodes: List[Node], update_ema: bool = True) -> float:
    """力量四：承载量随种群平均分对数扩张（方案 A）

    K_eff = K_base × (1 + slope × ln(avg_score / K_base))，clamp [min, max]
      avg=50→50；avg=100→≈85；avg=300→≈140；对数渐近饱和（收益递减）。
    EMA 平滑跨轮防抖（仅 evolve 时 update_ema=True 更新一次；
    展示用 update_ema=False 纯计算，避免 api_state 高频调用漂移）。
    disabled 或 slope<=0 返回 config.capacity（旧行为）。
    """
    if not getattr(config, "capacity_growth_enabled", False) \
            or config.capacity_growth_slope <= 0:
        return float(config.capacity)
    if not nodes:
        return float(config.capacity)
    avg = sum(n.score for n in nodes) / len(nodes)
    K = config.capacity
    if avg <= 0:
        return float(K)
    k_new = K * (1.0 + config.capacity_growth_slope
                 * math.log(max(avg, 1.0) / K))
    k_new = max(config.capacity_min, min(config.capacity_max, k_new))
    ema = getattr(config, "_capacity_ema", None)
    if ema is None:
        ema = k_new
    else:
        ema = 0.3 * k_new + 0.7 * ema
    if update_ema:
        config._capacity_ema = ema
    return round(ema, 1)


def percentile_score(nodes: List[Node], q: float = 0.60) -> float:
    """当前种群 score 的 q 百分位（新节点动态初始分，力量四）

    新后代从种群偏上水平起步：比 q 比例的现有节点强，
    不拖累种群平均分（avg 信号不被稀释），也不会被秒淘汰。
    无节点时回退 50（中性分）。
    """
    if not nodes:
        return 50.0
    scores = sorted(n.score for n in nodes)
    idx = int(q * (len(scores) - 1))
    return scores[idx]


def elimination_probabilities(nodes: List[Node],
                              capacity: float = None) -> Dict[str, float]:
    """个体淘汰概率 = 力量一（基础生存压力）+ 力量二（拥挤度）+ 力量三（综合得分指数弱度）

    设计.txt:15 两大力量：
      力量一：P_base(n) = 1/(1+e^((K-n)/τ))，n 越多生存压力越大，是施加到
              【每个】节点的基础淘汰率 —— 保证种群膨胀时生态位独占节点
              也有越来越高的被淘汰概率（而非只有拥挤节点被清）。
      力量二：拥挤度 density_i，生态位重叠越多概率越高（影响系数
              penalty = config.crowding_penalty，0.5 = 减半）。
      力量三（2026-08-28 重做）：综合得分指数弱度 ——
              weight_i = (P_base + penalty × density_i) × 5^(−score_weight × S_i / 10)
              S_i = score_i × (1 − penalty × density_i)（综合得分，拥挤度已折算）。
              效果：综合得分每高 10 分，淘汰权重 ÷5（用户量化需求：
              60 分淘汰概率 = 50 分的 1/5）；penalty=0 时退化纯分数。
              score_weight=0 退化为纯拥挤度旧行为（5^0=1）。
    capacity: 动态承载量 K_eff（力量四）；None 时现算（不更新 EMA，纯展示用）。
    权重归一化为概率；全部权重 ≈ 0（n 很小且无人拥挤）时退回均匀分布。
    """
    if capacity is None:
        capacity = effective_capacity(nodes, update_ema=False)
    density = crowding_scores(nodes)
    P_base = base_pressure(len(nodes), K=capacity)
    fit = _fitness_scores(nodes, density)
    w = getattr(config, "elimination_score_weight", 0.0)
    pen = getattr(config, "crowding_penalty", 0.5)
    log_weights = {}
    for nid, d in density.items():
        # 综合得分越高淘汰权重越低：5^(−w·S/10)；w=0 时恒为 1
        pressure = P_base + pen * d
        log_weights[nid] = (math.log(pressure) - w * fit.get(nid, 0.0) * math.log(5) / 10
                            if pressure > 0 else -math.inf)
    maximum = max(log_weights.values(), default=-math.inf)
    if maximum == -math.inf:
        return {n.id: 1.0 / len(nodes) for n in nodes}
    weights = {nid: math.exp(value - maximum) for nid, value in log_weights.items()}
    total = sum(weights.values())
    # weights 的键已是节点 id（str）
    return {nid: w / total for nid, w in weights.items()}


# ---------------- 繁衍 ----------------

def _fitness_scores(nodes: List[Node], crowding: Dict[str, float]) -> Dict[str, float]:
    """综合得分（考虑拥挤度后）：S = score × (1 − penalty × 拥挤度)

    拥挤度 0 → S = 任务得分；拥挤度 1 → S = score × (1 − penalty)。
    penalty = config.crowding_penalty（默认 0.5 = 拥挤度影响减半）。
    同时驱动亲代选择（正向，越高越可能繁衍）与淘汰概率（反向，
    淘汰权重 ∝ 5^(−S/10)：综合得分每高 10 分，淘汰概率降为 1/5）。
    """
    p = getattr(config, "crowding_penalty", 0.5)
    return {n.id: max(0.0, n.score) * (1.0 - p * crowding.get(n.id, 0.0))
            for n in nodes}


def _pick_parents(nodes: List[Node], crowding: Dict[str, float],
                  rng: random.Random) -> Tuple[Node, Node]:
    """按综合得分指数加权随机选 2 个亲代（得分高、拥挤度低 → 权重高）

    自然选择：繁衍权重 = 5^(S/20)，综合得分每高 20 分繁衍概率 ×5
    （10 分 ≈ 2.2 倍）—— 比淘汰（每 10 分 ÷5）温和，避免纯精英化
    垄断多样性；S=0 时权重=1（最差节点仍有微小繁衍概率）。
    """
    if len(nodes) == 1:
        return nodes[0], nodes[0]
    fit = _fitness_scores(nodes, crowding)
    peak = max(fit.values(), default=0.0)
    weights = [math.exp((fit.get(n.id, 0.0) - peak) * math.log(5.0) / 20.0) for n in nodes]
    total = sum(weights)
    probs = [w / total for w in weights]
    p1 = rng.choices(nodes, weights=probs, k=1)[0]
    # 第二个亲代：允许同一节点但降低概率
    probs2 = [w if n.id != p1.id else w * 0.1 for n, w in zip(nodes, probs)]
    t2 = sum(probs2)
    probs2 = [w / t2 for w in probs2]
    p2 = rng.choices(nodes, weights=probs2, k=1)[0]
    return p1, p2


def _skill_union(parents: List[Node]) -> Dict[str, dict]:
    """收集亲代的技能（技能名 → {desc, content}，完整基因继承）

    content 保存完整技能包路径与正文；无法读取的基因会使出生失败，
    不能以占位描述伪装完整继承。
    """
    union: Dict[str, dict] = {}
    for p in parents:
        try:
            from core.hermes import HermesInstance
            skills = HermesInstance.list_skills(p.id)
            for name, desc in skills.items():
                if name not in union:
                    union[name] = {"desc": desc, "content": None}
                try:
                    content = HermesInstance.get_skill_package(p.id, name)
                    if content and not union[name].get("content"):
                        union[name]["content"] = content
                except Exception:
                    pass
        except Exception:
            pass
    return union


def plan_newborns(nodes: List[Node], rng: random.Random = None) -> List[dict]:
    """制定本轮繁衍计划（不落盘，由外部执行）

    返回 [{birth_type, parents, skill_count}]，长度 = reproduction_rate
    """
    rng = rng or random.Random()
    R = config.reproduction_rate
    wb_ratio = config.whiteboard_ratio
    crowding = crowding_scores(nodes)

    plans = []
    for _ in range(R):
        if rng.random() < wb_ratio:
            plans.append({"birth_type": "whiteboard", "parents": [],
                          "skill_count": 0})
        else:
            p1, p2 = _pick_parents(nodes, crowding, rng)
            skills1 = _skill_union([p1])
            skills2 = _skill_union([p2])
            union = {**skills1, **skills2}  # 并集（name → {desc, content}）
            avg = max(1, int(round((len(skills1) + len(skills2)) / 2)))
            # 从并集中抽 avg 个（不足则全取）
            chosen = rng.sample(list(union.keys()), min(avg, len(union)))
            plans.append({
                "birth_type": "recombine",
                "parents": [p1.id, p2.id] if p1.id != p2.id else [p1.id],
                "skill_count": len(chosen),
                "skills": [{"name": n, **union[n]} for n in chosen],
            })
    return plans


# ---------------- 淘汰 ----------------

def select_eliminations(nodes: List[Node], count: int,
                        rng: random.Random = None,
                        capacity: float = None) -> List[Node]:
    """按个体淘汰概率（拥挤度归一化）选 count 个淘汰"""
    rng = rng or random.Random()
    if count <= 0:
        return []
    probs = elimination_probabilities(nodes, capacity=capacity)
    weights = [probs[n.id] for n in nodes]
    chosen = rng.choices(nodes, weights=weights, k=count)
    # 去重：可能抽中同一节点多次，保证不同节点
    seen = set()
    result = []
    for c in chosen:
        if c.id not in seen:
            seen.add(c.id)
            result.append(c)
    # 不足 count 再按权重补
    while len(result) < count:
        rest = [n for n in nodes if n.id not in seen]
        if not rest:
            break
        remaining_probs = elimination_probabilities(rest, capacity=capacity)
        rw = [remaining_probs[n.id] for n in rest]
        c = rng.choices(rest, weights=rw, k=1)[0]
        seen.add(c.id)
        result.append(c)
    return result[:count]


# ---------------- 完整进化一轮 ----------------

def evolve(reg: Registry = None, dry_run: bool = False) -> dict:
    """每轮新对话开始时调用：繁衍 → 淘汰 → 落盘

    返回进化报告 dict（节点数变化、出生/淘汰明细）
    """
    reg = reg or _registry
    rng = random.Random()
    nodes = reg.active()

    # 空池自愈（2026-08-30 P0 修复）：web 首启没有 ensure_initial、或节点被
    # 清空/淘汰光后，plan_newborns → _pick_parents 对空列表 rng.choices 抛
    # IndexError，进化线程崩溃 → 之后所有任务"无可用节点"。先重建初始种群。
    if not nodes and not dry_run:
        try:
            reg.ensure_initial(_default_describe())
        except Exception:
            pass
        nodes = reg.active()
        if not nodes:
            return {"n_before": 0, "pressure": 0.0, "effective_capacity": 0,
                    "elimination_target": 0, "births": [], "eliminations": [],
                    "n_after": 0,
                    "note": "种群为空且初始化失败（检查 profile 创建）"}

    # 力量四：动态承载量（EMA 每轮更新一次）+ 新节点动态初始分
    if not nodes:
        return {"n_before": 0, "n_after": 0, "pressure": 0, "effective_capacity": 0,
                "elimination_target": 0, "births": [], "eliminations": [], "note": "空池预览不创建节点"}
    K_eff = effective_capacity(nodes, update_ema=not dry_run)
    nb_score = percentile_score(nodes, config.newborn_score_percentile)

    report = {
        "n_before": len(nodes),
        "pressure": round(base_pressure(len(nodes), K=K_eff), 4),
        "effective_capacity": K_eff,
        "elimination_target": elimination_count(len(nodes), capacity=K_eff),
        "births": [],
        "eliminations": [],
        "n_after": None,
    }

    # 1) 繁衍计划
    plans = plan_newborns(nodes, rng)
    newborn_ids = set()
    for plan_index, p in enumerate(plans, 1):
        node_id = (f"ags_{getattr(reg, '_seq', 0) + plan_index}" if dry_run else reg.next_id())
        newborn_ids.add(node_id)
        if p["birth_type"] == "whiteboard":
            desc = _default_describe()
            new_node = Node(node_id, desc, gen=0, birth_type="whiteboard",
                            score=nb_score)
        else:
            # 重组：描述 = 亲代描述融合（简化：取第一个亲代描述 + 技能摘要）
            p1 = reg.get(p["parents"][0])
            desc = _recombine_describe(p1, p["skills"])
            new_node = Node(node_id, desc, gen=(p1.gen + 1 if p1 else 1),
                            birth_type="recombine", parents=p["parents"],
                            score=nb_score)
        if not dry_run:
            # 实际创建 hermes profile 并安装 skill
            try:
                from core.hermes import HermesInstance
                if not HermesInstance.create(node_id):
                    raise RuntimeError("profile 创建失败")
                ready = HermesInstance.setup_newborn(node_id, {"models": {
                    "hermes_default": {
                        "exec_model": config.worker_model,
                        "base_url": config.worker_url,
                        "api_key": config.worker_key,
                        "context_length": config.worker_ctx_window,
                    }}})
                if ready is False:
                    raise RuntimeError("profile 配置失败")
                # 默认技能：anysearch（新节点自带联网搜索能力，含完整 API 文档）
                try:
                    _anysearch_src = (PROJECT_ROOT / "skills" / "anysearch" / "SKILL.md")
                    _anysearch_content = (_anysearch_src.read_text(encoding="utf-8")
                                          if _anysearch_src.exists() else None)
                except Exception:
                    _anysearch_content = None
                if not HermesInstance.install_skill(
                    node_id, "anysearch",
                    "通用网络搜索工具（anysearch API）。任务需要查询最新信息、文档、代码示例、教程、新闻或任何实时网络内容时使用。支持 tag（文档/代码/新闻/学术等）和 params（按库/框架过滤）过滤。无认证即可用。",
                    content=_anysearch_content):
                    raise RuntimeError("默认技能安装失败")
                # 基因技能：繁衍继承（跳过 anysearch —— 它是默认技能，
                # 上面已装完整版；避免用继承的简短 desc 覆盖）
                for sk in p.get("skills", []):
                    if sk["name"] == "anysearch":
                        continue
                    if not sk.get("content"):
                        raise RuntimeError(f"技能 {sk['name']} 完整基因不可读取")
                    if not HermesInstance.install_skill(
                        node_id, sk["name"], sk.get("desc", ""),
                        content=sk.get("content")):
                        raise RuntimeError(f"技能 {sk['name']} 继承失败")
                reg.add(new_node)
            except Exception as e:
                print(f"[evolution] 创建节点 {node_id} profile 失败: {e}")
                report.setdefault("birth_errors", []).append({"id": node_id, "error": str(e)})
                continue
        report["births"].append({
            "id": node_id, "type": p["birth_type"], "parents": p["parents"],
            "skills": [s["name"] for s in p.get("skills", [])],
            "score": nb_score,
        })

    # 2) 淘汰（生存压力基于繁衍前的种群 n，先繁衍后淘汰的时序不变）
    #    排除本轮新生儿：刚出生的子代（尤其 recombine 描述继承亲代、拥挤度高）
    #    若允许当轮就被淘汰，基因没有机会表达即被清除
    E = elimination_count(len(nodes), capacity=K_eff)
    nodes_now = [n for n in reg.active() if n.id not in newborn_ids]
    elims = select_eliminations(nodes_now, E, rng, capacity=K_eff)
    for node in elims:
        if not dry_run:
            reg.archive_node(node.id)
        report["eliminations"].append({"id": node.id, "describe": node.describe[:50]})

    report["n_after"] = (len(nodes) + len(report["births"]) - len(elims)) if dry_run else len(reg.active())
    # 淘汰质量指标（方案 B 可见性）：淘汰均分 vs 种群均分。
    # 弱者优先淘汰时 elim_avg < pop_avg（score_gap 为负）→ 存活者平均分上升。
    if nodes:
        report["pop_avg_score"] = round(sum(n.score for n in nodes) / len(nodes), 2)
        report["elim_avg_score"] = round(
            sum(n.score for n in elims) / len(elims), 2) if elims else report["pop_avg_score"]
        report["score_gap"] = round(report["elim_avg_score"] - report["pop_avg_score"], 2)
    # Web 前端：进化轮次事件
    if not dry_run:
        bus.emit("evolve.round", n_before=report["n_before"],
             n_after=report["n_after"], pressure=report["pressure"],
             effective_capacity=report.get("effective_capacity"),
             elimination_target=report["elimination_target"],
             births=[{"id": b["id"], "type": b["type"],
                      "score": b.get("score")} for b in report["births"]],
             eliminations=[{"id": e["id"]} for e in report["eliminations"]],
             pop_avg_score=report.get("pop_avg_score"),
             elim_avg_score=report.get("elim_avg_score"),
             score_gap=report.get("score_gap"))
    return report


def _default_describe() -> str:
    """初始统一描述 / 白板描述"""
    from core.registry import GENERALIST_DESCRIPTION
    return GENERALIST_DESCRIPTION


def _recombine_describe(parent: Optional[Node], skills: list) -> str:
    """重组子代描述：亲代描述 + 继承技能摘要

    2026-08-30 修复（P1）：技能名去重保序（此前同一列表拼接两遍，语义
    重心被稀释、描述膨胀到 800+ 字）；总长度按 describe_max_len 截断
    （README 承诺描述 ≤500 字）。
    """
    base = parent.describe if parent else _default_describe()
    if not skills:
        return base[:config.describe_max_len]
    seen, names = set(), []
    for s in skills:
        name = (s.get("name") if isinstance(s, dict) else str(s)) or ""
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    suffix = f" 继承了技能基因：{'、'.join(names)}。"
    return (base + suffix)[:config.describe_max_len]


if __name__ == "__main__":
    # 纯函数自测（dry_run 不落盘）
    import tempfile
    from pathlib import Path
    from core.registry import Registry
    tmp_reg = Registry(path=Path(tempfile.mktemp(suffix=".json")))
    for i in range(10):
        tmp_reg.add(Node(f"ags_{i+1}", _default_describe()))
    print("繁衍计划样本:", plan_newborns(tmp_reg.active()))
    print("基础压力 n=10:", round(base_pressure(10), 4),
          "n=50:", round(base_pressure(50), 4),
          "n=70:", round(base_pressure(70), 4))
    print("淘汰数 n=10:", elimination_count(10),
          "n=50:", elimination_count(50),
          "n=60:", elimination_count(60))
    print("拥挤度:", crowding_scores(tmp_reg.active()))
    # 力量一验证：n 增大 → 基础压力上升 → 独占节点（density=0）淘汰概率上升
    for n in (10, 30, 50, 70):
        print(f"n={n:3d} P_base={base_pressure(n):.4f}")
    print("力量一叠加验证: elimination_probabilities 含基础压力:")
    for i in range(5):
        tmp_reg.add(Node(f"ags_{i+11}", "精通 Python 后端开发与 Flask"))
    print("  权重样例:", {k: round(v, 4) for k, v in
          list(elimination_probabilities(tmp_reg.active()).items())[:3]})
