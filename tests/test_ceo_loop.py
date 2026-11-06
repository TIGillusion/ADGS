"""回归测试：CEO 决策循环（talk()）终态逻辑 + 动态循环上限 + 回复循环检测。

覆盖（2026-08-14 修复"CEO 过早汇报 / 不调工具导致循环中断" + 动态上限机制）：
1. 工具集含 finish/report
2. 纯文本（无 tool_calls）→ 回填 continue；report → continue；finish → return
3. 动态循环上限四档：循环→40 / 压缩>5→60 / 压缩>=3→80 / 默认→100
4. 回复循环检测：同指纹>=3 或 连续4次同工具（report/finish 排除）
5. 端到端：反复 report 同参数 → 检测循环 → 40 步收敛（非 100）
6. _compress_history 清理开头孤儿 tool 消息

用法：cd 到项目根，python tests/test_ceo_loop.py
"""
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.ceo import CEO
from config.prompts import CEO_TOOLS


def test_tool_registered():
    names = [t["function"]["name"] for t in CEO_TOOLS]
    assert "finish" in names, f"finish 工具缺失: {names}"
    assert "report" in names, f"report 工具缺失: {names}"
    print(f"[ok] 工具集: {names}")


def test_dynamic_limit():
    ceo = CEO()
    assert ceo._dynamic_limit() == 100, "默认应为 100"
    ceo._compress_count = 2
    assert ceo._dynamic_limit() == 100, "压缩2次应为 100"
    ceo._compress_count = 3
    assert ceo._dynamic_limit() == 80, "压缩3次应为 80"
    ceo._compress_count = 5
    assert ceo._dynamic_limit() == 80, "压缩5次应为 80"
    ceo._compress_count = 6
    assert ceo._dynamic_limit() == 60, "压缩6次应为 60"
    ceo._loop_active = True
    assert ceo._dynamic_limit() == 40, "疑似循环应为 40（最高优先级）"
    print("[ok] 动态上限四档 100/80/60/40 正确")


def test_detect_loop():
    # 精确指纹：第2次疑似提示、第3次确认循环
    ceo = CEO()
    assert ceo._detect_loop("read_file", {"path": "a.txt"}) is False
    assert ceo._loop_hint_pending is False
    assert ceo._detect_loop("read_file", {"path": "a.txt"}) is False  # 第2次→疑似
    assert ceo._loop_hint_pending is True, "第2次同指纹应触发疑似提示"
    assert ceo._detect_loop("read_file", {"path": "a.txt"}) is True   # 第3次→循环
    print("[ok] 精确指纹：第2次疑似、第3次确认循环")

    # 拆分结果相似→归并到同一指纹：第2次疑似、第3次循环
    ceo = CEO()
    for i in range(2):
        assert ceo._detect_loop("split_tasks",
            {"reason": f"第{i}步", "subtasks": [{"goal": "实现用户登录"}]}) is False
    assert ceo._loop_hint_pending is True, "拆分相似第2次应触发疑似提示"
    assert ceo._detect_loop("split_tasks",
        {"reason": "第2步", "subtasks": [{"goal": "实现用户登录"}]}) is True
    print("[ok] 拆分结果相似归并：第2次疑似、第3次循环")

    # 拆分结果不同 → 不归并、不触发
    ceo = CEO()
    goals = ["实现用户登录", "设计数据库表结构", "编写单元测试",
             "部署生产环境", "优化查询性能", "撰写API文档"]
    for i, g in enumerate(goals):
        r = ceo._detect_loop("split_tasks",
            {"reason": f"第{i}步", "subtasks": [{"goal": g}]})
        assert r is False, f"拆分第{i}次（goal={g}）不应触发"
    assert ceo._loop_hint_pending is False, "结果不同的拆分不应触发疑似"
    print("[ok] 拆分结果不同→排除，连续6次不触发")

    # 拆分结果措辞微调（相似度>=0.85）→ 仍归并、触发
    ceo = CEO()
    assert ceo._detect_loop("single_task", {"goal": "实现用户登录和注册功能"}) is False
    assert ceo._detect_loop("single_task", {"goal": "实现用户登录与注册功能"}) is False
    assert ceo._loop_hint_pending is True
    assert ceo._detect_loop("single_task", {"goal": "实现用户登录、注册功能"}) is True
    print("[ok] 拆分结果措辞微调：归并后第2次疑似、第3次循环")

    # read_file 连续使用（探索纪律 2026-08-20）：不再豁免，连续 10 次判循环兜底
    ceo = CEO()
    for i in range(9):
        assert ceo._detect_loop("read_file", {"path": f"x{i}"}) is False, f"第{i+1}次不应触发"
    assert ceo._detect_loop("read_file", {"path": "x9"}) is True, "连续 10 次 read_file 应判循环"
    print("[ok] read_file 连续10次判循环（探索纪律，不再豁免）")

    # report 连续 → 排除，不触发
    ceo = CEO()
    for i in range(6):
        assert ceo._detect_loop("report", {"summary": f"进度{i}"}) is False
    assert ceo._loop_hint_pending is False
    print("[ok] report 连续调用不误判")


