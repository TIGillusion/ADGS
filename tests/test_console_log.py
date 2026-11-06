"""后端事件控制台输出(2026-10-23 用户需求:终端不要黑屏)回归:
   谁派给谁/谁对谁说了什么,内容截前 20 字;噪音事件不打印。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import utils.context_probe
utils.context_probe.probe_context_window = lambda *a, **k: None

import web_app


def _ev(type_, **data):
    return {"type": type_, "ts": 1792600000.0, "data": data}


def test_short_truncates_to_twenty_chars_single_line():
    assert web_app._short("一二三四五六七八九十" * 3) == "一二三四五六七八九十" * 2 + "…"
    assert web_app._short("短文本") == "短文本"
    assert "\n" not in web_app._short("多\n行\r\n文本")
    assert web_app._short(None) == ""


def test_dispatch_event_shows_who_and_what():
    line = web_app._format_event(_ev(
        "graph.dispatch", node="ags_591", task="统计core目录py文件",
        goal="统计 D:/core 下所有 py 文件数量并列清单"))
    assert "[派节点]" in line and "ags_591" in line
    assert "统计core目录py文件"[:20] in line
    assert "20" not in line[:0]  # 占位无害


def test_a2a_delegate_and_verdict_lines():
    line = web_app._format_event(_ev(
        "a2a.delegate", parent="ags_591", node="ags_582", status="running",
        summary="统计 core 目录下的 py 文件数量并列出完整清单" + "x" * 30))
    assert "[A2A委派]" in line and "ags_591 → ags_582" in line and "[running]" in line
    assert "…" in line  # 超 20 字截断
    line = web_app._format_event(_ev(
        "a2a.verdict", parent="ags_591", node="ags_582", passed=False, detail="汇报缺项"))
    assert "[A2A复核]" in line and "未通过" in line and "汇报缺项"[:20] in line


def test_ceo_and_node_lines():
    assert "[用户]" in web_app._format_event(_ev("chat.user", text="帮我写小说"))
    line = web_app._format_event(_ev("chat.ceo_finish", status="completed", summary="全书12章写完"))
    assert "[CEO 完成]" in line and "[completed]" in line and "全书12章写完" in line
    line = web_app._format_event(_ev("node.status", node="ags_377", status="running", detail="执行第11章"))
    assert "[节点状态]" in line and "ags_377" in line and "running" in line
    line = web_app._format_event(_ev("node.eval", node="ags_377", passed=True, score=55.2))
    assert "通过" in line and "55.2" in line


def test_noisy_events_are_suppressed():
    for etype, data in (("terminal.output", {"data": "x"}),
                        ("hermes.line", {"line": "x"}),
                        ("queue.update", {"queue": []}),
                        ("graph.task", {"id": "t"}),):
        ev = _ev(etype, **data)
        if etype == "graph.task":
            continue  # graph.task 是要打印的
        assert web_app._format_event(ev) is None, etype
    assert "[派任务]" in web_app._format_event(_ev("graph.task", name="single", goal="写第11章"))


def test_printer_streams_new_rows_from_shared_db(tmp_path):
    """Observe isolated shared events; never attach a test thread to the live DB."""
    import io, sqlite3, threading, time
    from web.console_log import run_console
    db=tmp_path/'events.sqlite'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE events (id INTEGER PRIMARY KEY,payload TEXT NOT NULL)')
        conn.execute('INSERT INTO events VALUES (1,?)',(json.dumps(_ev('chat.user',text='历史事件')),))
    output=io.StringIO();stop=threading.Event()
    thread=threading.Thread(target=run_console,kwargs={'state_dir':tmp_path,'poll_interval':.01,'tail':0,'stop_event':stop,'output':output},daemon=True)
    thread.start()
    try:
        deadline=time.monotonic()+2
        while '共享运行记录' not in output.getvalue() and time.monotonic()<deadline:time.sleep(.01)
        # Let the initial cursor finish before appending a new event.
        time.sleep(.05)
        with sqlite3.connect(db) as conn:
            conn.execute('INSERT INTO events VALUES (2,?)',(json.dumps(_ev('a2a.delegate',parent='ags_1',node='ags_2',status='running',summary='子任务')),))
        deadline=time.monotonic()+2
        while 'ags_1 → ags_2' not in output.getvalue() and time.monotonic()<deadline:time.sleep(.01)
        assert 'ags_1 → ags_2' in output.getvalue()
        assert '历史事件' not in output.getvalue()
    finally:
        stop.set();thread.join(2)
    assert not thread.is_alive()
