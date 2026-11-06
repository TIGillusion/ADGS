"""欢迎页发任务 → 必须开启新对话（2026-08-30 会话隔离修复验证）。

复现用户场景：
  页面A：点历史会话 163004（服务端 _current_sess 残留指向它）
  页面B（新标签，欢迎页）：输入任务发送
旧行为：任务发到 163004（旧会话串台）
新行为（wlSend 强制 newSession）：任务开新会话 auto-2026...，163004 不被写入
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8021"
TASK = "【会话隔离测试】请直接回复：收到。不要拆解任务。"
OUT_DIR = Path(__file__).resolve().parent / "ui_sessiso_out"


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="msedge")

        # ---- 页面 A：点历史会话，制造 _current_sess 残留 ----
        pa = browser.new_page(viewport={"width": 1280, "height": 800})
        pa.goto(BASE)
        pa.wait_for_selector(".hist-item", state="attached", timeout=30000)
        pa.wait_for_timeout(1500)
        for it in pa.query_selector_all(".hist-item"):
            if "163004" in (it.get_attribute("data-name") or ""):
                it.click()
                break
        pa.wait_for_timeout(1500)
        print(f"[A] 已点历史会话，当前查看: {pa.evaluate('currentSession')}")

        # ---- 页面 B：欢迎页输入任务 ----
        pb = browser.new_page(viewport={"width": 1280, "height": 800})
        pb.goto(BASE)
        pb.wait_for_selector("#wl-input", state="attached", timeout=30000)
        pb.wait_for_timeout(1500)
        # 记录 163004 文件 mtime（验证不被写入）
        import subprocess
        m0 = subprocess.run(
            ["stat", "-c", "%Y", "data/sessions/auto-20260828-163004.json"],
            capture_output=True, text=True).stdout.strip()
        pb.fill("#wl-input", TASK)
        pb.click("#wl-send")
        print("[B] 欢迎页已发送任务，等待 CEO…")
        t0 = time.time()
        while time.time() - t0 < 90:
            pb.wait_for_timeout(2500)
            st = pb.evaluate("({busy: busy, nodes: nodes.size, cs: currentSession})")
            chat = pb.evaluate(
                "document.querySelector('#chat-log').textContent.length")
            if not st["busy"] and chat > 0:
                print(f"  [{time.time()-t0:.0f}s] 任务结束 currentSession={st['cs']!r} "
                      f"nodes={st['nodes']}")
                break

        # ---- 断言 ----
        s = pb.evaluate("""() => {
          const out = [];
          for (const c of document.querySelectorAll('#chat-log > *')) {
            const t = c.textContent.trim();
            if (t) out.push(t.slice(0, 80));
          }
          return out;
        }""")
        print(f"[B] 聊天气泡 {len(s)} 条: {s[-3:]}")
        m1 = subprocess.run(
            ["stat", "-c", "%Y", "data/sessions/auto-20260828-163004.json"],
            capture_output=True, text=True).stdout.strip()
        print(f"163004 mtime: {m0} -> {m1}（未变=未被写入旧会话）")
        shot = OUT_DIR / f"b_{int(time.time())}.png"
        pb.screenshot(path=str(shot))
        browser.close()

    ok = True
    if m0 != m1:
        print("\n失败：163004 被写入（任务发到了旧会话）！")
        ok = False
    # 新会话应出现在 sessions 列表
    import urllib.request
    j = json.loads(urllib.request.urlopen(BASE + "/api/sessions", timeout=5).read())
    names = [x["name"] for x in j.get("sessions", [])]
    newones = [n for n in names if n.startswith("auto-20260830")]
    print(f"新会话(auto-20260830*): {newones}")
    if not newones:
        print("失败：未见新会话")
        ok = False
    print("\nDONE:", "欢迎页发任务=新对话 ✓" if ok else "仍有串台!")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
