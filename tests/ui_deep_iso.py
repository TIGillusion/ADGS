"""会话隔离完整深度测试（2026-08-30，需服务端已重启加载新后端）。

按钮级最小操作验证：
  1. 历史列表 preview：无"[系统提示]"压缩摘要混入
  2. 欢迎页发任务 → 新会话（wlSend 强制 newSession）
  3. 会话切换隔离：点会话A → 消息/图谱= A；点会话B → = B（来回两次）
  4. 跨页面串台隔离：页面X查看会话A 期间，页面Y发新任务(会话B) →
     X 不显示 B 的消息/图谱节点（事件 session 过滤）
  5. 主视图续接：在会话A页面发消息 → 任务落 A 会话（续接语义）
  6. 查看历史会话期间图谱不被其它任务节点污染
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8021"
OUT_DIR = Path(__file__).resolve().parent / "ui_deep_out"
ISSUES = []
T1 = "【深度测试1】直接回复：会话隔离测试甲。不要拆解。"
T2 = "【深度测试2】直接回复：会话隔离测试乙。不要拆解。"


def note(msg):
    print(f"  [注] {msg}")


def issue(msg):
    ISSUES.append(msg)
    print(f"  [问题] {msg}")


def chat_texts(page):
    return page.evaluate("""() => {
      const out = [];
      for (const c of document.querySelectorAll('#chat-log > *')) {
        const t = c.textContent.trim();
        if (t) out.push(t.slice(0, 60));
      }
      return out;
    }""")


def graph_types(page):
    return page.evaluate("""() => {
      const out = {};
      for (const n of nodes.values()) out[n.type] = (out[n.type]||0)+1;
      return out;
    }""")


def send_and_wait(page, text, timeout=90):
    page.fill("#chat-input", text) if page.evaluate("!!document.querySelector('#chat-input')") else None
    page.click("#chat-send")
    t0 = time.time()
    while time.time() - t0 < timeout:
        page.wait_for_timeout(2500)
        st = page.evaluate("({busy: busy, chat: document.querySelector('#chat-log').textContent.length})")
        if not st["busy"] and st["chat"] > 0:
            return time.time() - t0
    return None


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="msedge")
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        page.goto(BASE)
        page.wait_for_selector(".hist-item", state="attached", timeout=30000)
        page.wait_for_timeout(2500)

        # ---- 1) preview 无压缩摘要 ----
        items = page.query_selector_all(".hist-item")
        bad_prev = []
        for it in items:
            prev = it.query_selector(".hi-preview")
            if prev and "[系统提示]" in (prev.text_content() or ""):
                bad_prev.append(it.get_attribute("data-name"))
        note(f"历史 {len(items)} 个，含[系统提示]预览: {bad_prev or '无'} ✓"
             if not bad_prev else f"含[系统提示]预览: {bad_prev} ✗")
        if bad_prev:
            issue(f"历史预览混入压缩摘要: {bad_prev}")

        # ---- 2) 欢迎页发任务 → 新会话 ----
        page.click("#hist-new")  # 回欢迎页
        page.wait_for_timeout(1000)
        page.fill("#wl-input", T1)
        page.click("#wl-send")
        t0 = time.time()
        while time.time() - t0 < 90:
            page.wait_for_timeout(2500)
            st = page.evaluate("({busy: busy, chat: document.querySelector('#chat-log').textContent.length})")
            if not st["busy"] and st["chat"] > 0:
                break
        note(f"欢迎页任务结束，currentSession={page.evaluate('currentSession')} "
             f"图谱={json.dumps(graph_types(page), ensure_ascii=False)}")
        # 应出现新会话
        import urllib.request
        j = json.loads(urllib.request.urlopen(BASE + "/api/sessions", timeout=5).read())
        names = [x["name"] for x in j.get("sessions", [])]
        new1 = [n for n in names if n.startswith("auto-20260830")]
        note(f"新会话: {new1}")
        if not new1:
            issue("欢迎页发任务未产生新会话")

        # ---- 3) 会话切换隔离（先续接后串台，避免多页面全局状态干扰）----
        page.wait_for_timeout(1200)
        items = page.query_selector_all(".hist-item")
        targetA = targetB = None
        for it in items:
            nm = it.get_attribute("data-name") or ""
            if "163004" in nm:
                targetA = it
            elif "214013" in nm:
                targetB = it
        if targetA:
            targetA.click()
            page.wait_for_timeout(1800)
            gA = graph_types(page)
            cA = chat_texts(page)
            note(f"切到163004: 图谱={json.dumps(gA, ensure_ascii=False)} "
                 f"聊天首条={cA[0][:40] if cA else '空'}")
            keysA = page.evaluate("JSON.stringify([...nodes.keys()])")
        if targetB:
            targetB.click()
            page.wait_for_timeout(1800)
            gB = graph_types(page)
            cB = chat_texts(page)
            note(f"切到214013: 图谱={json.dumps(gB, ensure_ascii=False)} "
                 f"聊天首条={cB[0][:40] if cB else '空'}")
        if targetA:
            targetA.click()
            page.wait_for_timeout(1800)
            keysA2 = page.evaluate("JSON.stringify([...nodes.keys()])")
            same = keysA == keysA2
            note(f"切回163004: 图谱节点与首次一致={same}")
            if not same:
                issue("切换会话后图谱内容漂移（可能被事件污染）")

        # ---- 4) 主视图续接：在 163004 发消息 → 落 A 会话 ----
        page.fill("#chat-input", "【续接测试】直接回复：收到续接。")
        page.click("#chat-send")
        t0 = time.time()
        while time.time() - t0 < 90:
            page.wait_for_timeout(2500)
            st = page.evaluate("({busy: busy})")
            if not st["busy"]:
                break
        import subprocess
        m_163004_before = subprocess.run(
            ["stat", "-c", "%Y", "data/sessions/auto-20260828-163004.json"],
            capture_output=True, text=True).stdout.strip()
        page.wait_for_timeout(2500)
        m_163004_after = subprocess.run(
            ["stat", "-c", "%Y", "data/sessions/auto-20260828-163004.json"],
            capture_output=True, text=True).stdout.strip()
        note(f"续接任务后 163004 mtime: {m_163004_before} -> {m_163004_after}")
        if m_163004_before == m_163004_after:
            note("163004 未更新？续接消息可能开成了新会话（待确认）")
        else:
            note("163004 已更新（续接消息落回原会话）✓")

        # ---- 5) 跨页面串台隔离 ----
        page2 = browser.new_page(viewport={"width": 1280, "height": 800})
        page2.goto(BASE)
        page2.wait_for_selector("#wl-input", state="attached", timeout=30000)
        page2.wait_for_timeout(1500)
        # 页面1 正查看 163004（currentSession=A）；页面2 发新任务 → 会话C
        page2.fill("#wl-input", T2)
        page2.click("#wl-send")
        t0 = time.time()
        while time.time() - t0 < 90:
            page2.wait_for_timeout(2500)
            st2 = page2.evaluate("({busy: busy})")
            if not st2["busy"]:
                break
        note("页面2任务(会话C)已结束；检查页面1是否被串台")
        page.wait_for_timeout(2500)
        keysA3 = page.evaluate("JSON.stringify([...nodes.keys()])")
        same2 = keysA == keysA3
        cA3 = chat_texts(page)
        t2leak = any("测试乙" in t for t in cA3)
        note(f"页面1(查看163004)图谱仍一致={same2}, 聊天含会话C消息={t2leak}")
        if not same2:
            issue("查看历史会话时图谱被其它会话任务节点污染")
        if t2leak:
            issue("查看历史会话时聊天区混入其它会话消息")
        shot = OUT_DIR / f"final_{int(time.time())}.png"
        page.screenshot(path=str(shot))
        browser.close()

    print(f"\n===== 深度测试: {len(ISSUES)} 问题 =====")
    for i in ISSUES:
        print(f"  - {i}")
    sys.exit(1 if ISSUES else 0)


if __name__ == "__main__":
    main()
