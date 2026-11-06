"""API 防护回归测试（2026-08-30 修复验证，test_client，临时会话自清理）：
  1. 会话名路径穿越消毒（P2-14）
  2. /api/session/graph 防倒退覆盖（多页面污染防护）
  3. 技能安装 from_node/name 消毒
  4. 基础 API 冒烟
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import utils.context_probe
utils.context_probe.probe_context_window = lambda *a, **k: None

import web_app
from config.config import config

TEST_SESS = "test_guardrail_20260830"
app = web_app.app
c = app.test_client()


def _mk_session(name, n_graph, n_msgs=2):
    """造一个测试会话文件（n_graph 个节点图谱 + 若干条消息）"""
    sess_dir = config.DATA_DIR / "sessions"
    sess_dir.mkdir(parents=True, exist_ok=True)
    graph = [{"key": f"n{i}", "id": f"n{i}", "type": "node", "label": f"n{i}",
              "parent": None, "status": "idle", "x": 0, "y": 0,
              "extra": {"goal": f"目标{i}"}} for i in range(n_graph)]
    data = {"name": name, "saved_at": "2026-08-30 12:00:00",
            "history": [{"role": "user", "content": f"测试 {name}"}] * n_msgs,
            "graph": graph}
    p = sess_dir / f"{name}.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


def test_sanitize_session_name():
    r = c.post("/api/session/load", json={"name": "../../etc/passwd"})
    assert r.status_code == 200 and not r.get_json().get("ok"), r.get_data(as_text=True)[:120]
    assert "非法" in r.get_json().get("error", ""), r.get_json()
    r = c.post("/api/session/load", json={"name": "a/b"})
    assert not r.get_json().get("ok")
    r = c.post("/api/session/load", json={"name": "..\\..\\x"})
    assert not r.get_json().get("ok")
    print("[1] 会话名路径穿越消毒 ✓（../../ 与 \\ 均拒绝）")


def test_graph_rollback_guard():
    p = _mk_session(TEST_SESS, n_graph=6)
    try:
        # 设置 _current_sess 指向测试会话
        r = c.post("/api/session/load", json={"name": TEST_SESS})
        assert r.get_json().get("ok"), r.get_data(as_text=True)[:200]
        # 6 节点 → 6 节点：允许（正常更新）
        r = c.post("/api/session/graph", json={"graph": _mk_session(TEST_SESS, 6)["graph"]
                                               if False else
                                               [{"key": f"x{i}", "type": "node"} for i in range(6)]})
        assert r.get_json().get("ok"), r.get_data(as_text=True)[:200]
        # 6 节点 → 2 节点（<60%）：拒绝（防倒退）
        r = c.post("/api/session/graph", json={"graph": [{"key": f"y{i}", "type": "node"} for i in range(2)]})
        j = r.get_json()
        assert not j.get("ok"), f"小图覆盖应被拒绝: {j}"
        assert "异常减少" in j.get("error", ""), j
        # 6 节点 → 7 节点：允许（增长正常）
        r = c.post("/api/session/graph", json={"graph": [{"key": f"z{i}", "type": "node"} for i in range(7)]})
        assert r.get_json().get("ok"), r.get_data(as_text=True)[:200]
        print("[2] /api/session/graph 防倒退覆盖 ✓（6→2 拒 / 6→7 允许）")
    finally:
        p.unlink(missing_ok=True)


def test_graph_overview_pollution_guard():
    """2026-09-03 用户报告：旧前端「任务总览」自动保存把 29 个会话的混合图谱
    （含 29 个 "xxx::ceo" 节点）写进 auto-20260828-133107 → 拒写防护。
    规则：任何 key 带"其它会话名::"前缀的图谱整体拒写；本会话名::前缀允许。"""
    p = _mk_session(TEST_SESS, n_graph=6)
    try:
        r = c.post("/api/session/load", json={"name": TEST_SESS})
        assert r.get_json().get("ok"), r.get_data(as_text=True)[:200]
        base = [{"key": f"n{i}", "id": f"n{i}", "type": "node", "label": f"n{i}",
                 "parent": None, "status": "idle"} for i in range(6)]
        # ① 含外部会话的 ceo 节点（总览 dump 特征）：拒绝
        graph = list(base)
        graph.append({"key": "auto-20260828-133107::ceo", "id": "ceo", "type": "ceo",
                      "label": "CEO 决策中枢", "parent": "ceo", "status": "idle"})
        r = c.post("/api/session/graph", json={"graph": graph})
        j = r.get_json()
        assert not j.get("ok"), f"总览污染图谱应被拒绝: {j}"
        assert "总览" in j.get("error", ""), j
        # ② 含外部会话的非 ceo 节点（归一化后浏览器可能仍会导出的残留前缀）：拒绝
        graph = list(base)
        graph.append({"key": "auto-20260828-193209::round-1", "id": "r1",
                      "type": "round", "label": "轮 1", "parent": "ceo",
                      "status": "idle"})
        r = c.post("/api/session/graph", json={"graph": graph})
        assert not r.get_json().get("ok"), "外部前缀非 ceo 节点也应拒绝"
        # ③ 本会话名::前缀（旧前端自身残留）+ 规范 ceo：允许
        graph2 = list(base)
        graph2.append({"key": TEST_SESS + "::round-1", "id": "r1", "type": "round",
                       "label": "轮 1", "parent": "ceo", "status": "idle"})
        graph2.append({"key": "ceo", "id": "ceo", "type": "ceo", "label": "CEO",
                       "parent": None, "status": "idle"})
        r = c.post("/api/session/graph", json={"graph": graph2})
        assert r.get_json().get("ok"), r.get_data(as_text=True)[:200]
        print("[2b] /api/session/graph 总览污染防护 ✓（外部:: 拒 / 本会话:: 允许）")
    finally:
        p.unlink(missing_ok=True)


def test_skill_install_sanitize():
    r = c.post("/api/node/ags_1/skill/install",
               json={"from_node": "../../evil", "name": "x"})
    j = r.get_json()
    assert not j.get("ok"), f"from_node 路径穿越应拒绝: {j}"
    print("[3] 技能安装 from_node 消毒 ✓")


def test_basic_api():
    for url in ["/", "/api/state", "/api/sessions"]:
        r = c.get(url)
        assert r.status_code == 200, f"GET {url}: {r.status_code}"
    print("[4] 基础 API 冒烟 ✓")


if __name__ == "__main__":
    test_sanitize_session_name()
    test_graph_rollback_guard()
    test_graph_overview_pollution_guard()
    test_skill_install_sanitize()
    test_basic_api()
    print("\nPASS: API 防护全部验证通过")
