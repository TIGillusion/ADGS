"""_session_messages 话术还原回归（2026-09-03：历史恢复只剩工具、CEO 话术丢失修复）：
  1. ask_user / report / finish 三个"面向用户输出"工具按话术 kind 还原
     （kind=ask/report/finish + 对应内容字段），不再退化成 kind=tool
  2. 话术工具回填的占位 tool 结果（（已向用户提问：…）等）不附加到输出
  3. 普通工具（split_tasks 等）仍输出 kind=tool 且附结果
  4. 旧格式兼容：无 tool_calls 的 assistant 纯文本 → kind=text
纯函数级测试（不调 HTTP、不碰活服务状态）。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import utils.context_probe
utils.context_probe.probe_context_window = lambda *a, **k: None

import web_app


def _tc(name, args):
    return {
        "id": f"call_{name}", "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


def test_ask_user_restored_as_ask():
    history = [
        {"role": "system", "content": "prompt"},
        {"role": "user", "content": "帮我做 X"},
        {"role": "assistant", "content": "", "tool_calls": [
            _tc("ask_user", {"question": "你希望报告多长？"})]},
        {"role": "tool", "tool_call_id": "call_ask_user", "content": "（已向用户提问：你希望报告多长？）"},
    ]
    out = web_app._session_messages(history)
    # system 跳过，user 保留，ask_user 还原为 ask（问题文本来自 args）
    assert [m["kind"] for m in out] == ["user", "ask"], out
    assert out[1]["text"] == "你希望报告多长？"
    # 占位 tool 结果不产生额外条目、不附加到 ask 上
    assert "result" not in out[1]
    print("[1] ask_user → CEO 提问话术 ✓")


def test_report_finish_restored():
    history = [
        {"role": "user", "content": "做 Y"},
        {"role": "assistant", "content": "", "tool_calls": [
            _tc("report", {"summary": "已完成调研", "next_plan": "开始写作"})]},
        {"role": "tool", "tool_call_id": "call_report", "content": "（进度已汇报给用户，任务尚未结束，请继续决策下一步）"},
        {"role": "assistant", "content": "", "tool_calls": [
            _tc("finish", {"summary": "报告已交付", "workflow": "调研→写作→校对",
                           "artifacts": ["report.md"]})]},
        {"role": "tool", "tool_call_id": "call_finish", "content": "（最终汇报已输出给用户，任务结束）"},
    ]
    out = web_app._session_messages(history)
    assert [m["kind"] for m in out] == ["user", "report", "finish"], out
    r = out[1]
    assert r["text"] == "已完成调研" and r["reason"] == "开始写作", r
    f = out[2]
    assert f["text"] == "报告已交付" and f["workflow"] == "调研→写作→校对"
    assert f["artifacts"] == ["report.md"], f
    # 占位结果（（进度已汇报…）/（最终汇报…））不附加
    assert "result" not in r and "result" not in f
    print("[2] report/finish → 话术 kind ✓（占位结果已丢弃）")


def test_plain_tool_keeps_result():
    history = [
        {"role": "user", "content": "做 Z"},
        {"role": "assistant", "content": "", "tool_calls": [
            _tc("split_tasks", {"tasks": ["t1", "t2"]})]},
        {"role": "tool", "tool_call_id": "call_split_tasks",
         "content": json.dumps({"ok": True, "count": 2})},
    ]
    out = web_app._session_messages(history)
    assert [m["kind"] for m in out] == ["user", "tool"], out
    t = out[1]
    assert t["name"] == "split_tasks"
    assert t["args"] == {"tasks": ["t1", "t2"]}
    assert t.get("result"), "普通工具应附结果"
    print("[3] 普通工具 kind=tool + 结果 ✓")


def test_plain_assistant_text_and_bad_args():
    history = [
        {"role": "assistant", "content": "纯文本思考（无工具）"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "x", "type": "function",
             "function": {"name": "ask_user", "arguments": "{坏JSON"}}]},
        {"role": "tool", "tool_call_id": "x", "content": "（已向用户提问：请详细说明你的需求）"},
    ]
    out = web_app._session_messages(history)
    assert out[0] == {"kind": "text", "text": "纯文本思考（无工具）"}, out
    # args 解析失败 → _safe_json 返回 {"raw": ...}（非 dict 语义）→ 问题兜底默认文案
    assert out[1]["kind"] == "ask"
    assert out[1]["text"] == "请详细说明你的需求", out[1]
    print("[4] 纯文本/坏参数兜底 ✓")


if __name__ == "__main__":
    test_ask_user_restored_as_ask()
    test_report_finish_restored()
    test_plain_tool_keeps_result()
    test_plain_assistant_text_and_bad_args()
    print("ALL PASS")