def test_loop_flow():
    """纯文本→continue, report→continue, finish→return"""
    ceo = CEO()
    calls = {"seq": [
        {"content": "好的，我来处理", "tool_calls": [], "error": ""},
        {"content": "", "tool_calls": [{"id": "c2", "name": "report",
            "arguments": {"summary": "已完成第一步", "next_plan": "继续"}}], "error": ""},
        {"content": "", "tool_calls": [{"id": "c3", "name": "finish",
            "arguments": {"status": "blocked", "summary": "全部完成", "workflow": "流转",
                          "artifacts": ["a.md"]}}], "error": ""},
    ], "i": 0}

    def fake(messages, tools, tool_choice="required"):
        idx = calls["i"]
        calls["i"] += 1
        if idx < len(calls["seq"]):
            return calls["seq"][idx]
        return {"content": "", "tool_calls": [{"id": "cx", "name": "finish",
            "arguments": {"status": "blocked", "summary": "x", "workflow": "x", "artifacts": []}}], "error": ""}

    ceo.chat.call_tools = fake
    with redirect_stdout(io.StringIO()):
        result = ceo.talk("测试任务")
    assert calls["i"] == 3, f"预期3次调用，实际{calls['i']}"
    assert "全部完成" in result
    print("[ok] 纯文本→continue, report→continue, finish→return")


def test_loop_detection_end_to_end():
    """反复 report 同参数 → 检测循环 → 40 步内收敛（非 100）"""
    ceo = CEO()
    n = {"calls": 0}

    def fake(messages, tools, tool_choice="required"):
        n["calls"] += 1
        return {"content": "", "tool_calls": [{"id": f"c{n['calls']}", "name": "report",
            "arguments": {"summary": "同样的进度", "next_plan": "继续"}}], "error": ""}

    ceo.chat.call_tools = fake
    with redirect_stdout(io.StringIO()):
        result = ceo.talk("测试循环")
    assert ceo._loop_active is True, "应检测到疑似回复循环"
    assert n["calls"] == 40, f"循环应在 40 步收敛，实际 {n['calls']}"
    assert "疑似回复循环" in result
    # 疑似重复后应追加提示词到历史（第2次重复触发）
    hinted = any("检测到你最近在重复" in m.get("content", "")
                 for m in ceo.history)
    assert hinted, "疑似重复后应追加提示词到历史"
    print(f"[ok] 反复同动作 → 检测循环 → 40 步收敛 + 疑似重复提示已追加（调用 {n['calls']} 次）")


def test_loop_recovery():
    """确认循环后，窗口刷新（不再重复）→ 恢复上限"""
    ceo = CEO()
    # 3 次同指纹 → 确认循环，上限收紧到 40
    ceo._detect_loop("read_file", {"path": "a.txt"})
    ceo._detect_loop("read_file", {"path": "a.txt"})
    assert ceo._detect_loop("read_file", {"path": "a.txt"}) is True
    assert ceo._loop_active is True
    assert ceo._dynamic_limit() == 40
    # 8 步混合不同动作（不触发连续同工具），把 3 次重复挤出 10 步窗口
    actions = [
        ("list_files", {"path": "."}),
        ("single_task", {"goal": "任务A"}),
        ("read_file", {"path": "b.txt"}),
        ("report", {"summary": "进度1"}),
        ("list_files", {"path": "src"}),
        ("single_task", {"goal": "任务B"}),
        ("read_file", {"path": "c.txt"}),
        ("report", {"summary": "进度2"}),
    ]
    for name, a in actions:
        ceo._detect_loop(name, a)
    assert ceo._loop_active is False, "窗口刷新后应恢复"
    assert ceo._dynamic_limit() == 100
    print("[ok] 确认循环后窗口刷新 → 恢复上限到 100")


