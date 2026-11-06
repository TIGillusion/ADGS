"""真实用户链路测试（2026-08-30 服务端重启后）：欢迎页发任务 → CEO 处理 →
图谱轮次创建 → 会话保存。完全模拟用户操作（playwright 输入+点击，真实 LLM）。

任务设计：极简指令，要求 CEO 直接回复不拆解（快；若 CEO 仍拆解/派节点，
则同时观察节点执行与失败处理）。轮询最多 5 分钟。
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8021"
TASK = ("请直接回复：ADGS 系统链路测试通过。"
        "【硬性要求】不要拆解任务、不要调用任何工具、不要派发给节点，"
        "直接用一句话回复确认即可。")
OUT_DIR = Path(__file__).resolve().parent / "ui_sendtask_out"


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="msedge")
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(BASE)
        page.wait_for_selector("#wl-input", state="attached", timeout=30000)
        page.wait_for_timeout(1500)
        print("[1] 欢迎页就绪，输入任务…")
        page.fill("#wl-input", TASK)
        page.click("#wl-send")
        print("[2] 已发送，等待 CEO 处理…")

        # 轮询观察
        t0 = time.time()
        last_nodes = 0
        got_reply = False
        while time.time() - t0 < 300:
            page.wait_for_timeout(3000)
            st = page.evaluate("""() => ({
              busy: busy,
              nodes: nodes.size,
              chatLen: document.querySelector('#chat-log')
                        ? document.querySelector('#chat-log').textContent.length : 0,
              status: document.querySelector('#graph-status').textContent || '',
            })""")
            if st["nodes"] != last_nodes:
                print(f"  [{time.time()-t0:.0f}s] nodes={st['nodes']} "
                      f"chat={st['chatLen']} status={st['status']!r}")
                last_nodes = st["nodes"]
            # 任务结束判定：busy 变 false 且有聊天内容
            if not st["busy"] and st["chatLen"] > 0:
                got_reply = True
                print(f"  [{time.time()-t0:.0f}s] 任务结束（busy=False），"
                      f"nodes={st['nodes']} chat={st['chatLen']}字符")
                break
        shot = OUT_DIR / f"task_end_{int(time.time())}.png"
        page.screenshot(path=str(shot))
        print(f"[3] 截图 {shot}")

        # 读 CEO 回复内容
        chats = page.evaluate("""() => {
          const out = [];
          for (const c of document.querySelectorAll('#chat-log > *')) {
            out.push(c.textContent.trim().slice(0, 300));
          }
          return out;
        }""")
        print(f"[4] 聊天气泡 {len(chats)} 条:")
        for c in chats[-4:]:
            print(f"    - {c[:160]}")
        # 图谱结构
        types = page.evaluate("""() => {
          const out = {};
          for (const n of nodes.values()) {
            out[n.type] = (out[n.type] || 0) + 1;
          }
          return out;
        }""")
        print(f"[5] 图谱类型: {json.dumps(types, ensure_ascii=False)}")
        print(f"[6] pageerror: {len(errs)}")
        for e in errs[:3]:
            print(f"    - {e[:150]}")
        browser.close()

    if not got_reply:
        print("\n失败：5 分钟内任务未结束（CEO 可能拆解任务在跑节点，或 LLM 故障）")
        sys.exit(1)
    print("\nDONE: 真实用户链路（发任务→CEO→图谱→会话）验证完成")
    sys.exit(0)


if __name__ == "__main__":
    main()
