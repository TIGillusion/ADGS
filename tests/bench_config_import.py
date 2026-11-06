"""实测 import config.config 的真实耗时（用户质疑：CONFIG() 应很快）。

测两项：
1. 纯 import 耗时（monkeypatch 掉 context probe，排除 LLM 探测）
2. probe_context_window 真实调用一次耗时（不打补丁，直接调真实函数）
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ---- 1) 纯 import（跳过探测）----
import utils.context_probe
utils.context_probe.probe_context_window = lambda *a, **k: None

t0 = time.time()
from config.config import config
t1 = time.time()
print(f"[1] 纯 import config.config（无探测）耗时: {t1-t0:.3f}s")

# ---- 2) 真实探测一次（不带补丁，重新加载模块拿回真函数）----
import importlib
importlib.reload(utils.context_probe)
import utils.context_probe as cp

t0 = time.time()
p = cp.probe_context_window(config.worker_url, config.worker_model,
                            config.worker_key, force=True)
t1 = time.time()
print(f"[2] probe_context_window 真实调用(worker 模型): {t1-t0:.3f}s -> {p}")

t0 = time.time()
p = cp.probe_context_window(config.brain_url, config.brain_model,
                            config.brain_key, force=True)
t1 = time.time()
print(f"[3] probe_context_window 真实调用(brain 模型): {t1-t0:.3f}s -> {p}")