def test_compress_history_cleanup():
    """压缩后清理开头孤儿 tool 消息"""
    import core.ceo as ceo_mod
    ceo = CEO()
    orig = ceo_mod.compress
    try:
        ceo_mod.compress = lambda msgs, **kwargs: [
            {"role": "system", "content": "S"},
            {"role": "tool", "tool_call_id": "t1", "content": "孤儿1"},
            {"role": "tool", "tool_call_id": "t2", "content": "孤儿2"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ]
        out = ceo._compress_history()
        roles = [m["role"] for m in out]
        assert roles == ["system", "user", "assistant"], f"孤儿 tool 未清理: {roles}"
        print("[ok] 压缩后孤儿 tool 清理正确")
    finally:
        ceo_mod.compress = orig


def test_sanitize_tool_messages():
    """孤儿 tool 消息完整清理：开头孤儿 / 中段截断 / tool 结果不足 / 正常序列"""
    from core.compress import sanitize_tool_messages

    def A(tid="c1", n_tools=1):
        return {"role": "assistant", "content": "",
                "tool_calls": [{"id": f"call_{tid}_{i}", "type": "function",
                                "function": {"name": "f", "arguments": "{}"}}
                               for i in range(n_tools)]}

    def T(tid="c1"):
        return {"role": "tool", "tool_call_id": f"call_{tid}_0", "content": "r"}

    # 1) 开头孤儿 tool → 删
    msgs = [{"role": "system", "content": "S"}, T("x"),
            {"role": "user", "content": "u"}, A("a"), T("a")]
    out = sanitize_tool_messages(msgs)
    roles = [m["role"] for m in out]
    assert roles == ["system", "user", "assistant", "tool"], f"开头孤儿未删: {roles}"
    print("[ok] 开头孤儿 tool 删除")

    # 2) 中段截断：assistant(2 calls) 只跟 1 条 tool → 删 assistant（含已有 tool）
    msgs = [{"role": "system", "content": "S"},
            A("b", n_tools=2), T("b"),
            {"role": "user", "content": "u"}]
    out = sanitize_tool_messages(msgs)
    roles = [m["role"] for m in out]
    assert roles == ["system", "user"], f"tool 不足应删 assistant: {roles}"
    print("[ok] tool 结果不足 → 删除对应 assistant")

    # 3) 完整序列 + 多出的孤儿 tool → 保留完整对，删多出
    msgs = [{"role": "system", "content": "S"},
            A("c"), T("c"), T("orphan"),
            {"role": "user", "content": "u"}]
    out = sanitize_tool_messages(msgs)
    roles = [m["role"] for m in out]
    assert roles == ["system", "assistant", "tool", "user"], f"完整对应保留: {roles}"
    print("[ok] 完整 tool_calls+tool 保留，多余孤儿删除")

    # 4) 纯文本序列不动
    msgs = [{"role": "system", "content": "S"},
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "a"}]
    assert sanitize_tool_messages(msgs) == msgs
    print("[ok] 纯文本序列原样保留")


def test_unknown_tool_fallback():
    """模型幻觉未知工具名 → 回填 tool 响应，历史序列保持平衡（防 400）"""
    ceo = CEO()
    calls = {"seq": [
        {"content": "", "tool_calls": [{"id": "c1", "name": "magic_tool",
            "arguments": {"x": 1}}], "error": ""},
        {"content": "", "tool_calls": [{"id": "c2", "name": "finish",
            "arguments": {"status": "blocked", "summary": "完成", "workflow": "w", "artifacts": []}}], "error": ""},
    ], "i": 0}

    def fake(messages, tools, tool_choice="required"):
        idx = calls["i"]
        calls["i"] += 1
        return calls["seq"][idx] if idx < len(calls["seq"]) else calls["seq"][-1]

    ceo.chat.call_tools = fake
    with redirect_stdout(io.StringIO()):
        result = ceo.talk("测试未知工具")
    assert ceo.history[-1]["role"] == "tool", \
        f"历史末尾应为 tool（平衡），实际 {ceo.history[-1]['role']}"
    assert any("unknown tool magic_tool" in m.get("content", "") for m in ceo.history), \
        "未知工具应触发协议修复，不得执行"
    assert "完成" in result
    print("[ok] 未知工具名 → 回填 tool 响应，历史平衡，循环继续")


def test_dispatch_exception_balanced():
    """_dispatch_step 抛异常 → 回填错误结果，历史平衡，循环继续"""
    ceo = CEO()
    calls = {"seq": [
        {"content": "", "tool_calls": [{"id": "c1", "name": "split_tasks",
            "arguments": {"reason": "r", "overall_goal": "g",
                          "subtasks": [{"name": "a", "goal": "g", "requirements": [],
                              "acceptance_criteria": [], "deliverables": [], "permissions": "read only"}]}}], "error": ""},
        {"content": "", "tool_calls": [{"id": "c2", "name": "finish",
            "arguments": {"status": "blocked", "summary": "完成", "workflow": "w", "artifacts": []}}], "error": ""},
    ], "i": 0}

    def fake(messages, tools, tool_choice="required"):
        idx = calls["i"]
        calls["i"] += 1
        return calls["seq"][idx] if idx < len(calls["seq"]) else calls["seq"][-1]

    def boom(*a, **k):
        raise RuntimeError("模拟派发崩溃")

    ceo.chat.call_tools = fake
    ceo._dispatch_step = boom
    with redirect_stdout(io.StringIO()):
        result = ceo.talk("测试派发异常")
    assert calls["i"] == 2, f"异常后应继续决策，实际调用 {calls['i']}"
    err = [m for m in ceo.history if m["role"] == "tool"
           and "派发异常" in m["content"]]
    assert err, "应回填派发异常 tool 结果"
    assert ceo.history[-1]["role"] == "tool", "历史末尾应平衡"
    print("[ok] 派发异常 → 回填错误结果，历史平衡，循环继续")


