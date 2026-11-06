"""恢复 001846 任务：loadSession 验证干净图谱 → 回答 CEO 提问 → 观察任务启动"""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8021"
SESS = "auto-20260831-001846"
ANSWER = ("对，首页按钮点进去就是 home 页面（8100 端口）。首页三模块（采集进度/"
          "候选人画像/运营看板）需要真正联动起来。你继续推进，不用再确认，"
          "直到三模块都真实可用、数据互通为止。")
OUT = Path(__file__).resolve().parent / "ui_resume_out"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, channel="msedge")
        pg = b.new_page(viewport={"width": 1366, "height": 768})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:150]))
        pg.goto(BASE)
        pg.wait_for_selector(".hist-item", state="attached", timeout=20000)
        pg.wait_for_timeout(1500)
        for it in pg.query_selector_all(".hist-item"):
            if SESS in (it.get_attribute("data-name") or ""):
                it.click()
                break
        pg.wait_for_timeout(4000)
        st = pg.evaluate("""() => ({
          title: document.querySelector('#graph-title').textContent,
          nodes: nodes.size,
          rounds: [...nodes.values()].filter(n=>n.type==='round').map(n => n.key + ':' + (n.extra||{}).user_text?.slice(0,24) || ''),
          labels: [...nodes.values()].map(n => n.type + ':' + n.label).slice(0, 10),
        })""")
        print("图谱标题:", st["title"])
        print("节点数:", st["nodes"], "| 轮次:", st["rounds"])
        print("节点标签:", st["labels"])
        pg.screenshot(path=str(OUT / "1_clean_graph.png"))

        # 回答 CEO 提问（模拟用户在输入框输入发送）
        pg.fill("#chat-input", ANSWER)
        pg.click("#chat-send")
        print("\n已发送回答，等待 CEO 继续…")
        t0 = time.time()
        last_n = 0
        while time.time() - t0 < 240:  # 观察 4 分钟
            pg.wait_for_timeout(15000)
            busy = pg.evaluate("busy")
            n = pg.evaluate("nodes.size")
            chat = pg.evaluate("document.querySelector('#chat-log').textContent.length")
            if n != last_n:
                print(f"  [{time.time()-t0:.0f}s] busy={busy} nodes={n} chat={chat} (节点数变化)", flush=True)
                last_n = n
            if not busy and time.time() - t0 > 30:
                print(f"  [{time.time()-t0:.0f}s] 任务结束 busy=False nodes={n}")
                break
        pg.screenshot(path=str(OUT / "2_after_answer.png"))
        # 最近聊天
        chats = pg.evaluate("""() => [...document.querySelectorAll('#chat-log > *')]
            .map(c => c.textContent.trim().slice(0, 70)).slice(-5)""")
        for c in chats:
            print("  -", c)
        b.close()
    print("pageerror:", len(errs))
    for e in errs[:3]:
        print("  -", e)


if __name__ == "__main__":
    main()
