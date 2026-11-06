"""2026-08-30 审查报告修复回归测试（P1/P2/P3 部分）。

覆盖：
  P1-5  重组描述去重 + 长度上限
  P1-6  CEO 产物列表兼容字符串/乱码修复
  P2-11 委派评估分数尺度与单任务验收统一（±2）
  P2-12 registry 原子写（临时文件 + replace）
  P2-14 会话名/技能名路径穿越消毒
  P3    _is_blank_node 相等判断（recombine 描述不再误判为空）
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import utils.context_probe
utils.context_probe.probe_context_window = lambda *a, **k: None


def test_p1_5_recombine_describe_dedup_and_limit():
    from core.evolution import _recombine_describe
    from core.registry import Node

    parent = Node("ags_x", "节点 ags_x 擅长 Python 后端开发。")
    skills = [{"name": "skill-a"}, {"name": "skill-b"}, {"name": "skill-a"},  # 重复
              {"name": "skill-c"}, {"name": "skill-b"}]
    d = _recombine_describe(parent, skills)
    # 去重：每个技能名只出现一次
    for s in ["skill-a", "skill-b", "skill-c"]:
        assert d.count(s) == 1, f"{s} 出现 {d.count(s)} 次（应去重）: {d}"
    # 长度上限
    assert len(d) <= 500, f"描述长度 {len(d)} 超 500"
    # 无技能时原样返回（截断到上限）
    d2 = _recombine_describe(parent, [])
    assert d2 == parent.describe
    print(f"[P1-5] 重组描述去重+截断 ✓ 长度={len(d)}")
    print(f"         {d[:80]}...")


def test_p1_6_artifacts_string_and_list():
    from core.ceo import CEO
    ceo = object.__new__(CEO)
    ceo._active = "t"
    ceo._talk_session = None
    ceo._logs = {"t": []}
    ceo._histories = {"t": []}
    ceo.workflow_log = []
    # 字符串 artifacts（答题卡模式返回字符串）→ 不再逐字符拆分
    r1 = CEO._format_report(ceo, {"summary": "s", "artifacts": "report.md"})
    assert "- report.md" in r1, f"字符串 artifacts 应整体输出: {r1!r}"
    # 列表
    r2 = CEO._format_report(ceo, {"summary": "s", "artifacts": ["a.py", "b.md"]})
    assert "- a.py" in r2 and "- b.md" in r2
    # 空
    r3 = CEO._format_report(ceo, {"summary": "s"})
    assert "- 无" in r3
    print("[P1-6] CEO 产物列表兼容字符串/列表/空 ✓")


def test_p2_11_delegation_score_scale():
    from core.node import NODE
    from config.config import config
    # 子任务全成功、平均分 95 → delta 钳 ±10 × 0.2 = ±2（此前 ±15）
    children = [{"success": True, "evaluation": {"score": 95.0, "raw_score": 10}},
                {"success": True, "evaluation": {"score": 92.0, "raw_score": 10}}]
    r = NODE._evaluate_delegation(None, children, old_score=50.0)
    assert abs(r["score"] - (50.0 + 10 * config.delta_coef)) < 1e-6, \
        f"成功委派应 +2（钳 ±10×coef），实际 {r['score']}"
    # 全失败 → -2
    children_f = [{"success": False, "evaluation": {"raw_score": -10}},
                  {"success": False, "evaluation": {"raw_score": -10}}]
    r = NODE._evaluate_delegation(None, children_f, old_score=50.0)
    assert abs(r["score"] - (50.0 - 10 * config.delta_coef)) < 1e-6, \
        f"失败委派应 -2，实际 {r['score']}"
    # 无子任务 → 保持旧分
    r = NODE._evaluate_delegation(None, [], old_score=66.6)
    assert abs(r["score"] - 66.6) < 1e-6
    print(f"[P2-11] 委派评估尺度统一 ±{10 * config.delta_coef} ✓ "
          f"(50→{r['score']:.1f} 空子任务保持旧分)")


def test_p2_12_registry_atomic_save():
    import json
    from core.registry import Node, Registry
    tmp = Path(tempfile.mktemp(suffix="_reg.json"))
    reg = Registry(path=tmp)
    for i in range(5):
        reg.add(Node(f"ags_{i}", f"节点 {i}"))
    # 原子写：主文件是完整 JSON，且存在 .tmp 中间文件说明走临时文件
    data = json.loads(tmp.read_text(encoding="utf-8"))
    assert len(data["nodes"]) == 5
    tmp.unlink()
    print("[P2-12] registry 原子写（tmp+replace）✓")


def test_p2_14_sanitize_path_traversal():
    import web_app
    # 合法会话名
    assert web_app._sanitize_name("auto-20260830-123456", web_app._SESS_NAME_RE)
    # 路径穿越被拒
    assert web_app._sanitize_name("../../etc/passwd", web_app._SESS_NAME_RE) is None
    assert web_app._sanitize_name("..\\..\\x", web_app._SESS_NAME_RE) is None
    assert web_app._sanitize_name("a/b", web_app._SESS_NAME_RE) is None
    # 技能名：点号允许（如 skill.md 后缀），斜杠拒绝
    assert web_app._sanitize_name("my-skill", web_app._SKILL_NAME_RE)
    assert web_app._sanitize_name("a/../b", web_app._SKILL_NAME_RE) is None
    print("[P2-14] 会话名/技能名路径穿越消毒 ✓")


def test_p3_is_blank_node_equal_check():
    from core.evolution import _is_blank_node, _default_describe
    from core.registry import Node
    # 初始文本 = 空节点
    assert _is_blank_node(Node("ags_1", _default_describe())) is True
    # recombine 描述（初始文本 + 技能摘要，以同前缀开头）≠ 空节点
    d = _default_describe() + " 继承了技能基因：skill-a、skill-b。"
    assert _is_blank_node(Node("ags_2", d)) is False, "带技能摘要的 recombine 描述不应判空"
    # 分化描述
    assert _is_blank_node(Node("ags_3", "节点 ags_3 擅长 Flask 开发与测试。")) is False
    print("[P3] _is_blank_node 相等判断 ✓（recombine 描述不再误判空）")


if __name__ == "__main__":
    test_p1_5_recombine_describe_dedup_and_limit()
    test_p1_6_artifacts_string_and_list()
    test_p2_11_delegation_score_scale()
    test_p2_12_registry_atomic_save()
    test_p2_14_sanitize_path_traversal()
    test_p3_is_blank_node_equal_check()
    print("\nPASS: P1/P2/P3 修复全部验证通过")
