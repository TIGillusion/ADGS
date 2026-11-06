"""ADGS Web 真实交互模拟（playwright + 系统 Edge）——模拟用户一步步操作。

操作流（全部只读/无副作用，不发任务、不点进化）：
  1. 打开页面，全程收集 console 错误 / 页面 JS 异常
  2. 点历史会话恢复最大图谱
  3. Canvas 真实坐标点击：CEO / 轮次 / 序号 / 节点，断言面板文本
  4. 点空白处关闭面板
  5. 拖拽节点（mousedown+move+up）、拖动画布、滚轮缩放
  6. 节点管理弹窗开合、折叠边栏、输入框打字
  7. 每步截图，最后输出问题清单

用法：tools/uitest_venv/Scripts/python.exe tests/ui_interact.py
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8021"
OUT_DIR = Path(__file__).resolve().parent / "ui_interact_out"
ISSUES, NOTES = [], []


def issue(sev: str, msg: str):
    ISSUES.append((sev, msg))
    print(f"  [问题 {sev}] {msg}")


def note(msg: str):
    NOTES.append(msg)
    print(f"  [注] {msg}")


def shot(page, tag: str):
    p = OUT_DIR / f"{tag}_{int(time.time()*1000)}.png"
    page.screenshot(path=str(p))
    print(f"  [截图] {p.name}")
    return p


def node_screen_pos(page, n):
    rect = page.evaluate("document.querySelector('canvas').getBoundingClientRect()")
    cam = page.evaluate("({px: cam.px, py: cam.py, zoom: cam.zoom})")
    return (rect["left"] + cam["px"] + n["x"] * cam["zoom"],
            rect["top"] + cam["py"] + n["y"] * cam["zoom"])


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    console_errors = []
    page_errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="msedge")
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.goto(BASE)
        page.wait_for_selector(".hist-item", state="attached", timeout=30000)
        page.wait_for_timeout(2500)
        print("[1] 页面加载完成")
        shot(page, "01_load")

        # ---- 2) 恢复最大图谱 ----
        items = page.query_selector_all(".hist-item")
        print(f"[2] 历史会话 {len(items)} 个，逐个点开统计节点数")
        best = None
        for it in items:
            name = it.get_attribute("data-name")
            it.click()
            page.wait_for_timeout(1200)
            cnt = page.evaluate("nodes.size")
            note(f"会话 {name}: {cnt} 节点")
            if best is None or cnt > best[1]:
                best = (name, cnt, it)
        if not best:
            issue("高", "没有任何会话恢复出图谱")
            browser.close()
            return
        # 若 best 不是最后点的会话，点回 best
        if page.evaluate("nodes.size") < best[1]:
            best[2].click()
            page.wait_for_timeout(1500)
        note(f"选用会话 {best[0]}（{best[1]} 节点）继续")
        page.evaluate("fitRadialView()")
        page.wait_for_timeout(500)
        shot(page, "02_graph_best")

        # ---- 3) Canvas 坐标点击各类型节点 ----
        types = page.evaluate("""() => {
          const out = {};
          for (const n of nodes.values()) {
            if (!out[n.type]) out[n.type] = [];
            out[n.type].push({ key: n.key, x: n.x, y: n.y, label: n.label,
                               status: n.status });
          }
          return out;
        }""")
        print(f"[3] 图谱类型: { {k: len(v) for k, v in types.items()} }")
        # 每类挑一个离中心近的节点点击
        for t in ["ceo", "round", "task", "node", "clone"]:
            cands = types.get(t) or []
            if not cands:
                continue
            n = min(cands, key=lambda c: abs(c["x"]) + abs(c["y"]))
            x, y = node_screen_pos(page, n)
            page.mouse.click(x, y)
            page.wait_for_timeout(400)
            sel = page.evaluate("selectedKey || null")
            raw = page.evaluate(
                "document.querySelector('#detail-task-goal').textContent || ''"
                " + '||' + (document.querySelector('#detail-task-title').textContent || '')")
            parts = raw.split("||", 1)
            title, goal = (parts + [""])[:2]
            visible = page.evaluate(
                "!!document.querySelector('#detail') && !document.querySelector('#detail').classList.contains('hidden')")
            print(f"    - 点击 {t} 节点 {n['key']} ({n['label']}): "
                  f"selected={sel!r} 面板可见={visible} 标题={title!r}")
            if not visible:
                issue("中", f"点击 {t} 节点 {n['key']} 后面板未显示")
            elif sel != n["key"]:
                issue("中", f"点击 {t} 节点 {n['key']} 却选中了 {sel}")
            shot(page, f"03_click_{t}")
        # 点空白处关闭
        page.mouse.click(1400, 500)
        page.wait_for_timeout(300)
        closed = page.evaluate(
            "document.querySelector('#detail').classList.contains('hidden')")
        note(f"点空白处关闭面板: {closed}")
        if not closed:
            issue("低", "点空白处后面板未关闭")

        # ---- 4) 拖拽测试 ----
        # 4a 拖动画布
        page.mouse.move(1200, 400)
        page.mouse.down()
        page.mouse.move(1250, 450, steps=8)
        page.mouse.up()
        cam1 = page.evaluate("({px: cam.px, py: cam.py})")
        note(f"拖动画布后 cam={cam1}")
        page.wait_for_timeout(300)
        # 4b 拖拽一个节点（选中 node 类型）
        if types.get("node"):
            n = min(types["node"], key=lambda c: abs(c["x"]) + abs(c["y"]))
            x, y = node_screen_pos(page, n)
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x + 60, y + 40, steps=8)
            page.mouse.up()
            page.wait_for_timeout(300)
            pinned = page.evaluate("(k) => nodes.get(k).pinned", n["key"])
            note(f"拖拽节点 {n['key']} 后 pinned={pinned}")
            if not pinned:
                issue("中", f"拖拽节点 {n['key']} 后 pinned 未置位（拖动未生效）")
        # 4c 滚轮缩放
        page.mouse.move(1200, 500)
        page.mouse.wheel(0, -300)
        page.wait_for_timeout(300)
        zoom_after = page.evaluate("cam.zoom")
        note(f"滚轮缩放后 zoom={zoom_after:.3f}")
        shot(page, "04_drag_zoom")

        # ---- 5) 节点管理视图切换 ----
        page.click("#btn-nodes")
        page.wait_for_timeout(800)
        nodes_view = page.evaluate(
            "!document.querySelector('#nodes-wrap').classList.contains('hidden')"
            " && document.querySelector('#graph-wrap').classList.contains('hidden')")
        nodes_count = page.evaluate(
            "document.querySelector('#nodes-count').textContent || ''")
        note(f"节点管理视图: 显示={nodes_view} 节点数={nodes_count}")
        if not nodes_view:
            issue("中", "点击节点管理未切换到 nodes 视图")
        shot(page, "05_nodes_view")
        # 搜索框输入（真实交互）
        page.fill("#nodes-search", "ags_")
        page.wait_for_timeout(400)
        list_cnt = page.evaluate(
            "document.querySelectorAll('#nodes-list .node-card, #nodes-list > div').length")
        note(f"搜索 'ags_' 后列表条目: {list_cnt}")
        # 返回图谱
        page.click("#nodes-back")
        page.wait_for_timeout(400)
        back_ok = page.evaluate(
            "!document.querySelector('#graph-wrap').classList.contains('hidden')"
            " && document.querySelector('#nodes-wrap').classList.contains('hidden')")
        note(f"返回图谱: {back_ok}")
        if not back_ok:
            issue("中", "节点管理返回图谱失败")

        # ---- 6) 折叠边栏 + 输入框打字 ----
        page.click("#btn-collapse")
        page.wait_for_timeout(300)
        collapsed = page.evaluate("document.querySelector('#sidebar').classList.contains('collapsed')")
        note(f"折叠边栏: {collapsed}")
        if not collapsed:
            issue("低", "点击折叠边栏未生效")
        page.click("#btn-collapse")  # 恢复
        page.wait_for_timeout(200)
        # 输入框打字（不发送）：主视图用 #chat-input（#wl-input 是欢迎页的，隐藏）
        ta = page.query_selector("#chat-input")
        if ta:
            ta.fill("测试输入不发送")
            page.wait_for_timeout(200)
            val = page.evaluate("document.querySelector('#chat-input').value")
            note(f"输入框内容: {val!r}")
            # 清空，避免误发
            page.evaluate("document.querySelector('#chat-input').value = ''")
        shot(page, "06_input")

        # ---- 7) 面板/布局异常检查 ----
        page.evaluate("fitRadialView()")
        page.wait_for_timeout(400)
        shot(page, "07_final")

        # ---- 汇总 ----
        print(f"\n[console] 收集到 {len(console_errors)} 条 console 错误/警告:")
        seen = set()
        for e in console_errors[:15]:
            if e not in seen:
                seen.add(e)
                print(f"    - {e[:200]}")
        print(f"[pageerror] 收集到 {len(page_errors)} 条页面 JS 异常:")
        for e in page_errors[:10]:
            print(f"    - {e[:300]}")
        browser.close()

    print(f"\n===== 交互模拟完成: {len(NOTES)} 条记录, {len(ISSUES)} 个问题 =====")
    for sev, msg in ISSUES:
        print(f"  [{sev}] {msg}")
    sys.exit(1 if ISSUES else 0)


if __name__ == "__main__":
    main()
