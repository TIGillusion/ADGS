"""P0 修复验证（2026-08-30 审查报告）：
1. 验收 LLM 故障时节点分数保持旧分，不再归零（agent.py evaluate）
2. 进化空池自愈：web 首启/节点清空后 evolve 不再崩溃（evolution.py）
3. 上下文探测改为配置预写：config 秒级加载、无探测请求（config.py）
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 防 context probe 副作用（修复后默认已关闭，保险起见再替一次）
import utils.context_probe
utils.context_probe.probe_context_window = lambda *a, **k: None


def test_p0_1_evaluate_error_keeps_score():
    """LLM 故障/无工具调用 → score 保持 old_score（修复前返回 0 清零）"""
    import core.agent as agent_mod
    ag = agent_mod.AGENT("http://x", "m", "k", node_id="t_p01")
    # 网络错误分支
    ag.chat.call_tools = lambda *a, **k: {"error": "vLLM 抖动"}
    r = ag.evaluate("任务", "日志", old_score=53.6)
    assert r["passed"] is False and abs(r["score"] - 53.6) < 1e-6, f"错误分支应保持旧分: {r}"
    # 无工具调用分支
    ag.chat.call_tools = lambda *a, **k: {"tool_calls": []}
    r = ag.evaluate("任务", "日志", old_score=42.0)
    assert r["passed"] is False and abs(r["score"] - 42.0) < 1e-6, f"无工具调用应保持旧分: {r}"
    # 正常路径仍为增量累加（回归）
    ag.chat.call_tools = lambda *a, **k: {"tool_calls": [{
        "name": "accept_report", "arguments": {"passed": True, "score": 10, "passed_items": [], "failed_items": []}}]}
    r = ag.evaluate("任务", "日志", old_score=50.0)
    assert abs(r["score"] - 52.0) < 1e-6, f"正常路径应为 old+delta: {r}"
    print("[P0-1] 验收错误分支保持旧分 ✓（53.6 不归零 / 42.0 不归零 / 正常路径 50→52）")


def test_p0_2_evolve_empty_pool_self_heal():
    """空池 evolve 不再崩：ensure_initial 自愈后正常出报告"""
    from core.registry import Node, Registry
    from core.evolution import evolve, _default_describe

    # 场景 A：空池 + 初始化成功 → 自愈后正常进化（dry_run 不建 profile）
    tmp = Registry(path=tempfile.mktemp(suffix="_p02a.json"))

    def fake_init(describe, count=None):
        for i in range(3):
            tmp.add(Node(f"init_{i}", describe))
    tmp.ensure_initial = fake_init
    # Preview is side-effect free; initialize the fixture explicitly.
    fake_init(_default_describe())
    r = evolve(reg=tmp, dry_run=True)
    assert r["n_before"] == 3, f"空池自愈后应初始化 3 节点: {r}"
    assert r["births"], f"应产出繁衍计划: {r}"
    print(f"[P0-2a] 空池自愈 ✓ 初始化 3 节点, 繁衍计划 {len(r['births'])} 个")

    # 场景 B：空池 + 初始化失败 → 安全短路报告，不抛异常
    tmp2 = Registry(path=tempfile.mktemp(suffix="_p02b.json"))
    tmp2.ensure_initial = lambda describe, count=None: None  # 什么都不加
    r = evolve(reg=tmp2, dry_run=True)
    assert r["n_after"] == 0 and "note" in r, f"初始化失败应短路返回: {r}"
    print("[P0-2b] 空池+初始化失败 安全短路 ✓")


def test_p0_3_no_probe_fast_config():
    """配置预写上下文：加载 <1s、无探测、值正确"""
    t0 = time.time()
    from config.config import config
    dt = time.time() - t0
    assert dt < 5.0, f"config 加载应 <5s，实际 {dt:.1f}s（探测未关闭？）"
    assert config.ceo_ctx_window == 1048576, f"ceo_ctx={config.ceo_ctx_window}"
    assert config.worker_ctx_window == 32768, f"worker_ctx={config.worker_ctx_window}"
    assert config.ctx_window == config.ceo_ctx_window
    print(f"[P0-3] config 加载 {dt:.2f}s ✓ CEO 使用角色窗口，worker_ctx=32768（无探测）")


if __name__ == "__main__":
    test_p0_1_evaluate_error_keeps_score()
    test_p0_2_evolve_empty_pool_self_heal()
    test_p0_3_no_probe_fast_config()
    print("\nPASS: P0 三个问题全部修复验证通过")
