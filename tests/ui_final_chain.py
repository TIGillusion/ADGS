"""最终任务链路验证（2026-08-30 干净服务端）：
欢迎页发任务 → 新会话 → CEO 回复 → 图谱/会话保存（进化已完成，应无等待）
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8021"
TASK = "【最终验证】直接回复：链路OK。不要拆解任务。"
OUT = Path(__file__).resolve().parent / "ui_final_out"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, channel="msedge")
        pg = b.new_page(viewport={"width": 1366, "height": 768})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:150]))
        pg.goto(BASE)
        pg.wait_for_selector("#wl-input", state="attached", timeout=30000)
        pg.wait_for_timeout(1500)
        pg.fill("#wl-input", TASK)
        pg.click("#wl-send")
        t0 = time.time()
        done = False
        while time.time() - t0 < 120:
            pg.wait_for_timeout(2500)
            st = pg.evaluate("({busy: busy, chat: document.querySelector('#chat-log').textContent.length})")
            if not st["busy"] and st["chat"] > 0:
                done = True
                break
        print(f"任务结束: {done} 耗时 {time.time()-t0:.0f}s")
        chats = pg.evaluate("""() => {
          const out = [];
          for (const c of document.querySelectorAll('#chat-log > *')) {
            const t = c.textContent.trim();
            if (t) out.push(t.slice(0, 80));
          }
          return out;
        }""")
        for c in chats[-4:]:
            print("  -", c)
        types = pg.evaluate("""() => {
          const o = {};
          for (const n of nodes.values()) o[n.type] = (o[n.type]||0)+1;
          return o;
        }""")
        print("图谱:", json.dumps(types, ensure_ascii=False))
        pg.screenshot(path=str(OUT / "final.png"))
        b.close()
    print("pageerror:", errs[:3] if errs else "无")
    sys.exit(0 if done and not errs else 1)


if __name__ == "__main__":
    main()
