"""browser-use 确定性工具模拟用户操作（2026-08-30，无 LLM Agent）。

用 browser_use.Browser + Element API 真实驱动浏览器：
  1. 打开页面 → 观察欢迎页状态
  2. 欢迎页输入任务(Element.fill) → 点击发送(Element.click) → 等待 CEO 回复
  3. 点历史会话 → 图谱恢复 → 点图谱节点/顶栏按钮/折叠侧栏
  4. 每步：状态文本 + 截图
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from browser_use import Browser

BASE = "http://127.0.0.1:8021"
OUT = Path(__file__).resolve().parent / "bu_ops_out"
TASK = "【browser-use 工具测试】直接回复：收到。不要拆解任务。"


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    browser = Browser(headless=True, enable_default_extensions=False,
                      minimum_wait_page_load_time=0.5,
                      wait_for_network_idle_page_load_time=1.0)
    await browser.start()
    print("[1] Browser 启动 OK", flush=True)
    await browser.navigate_to(BASE)
    await asyncio.sleep(2.5)

    page = await browser.get_current_page()

    # ---- 欢迎页观察 ----
    st = await browser.get_state_as_text()
    print("[2] 页面状态(前200字):", st[:200].replace("\n", " "), flush=True)

    # ---- 欢迎页输入 + 发送（真实用户操作）----
    els = await page.get_elements_by_css_selector("#wl-input")
    print(f"[3] wl-input 元素: {len(els)} 个", flush=True)
    if not els:
        print("找不到 wl-input，放弃", flush=True)
        await browser.stop()
        return
    await els[0].fill(TASK)
    await asyncio.sleep(0.3)
    send = await page.get_elements_by_css_selector("#wl-send")
    await send[0].click()
    print("[4] 已发送任务，等待 CEO…", flush=True)

    # ---- 等待任务结束（固定等待 + 容错读取，CDP 偶发断线）----
    await asyncio.sleep(18)
    done = False
    for _ in range(5):
        try:
            busy = await page.evaluate("() => busy")
            chat = await page.evaluate("() => document.querySelector('#chat-log').textContent.length")
            if not busy and chat > 0:
                done = True
                break
        except Exception:
            await asyncio.sleep(3)
    print(f"[5] 任务结束={done}", flush=True)
    chats = await page.evaluate(
        "() => [...document.querySelectorAll('#chat-log > *')].map(c => c.textContent.trim().slice(0, 70))")
    print("[5] 聊天(最后5条):", flush=True)
    for c in chats[-5:]:
        print("   -", c, flush=True)
    await browser.take_screenshot(str(OUT / "1_welcome_task.png"))
    print(f"[6] 截图 1_welcome_task.png（任务结束={done}）", flush=True)

    # ---- 点历史会话（163004）----
    hist = await page.get_elements_by_css_selector(".hist-item")
    print(f"[7] 历史会话条目: {len(hist)} 个", flush=True)
    target = None
    for it in hist:
        try:
            name = await asyncio.wait_for(it.get_attribute("data-name"), 3) or ""
        except Exception:
            name = ""
        if "163004" in name:
            target = it
            break
    if target:
        try:
            await asyncio.wait_for(target.click(), 5)
            await asyncio.sleep(2.5)
            cs = await page.evaluate("() => currentSession")
            ncount = await page.evaluate("() => nodes.size")
            print(f"[8] 点163004后: currentSession={cs!r} nodes={ncount}", flush=True)
            await browser.take_screenshot(str(OUT / "2_hist_session.png"))
        except Exception as e:
            print(f"[8] 点会话失败: {str(e)[:100]}", flush=True)
    else:
        print("[8] 未找到 163004 条目", flush=True)

    # ---- 点顶栏按钮（任务总览 / 整理布局 / 主题）----
    for btn_id, label in [("btn-tasks", "任务总览"), ("btn-layout", "整理布局")]:
        try:
            btns = await page.get_elements_by_css_selector(f"#{btn_id}")
            if btns:
                await asyncio.wait_for(btns[0].click(), 5)
                await asyncio.sleep(1.2)
                mode = await page.evaluate("() => layoutMode")
                print(f"[9] 点{label}: layoutMode={mode}", flush=True)
            else:
                print(f"[9] {label} 按钮不可见", flush=True)
        except Exception as e:
            print(f"[9] {label} 操作失败: {str(e)[:80]}", flush=True)
    # 退出总览
    try:
        new_btn = await page.get_elements_by_css_selector("#hist-new")
        if new_btn:
            await asyncio.wait_for(new_btn[0].click(), 5)
            await asyncio.sleep(1.2)
            print("[10] hist-new 退出总览", flush=True)
    except Exception as e:
        print(f"[10] 退出总览失败: {str(e)[:80]}", flush=True)

    # ---- 折叠侧栏 ----
    try:
        col = await page.get_elements_by_css_selector("#btn-collapse")
        if col:
            await asyncio.wait_for(col[0].click(), 5)
            await asyncio.sleep(0.8)
            bbox = await page.evaluate(
                "() => { const r = document.querySelector('#btn-collapse').getBoundingClientRect(); return {x: Math.round(r.x), txt: document.querySelector('#btn-collapse').textContent}; }")
            print(f"[11] 折叠后按钮: {bbox}", flush=True)
            await browser.take_screenshot(str(OUT / "3_collapsed.png"))
            await asyncio.wait_for(col[0].click(), 5)  # 展开
            await asyncio.sleep(0.5)
            print("[12] 已展开", flush=True)
    except Exception as e:
        print(f"[11/12] 折叠操作失败: {str(e)[:80]}", flush=True)

    # ---- 图谱节点：用坐标点击一个节点（canvas 非 DOM）----
    try:
        n_raw = await page.evaluate("""() => {
          const arr = [...nodes.values()];
          const pick = arr.find(x => x.type === 'round') || arr[0];
          const rect = document.querySelector('canvas').getBoundingClientRect();
          return JSON.stringify({x: rect.left + cam.px + pick.x * cam.zoom,
                  y: rect.top + cam.py + pick.y * cam.zoom,
                  key: pick.key, label: pick.label});
        }""")
        import json as _json
        n = _json.loads(n_raw) if isinstance(n_raw, str) else n_raw
        print(f"[13] 点击图谱节点 {n['key']} @ ({n['x']:.0f},{n['y']:.0f})", flush=True)
        mouse = await page.mouse  # browser-use 的 mouse 是 async 属性
        await asyncio.wait_for(mouse.click(int(n["x"]), int(n["y"])), 5)
        await asyncio.sleep(1.0)
        sel = await page.evaluate("() => selectedKey")
        print(f"[14] 点击后 selectedKey={sel!r}（期望 {n['key']}）", flush=True)
        await browser.take_screenshot(str(OUT / "4_node_click.png"))
    except Exception as e:
        print(f"[13/14] 节点点击失败: {str(e)[:100]}", flush=True)

    # ---- 节点管理 ----
    try:
        nm = await page.get_elements_by_css_selector("#btn-nodes")
        if nm:
            await asyncio.wait_for(nm[0].click(), 5)
            await asyncio.sleep(12)  # /api/nodes 大响应加载慢（已知 ~12s）
            cnt = await page.evaluate("() => document.querySelector('#nodes-count').textContent || ''")
            print(f"[15] 节点管理: {cnt}", flush=True)
            await browser.take_screenshot(str(OUT / "5_nodes.png"))
    except Exception as e:
        print(f"[15] 节点管理失败: {str(e)[:80]}", flush=True)

    await browser.stop()
    print("\n[browser-use 工具操作] 完成", flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {str(e)[:300]}", flush=True)
        sys.exit(1)
