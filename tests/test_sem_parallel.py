"""验证全局并发信号量槽数 = 生效并行度（max_workers_effective）。

BUG（2026-08-30 修复）：node.py 的 _EXEC_SEM 此前用 config.vllm_concurrency（=3）
创建，导致 max_workers=2 时仍允许 3 个 hermes 并发执行（web 图谱同时出现
3 个"执行中"节点）。修复后信号量 = min(max_workers, vllm_concurrency) = 2。
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# CONFIG() 构造时会 force=True 探测三个模型的上下文窗口（每次打真实 LLM，
# 慢至数分钟）。测试不关心上下文窗口，先替换 probe 为快速返回 None。
import utils.context_probe
utils.context_probe.probe_context_window = lambda *a, **k: None

from config.config import config
from core.node import _EXEC_SEM, _acquire_exec, _release_exec


def test_config_values():
    assert config.max_workers == 2, f"max_workers={config.max_workers} != 2"
    assert config.vllm_concurrency == 3, f"vllm_concurrency={config.vllm_concurrency} != 3"
    assert config.max_workers_effective == 2, \
        f"max_workers_effective={config.max_workers_effective} != 2"
    print(f"[1/3] config: max_workers={config.max_workers} "
          f"vllm_concurrency={config.vllm_concurrency} "
          f"effective={config.max_workers_effective}")


def test_sem_value():
    assert _EXEC_SEM._value == config.max_workers_effective, \
        f"信号量槽数 {_EXEC_SEM._value} != 生效并行度 {config.max_workers_effective}"
    print(f"[2/3] 信号量初始槽数 = {_EXEC_SEM._value}（修复前为 3，修复后为 2）")


def test_concurrent_peak():
    """6 线程同时抢槽：同时持有槽的峰值必须 ≤ max_workers_effective（2）。"""
    n_threads = 6
    active = [0]
    peak = [0]
    lock = threading.Lock()

    def worker():
        _acquire_exec()
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.3)
        with lock:
            active[0] -= 1
        _release_exec()

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert peak[0] <= config.max_workers_effective, \
        f"并发峰值 {peak[0]} > 生效并行度 {config.max_workers_effective}"
    print(f"[3/3] {n_threads} 线程抢槽并发峰值 = {peak[0]}（≤ {config.max_workers_effective} ✓）")


if __name__ == "__main__":
    test_config_values()
    test_sem_value()
    test_concurrent_peak()
    print("PASS: 并发信号量限制与 max_workers=2 一致，不再允许 3 并发")
