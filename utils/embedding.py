"""ADGS v3 向量化与相似度 — 基于本地 bge-small-zh-v1.5（复用原 demo 改造）

职责：
  - 文本 → 向量（任务描述 / 节点描述）
  - 余弦相似度
  - 拥挤度密度计算（种类竞争用）

模型策略（懒加载）：
  - 首次调用 get_embedding() 时才下载（约 92MB），下载到项目根 ./models/
  - 缓存目录固定为项目根下 models/（绝对路径，与启动时 cwd 无关）
  - 下载默认走 hf-mirror 国内镜像（用户显式设过 HF_ENDPOINT 则尊重）
"""
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import List, Optional

import numpy as np

# 延迟加载：模型 92MB，首次调用才载入
_embedder = None
_embedder_lock = threading.Lock()
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："

# 向量缓存：节点描述在多次路由/拥挤度计算中会重复向量化，任务描述在同一轮
# 路由里也会被算多遍（route 打印 top-k 一次 + temperature_select 又算一次）。
# 按 (query, text) 缓存，LRU 上限防止任务描述无限累积。
# 线程安全：递归委派时多个子任务并行 route 会并发访问缓存，dict 操作需加锁
# （encode 在锁外执行，锁内只做 LRU 字典操作）。
_embed_cache = OrderedDict()
_embed_cache_lock = threading.Lock()
_EMBED_CACHE_MAX = 2048

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
# 固定相对项目根：模型下载/缓存的唯一位置，从任何 cwd 启动都不会散落到别处
CACHE_FOLDER = str(Path(__file__).resolve().parent.parent / "models")

# 网络策略（关键）：
#   - 本地缓存已存在 → 强制离线加载。否则 huggingface_hub 每次加载都会联网做
#     HEAD 检查，hf-mirror / huggingface.co 被墙或超时会导致"回退失败"
#   - 缓存不存在 → 首次使用联网下载，默认走 hf-mirror 国内镜像
#   - setdefault → 用户显式设置过的 HF_HUB_OFFLINE / HF_ENDPOINT 优先
_snapshot_dir = Path(CACHE_FOLDER) / ("models--" + MODEL_NAME.replace("/", "--"))
if _snapshot_dir.exists():
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
else:
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


def _get_embedder():
    global _embedder
    if _embedder is None:
        with _embedder_lock:
            if _embedder is None:
                from sentence_transformers import SentenceTransformer
                _embedder = SentenceTransformer(MODEL_NAME, cache_folder=CACHE_FOLDER)
    return _embedder


def get_embedding(text: str, query: bool = False) -> np.ndarray:
    """文本 → 归一化向量。query=True 时加检索前缀（bge 惯例）

    带 LRU 缓存：相同文本重复向量化（节点描述多次路由、同一轮任务描述被
    route/temperature_select 各算一遍）直接命中，避免重复跑 encode。
    线程安全：encode 在锁外执行，仅 LRU 字典操作加锁。
    """
    key = (query, text)
    with _embed_cache_lock:
        cached = _embed_cache.get(key)
        if cached is not None:
            _embed_cache.move_to_end(key)
            return cached
    emb = _get_embedder()
    t = QUERY_PREFIX + text if query else text
    vec = np.asarray(emb.encode(t, normalize_embeddings=True), dtype=np.float64)
    with _embed_cache_lock:
        _embed_cache[key] = vec
        if len(_embed_cache) > _EMBED_CACHE_MAX:
            _embed_cache.popitem(last=False)
    return vec


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    """余弦相似度（输入已归一化时 = 点积）"""
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def crowding_density(vectors: List[np.ndarray], sigma: float = 0.15,
                     threshold: float = 0.6) -> List[float]:
    """种类竞争拥挤度：高斯核对每个向量的邻居密度

    density_i = Σ_j exp(-(1 - cos(v_i,v_j)) / σ²)   仅计入相似度>阈值的邻居
    技能重叠越多 → 密度越高 → 淘汰概率越大（生态位压制）
    """
    n = len(vectors)
    if n == 0:
        return []
    sims = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            s = cosine_sim(vectors[i], vectors[j])
            sims[i, j] = sims[j, i] = s
    densities = []
    for i in range(n):
        row = sims[i]
        mask = row > threshold
        mask[i] = False
        if not mask.any():
            densities.append(0.0)
            continue
        d = float(np.sum(np.exp(-(1.0 - row[mask]) / (sigma * sigma))))
        densities.append(d)
    return densities


if __name__ == "__main__":
    # 自测：相似文本密度高，无关文本密度低
    v1 = get_embedding("精通 Python 后端开发与 Flask")
    v2 = get_embedding("擅长 Python Web 框架与接口开发")
    v3 = get_embedding("擅长前端 React 与 CSS 布局")
    print("sim(v1,v2):", round(cosine_sim(v1, v2), 4))
    print("sim(v1,v3):", round(cosine_sim(v1, v3), 4))
    print("density:", [round(d, 4) for d in crowding_density([v1, v2, v3])])
