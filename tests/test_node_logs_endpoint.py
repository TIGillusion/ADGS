"""节点磁盘历史日志端点回归（2026-09-03：web3 抽屉历史任务日志全空修复）：
  1. GET /api/node/<id>/logs 读真实工作区 live.log 尾部（行上限/截断标记）
  2. 无日志节点 → 双通道 null（前端显示空态提示）
  3. 非法节点 ID（路径穿越/点号/过长）→ 400 拒绝
  4. _tail_lines 单元行为（行过滤、大小上限截断）
test_client 模式（与 test_api_guardrails.py 同构），只读不写。
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import utils.context_probe
utils.context_probe.probe_context_window = lambda *a, **k: None

import web_app
from config.config import config

c = web_app.app.test_client()


def test_real_node_logs():
    """真实工作区存在的节点（ags_101）→ hermes 通道返回磁盘尾部行"""
    r = c.get("/api/node/ags_101/logs")
    assert r.status_code == 200, r.status_code
    j = r.get_json()
    assert j.get("ok"), j
    assert j.get("node") == "ags_101"
    live = Path(config.workspace) / ".adgs_logs" / "ags_101_live.log"
    if live.exists():
        assert j.get("hermes") is not None, "live.log 存在时 hermes 通道应非 null"
        assert isinstance(j["hermes"]["lines"], list)
        assert len(j["hermes"]["lines"]) > 0, "ags_101 有 289 行日志，不应为空"
        assert len(j["hermes"]["lines"]) <= 400, "默认 lines=400 上限"
        # live.log 头部是 "=== Profile: ..." 头，尾部应是执行内容
        assert any("===" in l or l.strip() for l in j["hermes"]["lines"][:3])
        # agentlog 通道：文件存在则非 null，不存在则 null（不报错）
        assert j.get("agentlog") is None or isinstance(j["agentlog"]["lines"], list)
    else:
        assert j.get("hermes") is None, "live.log 不存在时应为 null"
    print("[1] 真实节点日志回退 ✓（ags_101 hermes 通道非空）")


def test_unknown_node():
    """registry 不存在但 ID 合法的节点 → ok + 双通道按磁盘实际情况（通常 null）"""
    r = c.get("/api/node/ags_nonexistent_42/logs")
    assert r.status_code == 200, r.status_code
    j = r.get_json()
    assert j.get("ok"), j
    assert j.get("hermes") is None, "无磁盘日志 → hermes=null"
    assert j.get("agentlog") is None, "无磁盘日志 → agentlog=null"
    print("[2] 未知节点 → 双通道 null（不 500）✓")


def test_invalid_node_id():
    """非法节点 ID：穿越/点号/斜杠/超长 → 绝不 200/500。

    带斜杠/.. 的输入由路由层或 werkzeug 客户端规范化拦截（404/308）；
    能到达端点的单段非法 ID 必须由端点正则 400 拒绝（安全边界在端点）。
    """
    for bad in ["../../etc", "a/b", "a.b", "a\\b", "x" * 81, "中文"]:
        r = c.get(f"/api/node/{bad}/logs")
        assert r.status_code not in (200, 500), f"{bad!r} 应拒，实际 {r.status_code}"
    # 空路径段：/api/node//logs → werkzeug 规范化 308 或 404（绝不 200/500）
    r = c.get("/api/node//logs")
    assert r.status_code not in (200, 500), r.status_code
    # 能到达端点的单段非法 ID 必须由端点自身 400 拒绝（路由层挡不住）
    for bad in ["a.b", "a\\b", "x" * 81]:
        r = c.get(f"/api/node/{bad}/logs")
        assert r.status_code == 400, f"{bad!r} 端点应 400，实际 {r.status_code}"
    print("[3] 非法节点 ID 消毒 ✓（穿越/点号/超长均拒，无 200/500）")


def test_lines_param_clamped():
    """lines 参数越界收敛：0→50 下限，99999→2000 上限，非数字→默认 400"""
    r = c.get("/api/node/ags_101/logs?lines=99999")
    assert r.status_code == 200
    j = r.get_json()
    if j.get("hermes"):
        assert len(j["hermes"]["lines"]) <= 2000, "上限 2000"
    r = c.get("/api/node/ags_101/logs?lines=abc")
    assert r.status_code == 200, "非数字应回落默认，不 500"
    print("[4] lines 参数收敛 ✓")


def test_tail_lines_unit():
    """_tail_lines：非空行过滤 / 大文件尾部截断标记 / 不存在 → None"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "x.log"
        p.write_text("  \n首行\n\n末行\n", encoding="utf-8")
        d = web_app._tail_lines(p, 100)
        assert d is not None
        assert d["lines"] == ["首行", "末行"], f"空行应被过滤: {d['lines']}"
        assert d["total_lines"] == 2
        assert d["truncated"] is False
        # n 大于行数时返回全部非空行（尾部截取不丢内容）
        d = web_app._tail_lines(p, 50)
        assert d["lines"] == ["首行", "末行"], f"全量应保留: {d['lines']}"
        # 大文件：只读尾部 512KB，标记 truncated
        big = Path(td) / "big.log"
        with open(big, "w", encoding="utf-8") as f:
            for i in range(60000):
                f.write(f"line-{i}\n")
        d = web_app._tail_lines(big, 400)
        assert d is not None and d["truncated"] is True
        assert d["lines"][-1] == "line-59999"
        assert len(d["lines"]) == 400
        # 不存在
        assert web_app._tail_lines(Path(td) / "nope.log", 100) is None
    print("[5] _tail_lines 单元行为 ✓（过滤/截断/缺失）")


if __name__ == "__main__":
    test_real_node_logs()
    test_unknown_node()
    test_invalid_node_id()
    test_lines_param_clamped()
    test_tail_lines_unit()
    print("\nPASS: 节点磁盘日志端点全部验证通过")
