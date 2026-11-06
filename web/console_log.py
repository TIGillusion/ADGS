"""Read-only console viewer shared by the launcher and web frontend process."""
import datetime
import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_STRUCTURAL = "(json_extract(payload,'$.type') GLOB 'graph.*' OR json_extract(payload,'$.type') GLOB 'node.*' OR json_extract(payload,'$.type') GLOB 'a2a.*' OR json_extract(payload,'$.type') GLOB 'chat.*')"


def _read_only(path):
    return sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=2)


def read_console_events(path, after=None, tail=20):
    """Read only; snapshot the upper cursor before querying so no arrivals are lost."""
    with _read_only(Path(path)) as conn:
        upper=conn.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]
        if after is None:
            # Use the existing sparse structural index instead of scanning millions
            # of raw terminal records merely to open a viewer.
            rows=conn.execute('SELECT id,payload FROM events WHERE '+_STRUCTURAL+' AND id<=? ORDER BY id DESC LIMIT ?',
                              (upper,max(0,tail)*10)).fetchall() if tail else []
            lines=[line for _,payload in reversed(rows) if (line:=_format_event(json.loads(payload)))]
            return upper,lines[-tail:] if tail else []
        rows=conn.execute('SELECT id,payload FROM events WHERE id>? AND id<=? ORDER BY id LIMIT 1000',(after,upper)).fetchall()
        lines=[]
        for _,payload in rows:
            try:line=_format_event(json.loads(payload))
            except (ValueError,TypeError,OSError):continue
            if line:lines.append(line)
        return (rows[-1][0] if len(rows)==1000 else upper),lines


def console_activity(directory):
    """Summarize current attempts even when only filtered terminal bytes arrive."""
    try:
        with _read_only(Path(directory)/'terminals.sqlite') as conn:
            # Old hosts may have left unclosed attempts. They are historical
            # records, not evidence that a process is still working today.
            rows=conn.execute("SELECT profile,last_activity FROM attempts WHERE status IN ('starting','running','stopping') AND last_activity>=? ORDER BY started DESC LIMIT 8",(time.time()-86400,)).fetchall()
        if rows:
            return '活动执行：'+'；'.join(f'{profile}（最近活动 {max(0,int(time.time()-(activity or time.time())))} 秒前）' for profile,activity in rows)
        return '当前无活动执行终端，等待 CEO 决策或新任务'
    except (sqlite3.Error,OSError):
        return '等待运行时与执行记录'


def run_console(poll_interval=.6, state_dir=None, tail=20, stop_event=None, output=None, status_interval=30):
    """Independent observer: exiting the viewer never stops or starts work."""
    directory=Path(state_dir or os.environ.get('ADGS_STATE_DIR',str(ROOT/'.adgs_runtime')))
    stop=stop_event or threading.Event()
    stream=output or sys.stdout
    def write(text):
        try:stream.write(text+'\n');stream.flush()
        except UnicodeEncodeError:
            stream.write((text+'\n').encode(stream.encoding or 'utf8',errors='replace').decode(stream.encoding or 'utf8'));stream.flush()
    cursor=None
    waiting=False
    last_output=time.monotonic()
    try:
        write('[ADGS 日志] 已连接共享运行记录；关闭本窗口只停止看日志，后台任务继续运行。')
        write('[状态] '+console_activity(directory))
        while not stop.is_set():
            try:
                cursor,lines=read_console_events(directory/'events.sqlite',cursor,tail)
                waiting=False
                for line in lines:write(line)
                if lines:last_output=time.monotonic()
            except (sqlite3.Error,OSError):
                if not waiting:write('[日志] 等待共享事件记录…');waiting=True
            if time.monotonic()-last_output>=status_interval:
                write('['+datetime.datetime.now().strftime('%H:%M:%S')+'] [状态] '+console_activity(directory))
                last_output=time.monotonic()
            stop.wait(poll_interval)
    except (KeyboardInterrupt,BrokenPipeError,OSError):
        return

