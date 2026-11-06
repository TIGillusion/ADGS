"""001846 猎头系统任务监工脚本（cron + 人工轮询共用）。

输出一行状态：busy / 队列 / 001846 文件图谱节点数 / 最近结构事件摘要。
判定：
  RUNNING   busy=True（任务执行中）
  WAIT_ASK  busy=False 且最后事件是 CEO 提问（等用户回答）
  DONE      busy=False 队列空 且最后事件含"任务结束"（本轮完成）
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8021"
SESS = "auto-20260831-001846"


def main():
    try:
        with urllib.request.urlopen(BASE + "/api/state", timeout=8) as r:
            j = json.loads(r.read())
    except Exception as e:
        print(f"STATE_ERR 服务端不可达: {str(e)[:80]}")
        return 1
    evs = j.get("events", [])
    busy = j.get("busy")
    qlen = j.get("queue_len")
    # 最近事件（hermes.line 刷屏时结构事件可能被挤出 60 条窗口，直接看末尾）
    last = evs[-1] if evs else None
    last_txt = ""
    if last:
        d = last.get("data", {})
        last_txt = str(d.get("text") or d.get("msg") or d.get("id")
                       or d.get("node") or d.get("summary") or d.get("question")
                       or d.get("status") or d.get("line") or "")[:80]
    # 最后 400 条里的结构事件（评估进展）
    struct = [e for e in evs[-400:]
              if e["type"] in ("chat.user", "graph.task", "graph.dispatch",
                               "node.status", "chat.ceo_ask", "chat.ceo_finish",
                               "chat.ceo_end")]
    recent_struct = struct[-2:] if struct else []
    # 001846 文件
    sess_file = Path("data/sessions/auto-20260831-001846.json")
    g = 0
    mtime = 0
    if sess_file.exists():
        try:
            jf = json.loads(sess_file.read_text(encoding="utf-8"))
            g = len(jf.get("graph") or [])
            mtime = sess_file.stat().st_mtime
        except Exception:
            pass
    now = time.time()
    age = f"{now - last.get('ts', now):.0f}s前" if last else "-"
    if busy:
        state = "RUNNING"
    elif qlen:
        state = "QUEUE"
    elif last and last["type"] == "chat.ceo_ask":
        state = "WAIT_ASK"
    else:
        state = "DONE"
    rs = "; ".join(f"{e['type']}:{str(e.get('data',{}).get('id') or e.get('data',{}).get('node') or e.get('data',{}).get('status') or '')[:24]}"
                   for e in recent_struct)
    print(f"[{state}] busy={busy} queue={qlen} 图谱={g}节点 "
          f"最近[{age}] {last['type'] if last else '-'}: {last_txt}"
          + (f" | 结构: {rs}" if rs else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
