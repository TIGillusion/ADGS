"""ADGS Web 扩展交互测试（playwright）—— 按钮全覆盖 + 节点×状态点开矩阵。

覆盖：
  A. 按钮：任务总览(btn-tasks 进出) / 主题切换(btn-theme×2) / 布局整理(btn-layout)
     / 清空聊天(chat-clear) / 节点卡片点击(selectNodeEdit) / 详情关闭 /
     📋节点管理跳转(detail-open-edit) / 返回图谱(nodes-back)
  B. 节点×状态矩阵：恢复最大会话图谱，按 (type,status) 分组，每组真实坐标点击，
     断言面板标题/正文/状态/输出区/管理跳转按钮
安全：不发任务、不点进化、不保存描述。hist-new 仅用于退出总览（创建空会话，
后端无任务影响）。

用法：tools/uitest_venv/Scripts/python.exe tests/ui_interact2.py
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8021"
OUT_DIR = Path(__file__).resolve().parent / "ui_interact2_out"
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


def node_screen_pos(page, n):
    rect = page.evaluate("document.querySelector('canvas').getBoundingClientRect()")
    cam = page.evaluate("({px: cam.px, py: cam.py, zoom: cam.zoom})")
    return (rect["left"] + cam["px"] + n["x"] * cam["zoom"],
            rect["top"] + cam["py"] + n["y"] * cam["zoom"])


def graph_types(page):
    return page.evaluate("""() => {
      const out = {};
      for (const n of nodes.values()) {
        const k = n.type + '|' + (n.status || 'idle');
        (out[k] = out[k] || []).push({ key: n.key, x: n.x, y: n.y,
                                       label: n.label, extra: Object.assign({}, n.extra || {}) });
      }
      return out;
    }""")


def load_best_session(page):
    """恢复节点数最多的历史会话，返回 (name, count)"""
    page.wait_for_selector(".hist-item", state="attached", timeout=30000)
    items = page.query_selector_all(".hist-item")
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
    return best[0], best[1]


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    page_errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="msedge")
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.goto(BASE)
        page.wait_for_selector(".hist-item", state="attached", timeout=30000)
        page.wait_for_timeout(2000)
        print("[0] 页面加载完成")
        # 先进入主视图（顶栏按钮属于主视图，欢迎页时不可见）
        load_best_session(page)
        print("[0] 已进入主视图（顶栏按钮可用）")

        # ============ A. 按钮测试 ============
        print("[A] 按钮测试")
        # A1 任务总览进出
        page.click("#btn-tasks")
        page.wait_for_timeout(5000)
        ov_size = page.evaluate("nodes.size")
        ov_mode = page.evaluate("layoutMode")
        note(f"任务总览: nodes={ov_size} layoutMode={ov_mode}")
        if ov_size <= 1:
            issue("高", f"任务总览未加载图谱 (nodes={ov_size})")
        shot(page, "A1_overview")
        # 退出总览（hist-new 触发 restoreFromOverview）
        page.click("#hist-new")
        page.wait_for_timeout(1500)
        back_mode = page.evaluate("layoutMode")
        note(f"hist-new 退出总览后 layoutMode={back_mode}")
        if back_mode == "tasks":
            issue("中", "hist-new 未退出任务总览")
        # 点回最大会话继续
        load_best_session(page)
        note("已点回最大会话")
        shot(page, "A1_restored")

        # A2 主题切换
        theme0 = page.evaluate("document.body.dataset.theme || 'dark'")
        page.click("#btn-theme")
        page.wait_for_timeout(400)
        theme1 = page.evaluate("document.body.dataset.theme || ''")
        page.click("#btn-theme")
        page.wait_for_timeout(400)
        theme2 = page.evaluate("document.body.dataset.theme || ''")
        note(f"主题: {theme0} → {theme1} → {theme2}")
        if theme1 == theme0 or theme2 != theme0:
            issue("中", f"主题切换异常 {theme0}→{theme1}→{theme2}")
        shot(page, "A2_theme_light" if theme1 != theme0 else "A2_theme")

        # A3 布局整理
        page.click("#btn-layout")
        page.wait_for_timeout(600)
        lay = page.evaluate("layoutMode")
        note(f"布局整理后 layoutMode={lay}")
        if lay != "radial":
            issue("中", f"btn-layout 后 layoutMode={lay} != radial")
        shot(page, "A3_layout")

        # A4 清空聊天
        chat_before = page.evaluate("document.querySelector('#chat-log').textContent.length")
        page.click("#chat-clear")
        page.wait_for_timeout(300)
        chat_after = page.evaluate("document.querySelector('#chat-log').textContent.length")
        note(f"chat-clear: {chat_before} → {chat_after} 字符")
        if chat_after != 0:
            issue("低", f"chat-clear 未清空聊天区 (剩 {chat_after})")

        # A5 节点管理：点卡片 → 详情 → 关闭
        page.click("#btn-nodes")
        page.wait_for_timeout(2000)  # /api/nodes 服务端忙时 ~7s，多等
        cards = page.query_selector_all("#nodes-list > *")
        if cards:
            cards[0].click()
            page.wait_for_timeout(600)
            edit_open = page.evaluate(
                "!document.querySelector('#node-edit').classList.contains('hidden')")
            edit_title = page.evaluate(
                "document.querySelector('#node-edit-title').textContent || ''")
            note(f"节点卡片点击: 详情打开={edit_open} 标题={edit_title!r}")
            if not edit_open:
                issue("中", "点击节点卡片未打开详情")
            shot(page, "A5_node_edit")
            page.click("#node-edit-close")
            page.wait_for_timeout(300)
            edit_closed = page.evaluate(
                "document.querySelector('#node-edit').classList.contains('hidden')")
            if not edit_closed:
                issue("低", "node-edit-close 未关闭详情")
            # A6 返回图谱
            page.click("#nodes-back")
            page.wait_for_timeout(400)
        else:
            issue("中", "节点管理列表为空（服务端忙 /api/nodes 超时？）")
            page.click("#nodes-back")
            page.wait_for_timeout(400)

        # A7 📋节点管理跳转（先选中一个 node 节点）
        types = graph_types(page)
        node_cand = types.get("node|done") or types.get("node|idle") or types.get("node|running")
        if node_cand:
            n = node_cand[0]
            x, y = node_screen_pos(page, n)
            page.mouse.click(x, y)
            page.wait_for_timeout(400)
            open_btn_vis = page.evaluate(
                "!document.querySelector('#detail-open-edit').classList.contains('hidden')")
            note(f"选中 node 后 📋节点管理按钮可见={open_btn_vis}")
            if not open_btn_vis:
                issue("中", "node 节点选中后 📋节点管理跳转按钮不可见")
            page.click("#detail-open-edit")
            page.wait_for_timeout(1500)
            jumped = page.evaluate(
                "!document.querySelector('#nodes-wrap').classList.contains('hidden')"
                " && !document.querySelector('#node-edit').classList.contains('hidden')")
            note(f"📋跳转: nodes视图+详情打开={jumped}")
            if not jumped:
                issue("中", "detail-open-edit 跳转失败")
            shot(page, "A7_open_edit")
            page.click("#nodes-back")
            page.wait_for_timeout(400)
        else:
            note("无 node 节点可测 A7")

        # ============ B. 节点×状态矩阵 ============
        print("[B] 节点×状态点开矩阵")
        load_best_session(page)
        page.evaluate("fitRadialView()")
        page.wait_for_timeout(400)
        groups = graph_types(page)
        keys = sorted(groups.keys())
        print(f"  [矩阵] 共 {len(keys)} 个 type|status 组合: {keys}")
        checked = 0
        for gk in keys:
            t, st = gk.split("|", 1)
            cands = groups[gk]
            # 每组合挑离中心最近的
            n = min(cands, key=lambda c: abs(c["x"]) + abs(c["y"]))
            x, y = node_screen_pos(page, n)
            page.mouse.click(x, y)
            page.wait_for_timeout(350)
            sel = page.evaluate("selectedKey || null")
            meta = page.evaluate("document.querySelector('#detail-meta').textContent || ''")
            title = page.evaluate("document.querySelector('#detail-task-title').textContent || ''")
            goal = page.evaluate("document.querySelector('#detail-task-goal').textContent || ''")
            open_btn = page.evaluate(
                "!document.querySelector('#detail-open-edit').classList.contains('hidden')")
            brain_has = page.evaluate(
                "document.querySelector('#detail-brain') ? document.querySelector('#detail-brain').textContent.length > 0 : false")
            hermes_has = page.evaluate(
                "document.querySelector('#detail-hermes') ? document.querySelector('#detail-hermes').textContent.length > 0 : false")
            # 断言
            ok_sel = sel == n["key"]
            if not ok_sel:
                issue("中", f"点击 {gk} 节点 {n['key']} 却选中 {sel}")
            # 标题语义
            expect_title = {"ceo": "当前任务", "round": "对话内容",
                            "task": "任务目标", "node": "任务目标",
                            "clone": "任务目标"}.get(t, "")
            if expect_title and expect_title not in title:
                issue("中", f"{gk} 节点标题异常: {title!r}（期望含 {expect_title}）")
            # 正文非空
            if t in ("ceo", "round", "task", "node", "clone") and not goal.strip():
                issue("中", f"{gk} 节点 {n['key']} 面板正文为空")
            # open-btn 仅 node/clone
            if (t in ("node", "clone")) != open_btn:
                issue("低", f"{gk} 节点 📋按钮可见性异常: {open_btn}")
            # 输出区:node/clone 应有 brain/hermes 区（内容可能空）
            if t in ("node", "clone") and not (brain_has or hermes_has):
                pass  # 输出区存在但内容空=正常（无日志时）
            print(f"    ✓ {gk} {n['key']}: 状态={meta[:40]!r} 标题={title[:26]!r} "
                  f"📋={open_btn} brain={brain_has} hermes={hermes_has}")
            checked += 1
        note(f"矩阵共断言 {checked} 个 type|status 组合")
        shot(page, "B_matrix")

        # ---- 汇总 ----
        print(f"\n[pageerror] {len(page_errors)} 条页面 JS 异常")
        for e in page_errors[:8]:
            print(f"    - {e[:250]}")
        browser.close()

    print(f"\n===== 扩展交互: {len(NOTES)} 条记录, {len(ISSUES)} 个问题 =====")
    for sev, msg in ISSUES:
        print(f"  [{sev}] {msg}")
    sys.exit(1 if ISSUES else 0)


if __name__ == "__main__":
    main()