def test_sanitize_before_llm_call():
    """历史被外部破坏（孤儿 tool）→ 每次 LLM 调用前自动清理（防 400）"""
    ceo = CEO()
    seen = {}

    def fake(messages, tools, tool_choice="required"):
        seen["roles"] = [m["role"] for m in messages]
        return {"content": "", "tool_calls": [{"id": "c1", "name": "finish",
            "arguments": {"status": "blocked", "summary": "s", "workflow": "w", "artifacts": []}}], "error": ""}

    ceo.chat.call_tools = fake
    # 预置脏历史：孤儿 tool（模拟异常中断遗留）
    ceo.history.append({"role": "tool", "tool_call_id": "ghost", "content": "孤儿"})
    with redirect_stdout(io.StringIO()):
        ceo.talk("测试清理")
    roles = seen["roles"]
    assert "tool" not in roles, f"孤儿 tool 应被清理，发送历史: {roles}"
    assert roles.count("tool") == roles.count("assistant"), f"发送历史不平衡: {roles}"
    print(f"[ok] 脏历史调用前自动清理: {roles}")


def test_explore_discipline():
    """探索纪律（2026-08-20）：大文件拒绝、3000 字符截断、递归拒绝、超 5 次提示委派"""
    import tempfile
    from pathlib import Path
    ceo = CEO()
    # 1) 大文件（>1MB）拒绝并引导委派
    big = Path(tempfile.mktemp(suffix=".txt"))
    big.write_bytes(b"x" * 2_000_000)
    r = ceo._read_file({"path": str(big)})
    assert "文件过大" in r and "委派节点" in r, f"大文件应拒绝委派: {r[:60]}"
    big.unlink()
    # 2) 3000 字符硬截断（模型传大 max_chars 也无效）
    mid = Path(tempfile.mktemp(suffix=".txt"))
    mid.write_text("a" * 10000, encoding="utf-8")
    r = ceo._read_file({"path": str(mid), "max_chars": 99999})
    assert "已截断" in r and "原文共 10000 字符" in r, f"应截断到 3000: {r[:60]}"
    assert r.index("已截断") < 3500, "截断位置应在 3000 字符附近"
    mid.unlink()
    # 3) recursive 递归列出拒绝
    r = ceo._list_files({"path": ".", "recursive": True})
    assert "递归" in r and "委派节点" in r, f"递归应拒绝委派: {r[:60]}"
    # 4) 探索超限：talk 循环 7 次探索，第 6/7 次工具结果带超限提示
    seq = ([{"content": "", "tool_calls": [{"id": f"c{i}", "name": "list_files",
             "arguments": {"path": "."}}], "error": ""} for i in range(7)]
           + [{"content": "", "tool_calls": [{"id": "cf", "name": "finish",
               "arguments": {"status": "blocked", "summary": "s", "workflow": "w", "artifacts": []}}], "error": ""}])
    i = {"n": 0}
    def fake(messages, tools, tool_choice="required"):
        idx = i["n"]
        i["n"] += 1
        return seq[idx] if idx < len(seq) else seq[-1]
    ceo2 = CEO()
    ceo2.chat.call_tools = fake
    with redirect_stdout(io.StringIO()):
        ceo2.talk("测试探索纪律")
    assert ceo2._explore_count == 7, f"应累计 7 次探索，实际 {ceo2._explore_count}"
    hints = [m["content"] for m in ceo2.history
             if m["role"] == "tool" and "探索已超限" in m["content"]]
    assert len(hints) == 2, f"第 6/7 次应带超限提示，实际 {len(hints)}"
    print("[ok] 探索纪律：大文件拒绝 / 3000截断 / 递归拒绝 / 超5次提示委派")


if __name__ == "__main__":
    test_tool_registered()
    test_dynamic_limit()
    test_detect_loop()
    test_loop_flow()
    test_loop_detection_end_to_end()
    test_loop_recovery()
    test_compress_history_cleanup()
    test_sanitize_tool_messages()
    test_unknown_tool_fallback()
    test_dispatch_exception_balanced()
    test_sanitize_before_llm_call()
    test_explore_discipline()
    print("\n全部通过 ✓")
