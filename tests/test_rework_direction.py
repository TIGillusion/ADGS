"""Repeated rework must not turn finishing work into a new investigation."""
import pytest
from core.node import NODE

@pytest.mark.parametrize("reworks", [1, 3, 8])
def test_verified_fix_and_missing_report_keep_the_same_direction(reworks):
    task = "## 任务\n修复网格并保留原始证据"
    failure = "根因与修复方案已实测确认，尚未落盘；执行报告缺失"
    instruction = NODE.__new__(NODE)._rework_instruction(task, {
        "passed": False,
        "failed_items": [{"criterion": "修复落盘与执行报告", "why": failure}],
    }, reworks=reworks)
    assert task in instruction and failure in instruction
    assert "已验证方案尚未落盘时先实施并复验" in instruction
    assert "仅缺报告、格式或验收证据时只补这些缺项" in instruction
    assert "方向纠偏（强制）" not in instruction
    assert "至少 2 个不同方向" not in instruction

def test_contradictory_evidence_still_allows_reinvestigation():
    failure = "已实施方案后最小复现仍失败，原根因被新证据反驳"
    instruction = NODE.__new__(NODE)._rework_instruction("原始任务与权限", {
        "failed_items": [failure],
    }, reworks=3)
    assert failure in instruction
    assert "新失败证据反驳原根因或已实施方案时" in instruction
    assert "最小复现重新排查并调整方向" in instruction
    assert "保留用户和其他会话的成果" in instruction
