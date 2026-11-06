"""回归:未配对/未合并代理字符不得中断任务(2026-10-21 实测事故)。

事故链:pywinpty 读边界拆开 UTF-16 代理对 → feed_chunk 的
text.encode("utf-8") 杀死 drain 线程 → 脏字符留在 chunks →
execution_store.put 写 sqlite TEXT 时抛 UnicodeEncodeError →
整条 continue_task 作废(worker 实际已成功退出)。
同理,json.loads 不合并 "\\ud83c\\udf0a" 形式的转义,模型输出里的
emoji 进入任何严格 UTF-8 编码点都会炸。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.contracts import utf8_safe, utf8_safe_json
from core.execution_store import ExecutionStore
from core.terminal_runtime import TerminalStore
from utils.chat import _clean_args
from core.ceo import CEO


DIRTY_PAIR = "".join(chr(c) for c in (0xD83C, 0xDF0A))   # json.loads 不合并:两个码元
DIRTY_LONE = chr(0xD83C)                                 # 读边界/截断切开的孤立高位代理


def test_utf8_safe_combines_pairs_and_replaces_lone_surrogates():
    assert utf8_safe("abc" + DIRTY_PAIR + "def") == "abc\U0001F30Adef"
    assert utf8_safe("x" + DIRTY_LONE + "y") == "x\ufffdy"
    assert utf8_safe("干净文本") == "干净文本"
    assert utf8_safe(None) is None
    assert utf8_safe(123) == 123
    # 大字符串快速路径:干净文本原样返回(同对象)
    big = "好" * 50000
    assert utf8_safe(big) is big


def test_utf8_safe_json_walks_nested_structures():
    value = {"goal": "写小说" + DIRTY_PAIR, "items": ["a" + DIRTY_LONE, {"b": DIRTY_PAIR}]}
    cleaned = utf8_safe_json(value)
    assert cleaned["goal"] == "写小说\U0001F30A"
    assert cleaned["items"][0] == "a\ufffd"
    assert cleaned["items"][2 - 1]["b"] == "\U0001F30A"


def test_execution_store_put_survives_dirty_work_log(tmp_path):
    store = ExecutionStore(tmp_path / "executions.sqlite")
    store.put("exec-1", work_log="章节正文" + DIRTY_LONE + DIRTY_PAIR)
    # 落库文本被清洗;回读不再含任何代理字符
    saved = store.get("exec-1")
    assert saved["work_log"] == "章节正文\ufffd\U0001F30A"
    saved["work_log"].encode("utf-8")  # 不再抛 UnicodeEncodeError
    # 回读再写也不炸
    store.put("exec-1", state="interrupted", error="ok")
    assert store.get("exec-1")["work_log"] == "章节正文\ufffd\U0001F30A"


def test_terminal_store_append_survives_dirty_data(tmp_path):
    store = TerminalStore(tmp_path)
    store.create(attempt_id="a1", execution_id="e1", run_id=None, task_id=None,
                 profile="p", session_id=None)
    seq = store.append("a1", "data", {"text": "终端输出" + DIRTY_LONE})
    assert seq >= 1


def test_event_bus_emit_survives_dirty_payload(tmp_path):
    from web.event_bus import EventBus
    bus = EventBus()
    bus.configure_store(tmp_path / "events.sqlite")
    ev = bus.emit("hermes.line", line="流水" + DIRTY_LONE + DIRTY_PAIR)
    stored = bus.history_after(ev["id"] - 1, 10)[-1]
    assert stored["data"]["line"] == "流水\ufffd\U0001F30A"


def test_session_atomic_write_survives_dirty_history(tmp_path):
    from core.ceo import atomic_write_session
    payload = {"history": [{"role": "tool", "content": "结果" + DIRTY_LONE}]}
    path = tmp_path / "sess.json"
    atomic_write_session(path, payload)
    assert json.loads(path.read_text(encoding="utf-8"))["history"][0]["content"] == "结果\ufffd"


def test_clean_args_sanitizes_model_tool_arguments():
    raw = json.dumps({"instruction": "生成章节" + DIRTY_PAIR + " 结束"})
    args = _clean_args(raw)
    assert args["instruction"] == "生成章节\U0001F30A 结束"


def test_dispatch_infrastructure_error_exempts_budget_timeouts():
    wall = {"infra_failure": True, "outcome": "interrupted",
            "error": "Wall timeout (6383.87s)", "termination_reason": "wall_timeout"}
    idle = {"infra_failure": True, "outcome": "interrupted",
            "error": "No observed task activity (1800s)", "termination_reason": "inactivity_timeout"}
    legacy = {"infra_failure": True, "outcome": "interrupted", "error": "Wall timeout (971.76s)"}
    provider = {"infra_failure": True, "outcome": "interrupted", "error": "provider HTTP 500"}
    split = {"stages": 2, "results": [wall, provider]}

    assert CEO._dispatch_infrastructure_error(wall) == ""
    assert CEO._dispatch_infrastructure_error(idle) == ""
    assert CEO._dispatch_infrastructure_error(legacy) == ""
    assert CEO._dispatch_infrastructure_error(provider) == "provider HTTP 500"
    # 嵌套结果里 provider 故障仍然终止;只有预算超时被豁免
    budget_split = {"stages": 2, "results": [wall, idle]}
    assert CEO._dispatch_infrastructure_error(budget_split) == ""
    assert CEO._dispatch_infrastructure_error(split) == "provider HTTP 500"
