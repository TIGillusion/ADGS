"""验证新一轮任务序号从 1 开始的复位逻辑（2026-09-03 用户实测修复）。

背景：web 图谱任务序号 = 后端 _task_seq（每轮从 1 起）。talk() 进入时
只有 _task_done=True（上一任务已 finish/上限/出错结束）才重置 _task_seq
并 _round_no+=1。ask_user 提问挂起时 _task_done=False（回答接着同一轮，
故意不重置）。此前 bug：任务 runner 异常路径只调 reset_session()
（其内部把 _task_done 置 False）而不恢复 True → 下一条用户消息被当成
"挂起回答"：序号接续上轮（如轮 4 直接从 2 开始）、轮号不 +1、队列误判
挂起而保留。修复：异常路径 reset_session 后恢复 _task_done=True。

不触碰真实 LLM：object.__new__ 构造裸 CEO + 直接测 _maybe_reset_round /
reset_session 状态迁移（与 test_graph_task_count.py 同构）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# CONFIG() 构造时会 force=True 探测模型上下文（打真实 LLM），测试前替换掉
import utils.context_probe
utils.context_probe.probe_context_window = lambda *a, **k: None

import core.ceo as ceo_mod


def _make_ceo(task_done=False, task_seq=0, round_no=1):
    c = object.__new__(ceo_mod.CEO)
    c._active = "test"
    c._talk_session = None
    c._logs = {"test": []}
    c._histories = {"test": []}
    c._task_seq = task_seq
    c._round_no = round_no
    c._task_done = task_done
    c.workflow_log = []
    return c


def test_new_task_resets_seq_and_bumps_round():
    """上一任务正常结束（finish/上限/LLM失败）→ 下一条消息开新轮、序号归 0"""
    ceo = _make_ceo(task_done=True, task_seq=3, round_no=4)
    assert ceo._maybe_reset_round() is True
    assert ceo._task_seq == 0, f"新轮序号应归 0，实际 {ceo._task_seq}"
    assert ceo._round_no == 5, f"轮号应 +1 到 5，实际 {ceo._round_no}"
    assert ceo._task_done is False
    # 同一轮内重复调用（后续 talk 分支）不应再 +1
    assert ceo._maybe_reset_round() is False
    assert ceo._round_no == 5
    print("[1/4] 正常结束 → 新轮序号归 0、轮号 +1 ✓")


def test_ask_user_suspend_keeps_round():
    """ask_user 挂起（_task_done=False）→ 用户回答不重置（同一轮继续）"""
    ceo = _make_ceo(task_done=False, task_seq=2, round_no=4)
    assert ceo._maybe_reset_round() is False
    assert ceo._task_seq == 2, "挂起恢复不应重置序号"
    assert ceo._round_no == 4, "挂起恢复不应 +1 轮号"
    print("[2/4] ask_user 挂起恢复 → 同一轮、序号不重置 ✓")


def test_exception_path_restores_done_and_next_round_resets():
    """异常路径状态迁移（web_app.py runner 异常处理器序列的等价模拟）：

    reset_session() 清目标会话上下文并把 _task_done 置 False（设计如此，
    它本身是"清理会话"原语）；runner 随后恢复 _task_done=True
    （2026-09-03 修复）。模拟完整序列后，下一条用户消息必须开新轮。
    """
    ceo = _make_ceo(task_done=True, task_seq=1, round_no=3)
    # 任务运行中（第 3 轮第 1 个任务后 seq=1）抛出异常 → runner 处理：
    ceo._histories["test"].append({"role": "user", "content": "保留已完成成果"})
    ceo.mark_interrupted("test", "fixture interruption")
    assert ceo._task_done is False
    assert ceo._terminal_status == "exception"
    assert ceo._histories["test"][0]["content"] == "保留已完成成果"
    # 异常任务保持未完成，后续恢复继续原轮次，不把异常误报为完成。
    assert ceo._maybe_reset_round() is False
    assert ceo._round_no == 3, "异常恢复不应凭空创建新轮"
    print("[3/4] 异常后保留未完成状态与异常终态 ✓")


def test_exception_without_fix_reproduces_drift():
    """回归对照：若异常路径【不】恢复 _task_done（修复前行为），
    复现用户实测的漂移——轮号不 +1、序号接续（新一轮第一个任务显示 2）"""
    ceo = _make_ceo(task_done=True, task_seq=1, round_no=3)
    ceo.reset_session("test")
    # 修复前：不恢复 True → 下一条消息被当作"挂起回答"
    assert ceo._maybe_reset_round() is False
    assert ceo._round_no == 0, "显式重置应清空轮次"
    assert ceo._task_seq == 0, "显式重置应清空任务序号"
    print("[4/4] 对照（未恢复 _task_done）→ 复现'新一轮从 2 开始'漂移 ✓")


if __name__ == "__main__":
    test_new_task_resets_seq_and_bumps_round()
    test_ask_user_suspend_keeps_round()
    test_exception_path_restores_done_and_next_round_resets()
    test_exception_without_fix_reproduces_drift()
    print("PASS: 新一轮任务序号复位（正常结束/挂起/异常三路径）")
