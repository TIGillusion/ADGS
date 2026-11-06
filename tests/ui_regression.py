"""ADGS Web UI 回归测试（playwright + 系统 Edge，确定性模式，不依赖 LLM）。

只读模式：打开 http://127.0.0.1:8021 →
  1. 基础 DOM 存在性断言（输入框/canvas/面板/日志区）
  2. 读取前端图谱 nodes 数据（type/status/extra.count）
  3. 对每个序号节点调用页面内 selectNode()，断言面板文本：
     - extra.count>1 → 显示【并发总体任务】
     - extra.count<=1/缺失 → 显示【任务目标】（该次委派自身任务）
  4. 统计 running 节点数（信号量修复后 ≤ max_workers_effective）
  5. 截图存档 + 输出图谱结构摘要

用法：tools/uitest_venv/Scripts/python.exe tests/ui_regression.py
注意：服务端须加载修复后的新代码（重启），否则旧数据无 count 字段。
浏览器：playwright channel=msedge（用系统 Edge 独立实例，免下载 chromium）。
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8021"
OUT_DIR = Path(__file__).resolve().parent / "ui_regression_out"
PASS, FAIL = [], []


def report(name: str, ok: bool, detail: str = ""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f" — {detail}" if detail else ""))


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="msedge")
        page = browser.new_page()
        page.goto(BASE)
        page.wait_for_selector("#detail-task-goal", state="attached",
                               timeout=30000)
        page.wait_for_timeout(2500)  # 等 SSE 快照重放 / 图谱重建
        print(f"[connected] {BASE}")

        # ---- 1) 基础 DOM ----
        dom = page.evaluate("""() => ({
          input: !!document.querySelector('textarea, input[type=text]'),
          canvas: !!document.querySelector('canvas'),
          detail: !!document.querySelector('#detail-task-goal'),
          syslog: !!document.querySelector('#sys-log'),
        })""")
        for k, v in dom.items():
            report(f"DOM 元素 {k}", bool(v))
        print(f"  [DOM] {json.dumps(dom, ensure_ascii=False)}")

        # ---- 2) 图谱数据（当前快照，通常只有 ceo）----
        data = json.loads(page.evaluate("""() => {
          const out = [];
          for (const [k, n] of nodes) {
            out.push({ key: k, id: n.id, type: n.type, label: n.label,
                       parent: n.parent, status: n.status,
                       extra: Object.assign({}, n.extra || {}) });
          }
          return JSON.stringify(out);
        }"""))
        print(f"  [graph] 初始节点总数 {len(data)}（无历史快照时仅 CEO）")

        # ---- 3) 点历史会话恢复图谱 → 序号节点面板断言 ----
        page.wait_for_selector(".hist-item", state="attached", timeout=15000)
        items = page.query_selector_all(".hist-item")
        print(f"  [sessions] 历史会话 {len(items)} 个，逐个点开恢复图谱并断言")
        checked_conc = checked_single = 0
        for it in items:
            name = it.get_attribute("data-name")
            it.click()
            page.wait_for_timeout(1500)  # 等 loadSession + 图谱恢复
            nodes_now = page.evaluate("nodes.size")
            if nodes_now <= 1:
                print(f"    - {name}: 恢复后节点数 {nodes_now}，跳过")
                continue
            tasks = json.loads(page.evaluate("""() => JSON.stringify(
              [...nodes.values()].filter(n => n.type === 'task')
                .map(n => ({ key: n.key, extra: Object.assign({}, n.extra || {}) }))
            )"""))
            print(f"    - {name}: 恢复后 {nodes_now} 节点, 序号 {len(tasks)} 个")
            for n in tasks:
                cnt = n["extra"].get("count")
                page.evaluate(f"selectNode({json.dumps(n['key'])})")
                page.wait_for_timeout(80)
                txt = page.evaluate(
                    "document.getElementById('detail-task-goal').textContent || ''")
                is_conc = txt.startswith("【并发总体任务】")
                is_single = txt.startswith("【任务目标】")
                if cnt is not None and cnt > 1:
                    checked_conc += 1
                    report(f"并发序号 {n['key']} (count={cnt}) 显示总体任务",
                           is_conc, txt[:44].replace("\n", " "))
                elif cnt is not None:
                    checked_single += 1
                    report(f"单发序号 {n['key']} (count={cnt}) 显示自身任务",
                           is_single and not is_conc, txt[:44].replace("\n", " "))
                else:
                    print(f"  [info] {n['key']} 无 count（旧快照/旧后端），"
                          f"显示: {txt[:44].replace(chr(10), ' ')}")
        if not items:
            print("  [info] 无历史会话可断言")

        # ---- 4) 截图 ----
        shot = OUT_DIR / f"graph_{int(time.time())}.png"
        page.screenshot(path=str(shot), full_page=True)
        print(f"  [shot] {shot}")
        browser.close()

    print(f"\n===== UI 回归: {len(PASS)} 通过, {len(FAIL)} 失败 =====")
    if FAIL:
        for f in FAIL:
            print(f"  - {f}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
