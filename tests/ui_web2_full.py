"""Web2 新版全流程测试（2026-09-02）：
  1. 欢迎页加载 + 无 JS 错误
  2. 欢迎页发任务 → CEO 回复 → 图谱轮次/节点创建 → 会话保存
  3. 点历史会话（163004）→ 图谱恢复 135 节点 → 点节点看详情面板
  4. 折叠侧栏（按钮左缘可点回）/ 展开
  5. 整理布局三态（tree→radial→恢复）
  6. 任务总览进出
  7. 节点管理视图
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8021/web2/"
TASK = "【web2测试】直接回复：新版链路OK。不要拆解任务。"
OUT = Path(__file__).resolve().parent / "web2_out"
ISSUES = []


def note(msg):
    print(f"  [注] {msg}", flush=True)


def issue(msg):
    ISSUES.append(msg)
    print(f"  [问题] {msg}", flush=True)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, channel="msedge")
        pg = b.new_page(viewport={"width": 1366, "height": 768})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
        pg.goto(BASE)
        pg.wait_for_selector("#wl-input", state="attached", timeout=20000)
        pg.wait_for_timeout(3500)
        note(f"加载: view={pg.evaluate('view')} 会话列表={pg.evaluate('document.querySelectorAll(\".hist-item\").length')}")

        # ---- 2) 欢迎页发任务 ----
        pg.fill("#wl-input", TASK)
        pg.click("#wl-send")
        t0 = time.time()
        done = False
        while time.time() - t0 < 90:
            pg.wait_for_timeout(2500)
            st = pg.evaluate("({busy: busyState, chat: document.querySelector('#chat-log').textContent.length})")
            if not st["busy"] and st["chat"] > 100:
                done = True
                break
        chats = pg.evaluate("""() => [...document.querySelectorAll('#chat-log > *')]
            .map(c => c.textContent.trim().slice(0, 60)).slice(-4)""")
        note(f"任务结束={done} 耗时{time.time()-t0:.0f}s")
        for c in chats: note(f"  聊: {c}")
        types = pg.evaluate("""() => { const o={}; for (const n of nodes.values()) o[n.type]=(o[n.type]||0)+1; return o; }""")
        note(f"图谱: {json.dumps(types, ensure_ascii=False)}")
        pg.screenshot(path=str(OUT / "1_task.png"))

        # ---- 3) 点历史会话 163004 ----
        pg.wait_for_timeout(1500)
        for it in pg.query_selector_all(".hist-item"):
            if "163004" in (it.get_attribute("data-name") or ""):
                it.click(); break
        pg.wait_for_timeout(4500)
        st = pg.evaluate("({nodes: nodes.size, cs: currentSession, title: document.querySelector('#graph-title').textContent})")
        note(f"163004: nodes={st['nodes']} cs={st['cs']} 标题={st['title']}")
        if st["nodes"] < 100:
            issue(f"163004 图谱节点过少: {st['nodes']}（期望 ~135）")
        pg.screenshot(path=str(OUT / "2_163004.png"))

        # 点图谱节点 → 详情面板
        pg.evaluate("""() => { const arr=[...nodes.values()]; const n=arr.find(x=>x.type==='round')||arr[0]; selectNode(n.key); }""")
        pg.wait_for_timeout(700)
        dt = pg.evaluate("document.querySelector('#detail').classList.contains('hidden') ? 'hidden' : 'open'")
        title = pg.evaluate("document.querySelector('#detail-title').textContent")
        note(f"详情面板: {dt}, 标题: {title}")
        if dt == "hidden":
            issue("点节点后面板未打开")
        # 点叉叉关闭面板
        pg.click("#detail-close")
        pg.wait_for_timeout(500)
        note("叉叉关闭面板: " + str(pg.evaluate("document.querySelector('#detail').classList.contains('hidden')")))

        # ---- 4) 折叠/展开 ----
        pg.click("#btn-collapse")
        pg.wait_for_timeout(600)
        pos = pg.evaluate("() => { const r=document.querySelector('#btn-collapse').getBoundingClientRect(); return {x:Math.round(r.x), txt:document.querySelector('#btn-collapse').textContent}; }")
        note(f"折叠后按钮: {pos}")
        if pos["x"] > 5: issue(f"折叠后按钮不在左缘 x={pos['x']}")
        pg.screenshot(path=str(OUT / "3_collapsed.png"))
        pg.click("#btn-collapse")
        pg.wait_for_timeout(400)
        note("展开OK")

        # ---- 5) 整理布局三态 ----
        pg.click("#btn-layout"); pg.wait_for_timeout(800)
        m1 = pg.evaluate("layoutMode")
        pg.click("#btn-layout"); pg.wait_for_timeout(800)
        m2 = pg.evaluate("layoutMode")
        pg.click("#btn-layout"); pg.wait_for_timeout(800)
        m3 = pg.evaluate("layoutMode")
        note(f"布局循环: {m1} → {m2} → {m3}")

        # ---- 6) 任务总览进出 ----
        pg.click("#btn-tasks")
        pg.wait_for_timeout(4000)
        ov = pg.evaluate("({view: view, nodes: nodes.size})")
        note(f"总览: view={ov['view']} nodes={ov['nodes']}")
        if ov["view"] != "overview": issue("未进入总览")
        pg.screenshot(path=str(OUT / "4_overview.png"))
        # 点总览中一个节点 → 进入会话
        pg.evaluate("""() => { const n=[...nodes.values()][0]; overviewClick(n.key); }""")
        pg.wait_for_timeout(4000)
        note(f"总览点节点后: view={pg.evaluate('view')} cs={pg.evaluate('currentSession')}")
        # 再进总览再退出(hist-new)
        pg.click("#btn-tasks")
        pg.wait_for_timeout(3000)
        pg.click("#btn-newsess")
        pg.wait_for_timeout(2500)
        note(f"newSession 后: view={pg.evaluate('view')} cs={pg.evaluate('currentSession')}")

        # ---- 7) 节点管理 ----
        pg.click("#btn-nodes")
        pg.wait_for_timeout(10000)
        cnt = pg.evaluate("document.querySelector('#nodes-count').textContent || ''")
        nlist = pg.evaluate("document.querySelectorAll('.node-card').length")
        note(f"节点管理: {cnt} 卡片={nlist}")
        pg.fill("#nodes-search", "ags_3")
        pg.wait_for_timeout(500)
        fl = pg.evaluate("document.querySelectorAll('.node-card').length")
        note(f"搜索 ags_3: {fl} 条")
        pg.click("#nodes-back")
        pg.wait_for_timeout(800)
        note(f"返回后 view={pg.evaluate('view')}")
        pg.screenshot(path=str(OUT / "5_final.png"))
        b.close()

    print(f"\npageerror: {len(errs)}")
    for e in errs[:5]: print("  -", e[:200])
    print(f"===== Web2 全流程: {len(ISSUES)} 问题 =====")
    for i in ISSUES: print("  [问题]", i)
    sys.exit(1 if ISSUES or errs else 0)


if __name__ == "__main__":
    main()
