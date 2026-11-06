"""模拟用户点击观察节点生长与名称（2026-08-31，只读，不干扰用户任务）。

流程：
  1. 打开页面 → 恢复用户正在跑的会话(auto-20260831-001846)
  2. 每 8s 采样图谱：节点数 / 类型分布 / 各节点 key+label+status（观察生长）
  3. 中途用鼠标坐标点击 round/task/node 各类型节点 → 读详情面板
     （验证名称与任务目标正确）
  4. 输出生长时间线
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8021"
SESS = "auto-20260831-001846"
OUT = Path(__file__).resolve().parent / "ui_growth_out"
SAMPLES = []


def node_screen_pos(page, n):
    rect = page.evaluate("document.querySelector('canvas').getBoundingClientRect()")
    cam = page.evaluate("({px: cam.px, py: cam.py, zoom: cam.zoom})")
    return (rect["left"] + cam["px"] + n["x"] * cam["zoom"],
            rect["top"] + cam["py"] + n["y"] * cam["zoom"])


def sample(page, tag):
    st = page.evaluate("""() => {
      const types = {}, statuses = {};
      const nodesList = [];
      for (const n of nodes.values()) {
        types[n.type] = (types[n.type] || 0) + 1;
        statuses[n.status] = (statuses[n.status] || 0) + 1;
        nodesList.push({ key: n.key, type: n.type, label: n.label,
                         status: n.status, x: n.x, y: n.y });
      }
      return { types, statuses, nodes: nodesList, cs: currentSession };
    }""")
    SAMPLES.append((tag, st))
    types = st["types"]
    running = [n for n in st["nodes"] if n["status"] == "running"]
    print(f"[{tag}] nodes={len(st['nodes'])} types={json.dumps(types, ensure_ascii=False)} "
          f"running={[n['label'] for n in running][:4]}", flush=True)
    return st


def click_node(page, n, tag):
    x, y = node_screen_pos(page, n)
    page.mouse.click(x, y)
    page.wait_for_timeout(500)
    sel = page.evaluate("selectedKey")
    title = page.evaluate("""() => {
      const el = document.querySelector('#detail-task-goal');
      return el ? el.textContent.trim().slice(0, 90) : '(无面板)';
    }""")
    print(f"  点[{tag}] {n['key']} @({x:.0f},{y:.0f}) → selected={sel!r} "
          f"面板: {title[:80]!r}", flush=True)
    return sel


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
        # 恢复用户会话
        for it in pg.query_selector_all(".hist-item"):
            if SESS in (it.get_attribute("data-name") or ""):
                it.click()
                break
        pg.wait_for_timeout(4000)
        st = sample(pg, "t0")
        print("  会话:", st["cs"], "| 图谱标题:", pg.evaluate(
            "document.querySelector('#graph-title').textContent"), flush=True)

        clicked = set()
        for i in range(1, 11):  # ~80s 观察
            pg.wait_for_timeout(8000)
            st = sample(pg, f"t{i}")
            # 每轮尝试点击一个未点过的新节点（round → task → node → clone 优先）
            if i % 2 == 0:
                for want in ("round", "task", "node", "clone", "ceo"):
                    cand = next((n for n in st["nodes"]
                                 if n["type"] == want and n["key"] not in clicked), None)
                    if cand:
                        clicked.add(cand["key"])
                        click_node(pg, cand, want)
                        break
            # 截图快照（每 3 轮一张）
            if i % 3 == 0:
                pg.screenshot(path=str(OUT / f"growth_{i:02d}.png"))
        pg.screenshot(path=str(OUT / "growth_final.png"))
        b.close()

    print("\n===== 节点生长时间线 =====")
    prev = 0
    for tag, st in SAMPLES:
        delta = len(st["nodes"]) - prev
        prev = len(st["nodes"])
        labels = [(n["type"], n["label"]) for n in st["nodes"][:12]]
        print(f"  {tag}: +{delta} 共{len(st['nodes'])} 前12: {labels}")
    print("\n===== 节点名称样例 =====")
    if SAMPLES:
        last = SAMPLES[-1][1]["nodes"]
        for n in last[:14]:
            print(f"  {n['type']:6s} {n['key'][:38]:40s} label={n['label'][:30]!r} status={n['status']}")
    print("pageerror:", len(errs))
    for e in errs[:3]:
        print("  -", e)


if __name__ == "__main__":
    main()
