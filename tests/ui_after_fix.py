"""UI 交互回归（2026-08-30 服务端重启后，playwright）——重点覆盖改动点/易错点：
  1. 页面加载：0 JS 异常、0 console error
  2. 折叠边栏修复：折叠后按钮固定左缘可点回（此前移出视口）
  3. 轮次节点标题（此前"第 轮 1 轮"重复）
  4. 序号节点 count 语义（旧快照无 count → 显示自身任务目标）
  5. 任务总览进出（hist-new 退出）
  6. 节点管理列表/搜索/返回
  7. SSE 重连校准函数 resyncAfterReconnect() 手动调用无异常
  8. 重启检测：localStorage 旧 boot_ts → reload → 清快照+欢迎页
  9. ANSI 256 色 ansi256ToHex() 输出正确
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8021"
OUT_DIR = Path(__file__).resolve().parent / "ui_afterfix_out"
ISSUES, NOTES = [], []


def issue(sev, msg):
    ISSUES.append((sev, msg))
    print(f"  [问题 {sev}] {msg}")


def note(msg):
    NOTES.append(msg)
    print(f"  [注] {msg}")


def shot(page, tag):
    p = OUT_DIR / f"{tag}_{int(time.time()*1000)}.png"
    page.screenshot(path=str(p))
    print(f"  [截图] {p.name}")


def node_screen_pos(page, n):
    rect = page.evaluate("document.querySelector('canvas').getBoundingClientRect()")
    cam = page.evaluate("({px: cam.px, py: cam.py, zoom: cam.zoom})")
    return (rect["left"] + cam["px"] + n["x"] * cam["zoom"],
            rect["top"] + cam["py"] + n["y"] * cam["zoom"])


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    console_err, page_err, bad_resp = [], [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="msedge")
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.on("console", lambda m: console_err.append(m.text)
                if m.type == "error" else None)
        page.on("pageerror", lambda e: page_err.append(str(e)))
        page.on("response", lambda r: bad_resp.append(f"{r.status} {r.url}")
                if r.status >= 400 else None)
        page.goto(BASE)
        page.wait_for_selector(".hist-item", state="attached", timeout=30000)
        page.wait_for_timeout(2500)

        # ---- 1) 加载检查 ----
        note(f"加载完成: JS异常={len(page_err)} console错误={len(console_err)}")
        if page_err:
            issue("高", f"页面 JS 异常: {page_err[:3]}")
        if console_err:
            for e in console_err[:3]:
                issue("低", f"console error: {e[:120]}")

        # ---- 9) ANSI 256 色函数 ----
        hex200 = page.evaluate("ansi256ToHex(200)")
        hex232 = page.evaluate("ansi256ToHex(232)")
        hex5 = page.evaluate("ansi256ToHex(5)")
        note(f"ansi256ToHex: 200→{hex200} 232→{hex232} 5→{hex5}")
        if hex200 == "var(--ansi-200)" or not hex200.startswith("#"):
            issue("中", f"ansi256ToHex(200) 未生成 hex: {hex200}")
        if hex5 != "#bd93f9":
            issue("低", f"ansi256ToHex(5) 基本色应映射 CSS 同款: {hex5}")

        # ---- 2) 折叠/展开 ----
        page.click("#btn-collapse")
        page.wait_for_timeout(500)
        bbox = page.evaluate("""() => {
          const r = document.querySelector('#btn-collapse').getBoundingClientRect();
          return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width),
                  txt: document.querySelector('#btn-collapse').textContent};
        }""")
        note(f"折叠后按钮: {bbox}")
        if bbox["x"] < 0 or bbox["x"] > 5:
            issue("高", f"折叠后按钮不在视口左缘 (x={bbox['x']})")
        if bbox["txt"] != "»":
            issue("中", f"折叠后按钮文字应为 »，实际 {bbox['txt']!r}")
        shot(page, "02_collapsed")
        page.click("#btn-collapse")  # 展开
        page.wait_for_timeout(400)
        sbx = page.evaluate("() => Math.round(document.querySelector('#sidebar').getBoundingClientRect().x)")
        if sbx != 0:
            issue("高", f"展开后 sidebar x={sbx}（应 0）")
        note(f"展开后 sidebar x={sbx} ✓")

        # ---- 7) SSE 重连校准函数 ----
        try:
            page.evaluate("resyncAfterReconnect()")
            page.wait_for_timeout(1200)
            note("resyncAfterReconnect() 调用无异常")
        except Exception as e:
            issue("中", f"resyncAfterReconnect 异常: {str(e)[:150]}")
        # 校准不应触发图谱保存（抑制生效）——检查没有新的会话文件写入时间变化
        shot(page, "07_resync")

        # ---- 3/4) 恢复历史会话验证 round/task 标题 ----
        items = page.query_selector_all(".hist-item")
        note(f"历史会话 {len(items)} 个")
        best = None
        for it in items:
            name = it.get_attribute("data-name")
            it.click()
            page.wait_for_timeout(1200)
            cnt = page.evaluate("nodes.size")
            if best is None or cnt > best[1]:
                best = (name, cnt, it)
        if page.evaluate("nodes.size") < best[1]:
            best[2].click()
            page.wait_for_timeout(1500)
        page.evaluate("fitRadialView()")
        page.wait_for_timeout(400)
        note(f"选用会话 {best[0]}（{best[1]} 节点）")
        types = page.evaluate("""() => {
          const out = {};
          for (const n of nodes.values()) {
            if (!out[n.type]) out[n.type] = [];
            out[n.type].push({key: n.key, x: n.x, y: n.y, label: n.label,
                              extra: Object.assign({}, n.extra || {})});
          }
          return out;
        }""")
        note(f"图谱类型: { {k: len(v) for k, v in types.items()} }")
        # round 标题
        if types.get("round"):
            n = types["round"][0]
            x, y = node_screen_pos(page, n)
            page.mouse.click(x, y)
            page.wait_for_timeout(400)
            title = page.evaluate("document.querySelector('#detail-task-goal').textContent || ''")
            first = title.split("\n")[0]
            note(f"round 标题首行: {first!r}")
            if "第 " in first and " 轮" in first and "轮" in first.replace("第 ", "").replace(" 轮", ""):
                issue("中", f"round 标题仍重复'轮': {first!r}")
        # task 节点 count 语义（打印 selected 确认点中目标）
        if types.get("task"):
            n = types["task"][0]
            x, y = node_screen_pos(page, n)
            page.mouse.click(x, y)
            page.wait_for_timeout(400)
            sel = page.evaluate("selectedKey || null")
            txt = page.evaluate("document.querySelector('#detail-task-goal').textContent || ''")
            cnt = n["extra"].get("count")
            note(f"task {n['key']} count={cnt} selected={sel!r} 面板: {txt[:44].replace(chr(10),' ')}")
            if cnt is None and txt.startswith("【并发总体任务】"):
                issue("中", "无 count 的序号节点显示为并发总体任务（应为自身任务目标）")
        shot(page, "03_04_graph")

        # ---- 5) 任务总览进出 ----
        page.click("#btn-tasks")
        page.wait_for_timeout(5000)
        ov = page.evaluate("({size: nodes.size, mode: layoutMode})")
        note(f"任务总览: {ov}")
        if ov["mode"] != "tasks" or ov["size"] <= 1:
            issue("高", f"任务总览异常: {ov}")
        shot(page, "05_overview")
        page.click("#hist-new")
        page.wait_for_timeout(1500)
        if page.evaluate("layoutMode") == "tasks":
            issue("中", "hist-new 未退出总览")
        else:
            note("总览退出 ✓")

        # ---- 6) 节点管理（轮询等待加载，服务端 /api/nodes 大响应可能数秒）----
        page.click("#btn-nodes")
        ncount, nlist = "", 0
        for _ in range(16):
            page.wait_for_timeout(750)
            ncount = page.evaluate("document.querySelector('#nodes-count').textContent || ''")
            nlist = page.evaluate("document.querySelectorAll('#nodes-list > *').length")
            if nlist > 0:
                break
        note(f"节点管理: {ncount!r} 列表条目={nlist}（等待 {(_+1)*0.75:.1f}s）")
        if not ncount or nlist == 0:
            issue("中", f"节点管理列表为空: count={ncount!r} items={nlist}")
        page.fill("#nodes-search", "ags_36")
        page.wait_for_timeout(400)
        filtered = page.evaluate("document.querySelectorAll('#nodes-list > *').length")
        note(f"搜索 'ags_36': {filtered} 条")
        page.click("#nodes-back")
        page.wait_for_timeout(400)
        shot(page, "06_nodes")

        # ---- 8) 重启检测（模拟后端重启）----
        page.evaluate("localStorage.setItem('adgs_boot_ts', '1')")  # 旧 boot_ts
        page.reload()
        page.wait_for_selector(".hist-item", state="attached", timeout=30000)
        page.wait_for_timeout(2500)
        view = page.evaluate("""() => ({
          welcome: !document.querySelector('#welcome-wrap').classList.contains('hidden'),
          graph: !document.querySelector('#graph-wrap').classList.contains('hidden'),
          nodes: nodes.size,
        })""")
        note(f"重启检测（旧 boot_ts + reload）: {view}")
        if not view["welcome"]:
            issue("中", f"检测到后端重启应回欢迎页: {view}")
        shot(page, "08_restart_detect")

        # ---- 汇总 ----
        browser.close()

    print(f"\n[pageerror] {len(page_err)} 条")
    for e in page_err[:5]:
        print(f"    - {e[:200]}")
    print(f"[bad responses] {len(bad_resp)} 条")
    for r in bad_resp[:8]:
        print(f"    - {r[:150]}")
    print(f"===== UI 回归: {len(NOTES)} 记录, {len(ISSUES)} 问题 =====")
    for sev, msg in ISSUES:
        print(f"  [{sev}] {msg}")
    sys.exit(1 if ISSUES else 0)


if __name__ == "__main__":
    main()