_CONSOLE_EVENT_TYPES = {
    "chat.user": "用户",
    "chat.recovery_user": "事故恢复 · 用户",
    "chat.recovery_reply": "事故恢复 · CEO",
    "chat.ceo_ask": "CEO 提问",
    "chat.ceo_report": "CEO 汇报",
    "chat.ceo_finish": "CEO 完成",
    "chat.ceo_end": "CEO 结束",
    "chat.ceo_tool": "CEO 工具",
    "graph.task": "派任务",
    "graph.dispatch": "派节点",
    "node.brain": "节点大脑",
    "node.status": "节点状态",
    "node.eval": "节点验收",
    "node.rework": "节点返工",
    "a2a.delegate": "A2A委派",
    "a2a.verdict": "A2A复核",
    "system": "系统",
    "evolve.round": "进化",
}


def _short(text, width: int = 20) -> str:
    """单行化并截前 width 字（用户指定的摘要长度）。"""
    if text is None:
        return ""
    text = " ".join(str(text).split())
    return text[:width] + ("…" if len(text) > width else "")


def _format_event(ev: dict):
    """事件 → 控制台一行摘要；噪音事件返回 None。纯函数（可单测）。"""
    etype = ev.get("type", "")
    label = _CONSOLE_EVENT_TYPES.get(etype)
    if not label:
        return None
    data = ev.get("data") or {}
    ts = datetime.datetime.fromtimestamp(float(ev.get("ts") or time.time())).strftime("%H:%M:%S")

    if etype in ("chat.user", "chat.recovery_user", "chat.recovery_reply"):
        body = _short(data.get("text"))
    elif etype == "chat.ceo_ask":
        body = _short(data.get("question"))
    elif etype in ("chat.ceo_report", "chat.ceo_finish"):
        body = f"[{data.get('status', '进度')}]" if etype == "chat.ceo_finish" else ""
        body += _short(data.get("summary"), 1200 if data.get("status") in ("blocked", "failed") else 20)
    elif etype == "chat.ceo_end":
        body = f"{data.get('status', '')}: {_short(data.get('reason') or data.get('error') or data.get('summary'), 1200)}"
    elif etype == "chat.ceo_tool":
        body = _short(data.get("name"), 30)
    elif etype == "graph.task":
        body = f"「{_short(data.get('name'))}」{_short(data.get('goal'))}"
    elif etype == "graph.dispatch":
        body = f"{data.get('node')} ← 「{_short(data.get('task'), 24)}」 {_short(data.get('goal'))}"
    elif etype == "node.brain":
        body = f"{data.get('node')} {data.get('action')}: {_short(data.get('detail') or data.get('reason'))}"
    elif etype == "node.status":
        body = f"{data.get('node')} → {data.get('status')}: {_short(data.get('detail'))}"
    elif etype == "node.eval":
        passed = data.get("passed")
        mark = "通过" if passed else "未通过"
        body = f"{data.get('node')} {mark}" + (f" {data.get('score')} 分" if data.get("score") is not None else "")
    elif etype == "node.rework":
        body = f"{data.get('node')} 第 {data.get('reworks')} 轮返工"
    elif etype == "a2a.delegate":
        body = (f"{data.get('parent')} → {data.get('node')} [{data.get('status')}]: "
                f"{_short(data.get('summary'))}")
    elif etype == "a2a.verdict":
        mark = "通过" if data.get("passed") else "未通过"
        body = f"{data.get('parent')} 复核 {data.get('node')}：{mark}｜{_short(data.get('detail'))}"
    elif etype == "system":
        body = _short(data.get("msg"), 60)
    elif etype == "evolve.round":
        body = (f"繁衍 {len(data.get('births') or [])} 淘汰 {len(data.get('eliminations') or [])}"
                f"（{data.get('n_before')}→{data.get('n_after')}）")
    else:
        return None
    return f"[{ts}] [{label}] {body}".rstrip()


