"""browser-use 0.13.8 最小探针：启动 → 打开 ADGS web → 读 DOM/截图

验证 browser-use 新 API（CDP 模式）能否驱动真实浏览器访问本地页面。
"""
import asyncio
import sys


async def main():
    from browser_use import Browser

    print("[1] 启动 Browser(headless=True, channel=msedge)...", flush=True)
    browser = Browser(headless=True, channel="msedge",
                      enable_default_extensions=False,
                      minimum_wait_page_load_time=0.5,
                      wait_for_network_idle_page_load_time=1.0)
    await browser.start()
    print("[2] started, navigate...", flush=True)
    await browser.navigate_to("http://127.0.0.1:8021")
    await asyncio.sleep(3)
    print("[3] title:", await browser.get_current_page_title(), flush=True)
    st = await browser.get_state_as_text()
    print(f"[4] state text ({len(st)} chars):", flush=True)
    print(st[:800], flush=True)
    shot = "tests/ui_probe_shot.png"
    await browser.take_screenshot(shot)
    print(f"[5] screenshot -> {shot}", flush=True)
    await browser.stop()
    print("DONE", flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"PROBE FAIL: {type(e).__name__}: {e}", flush=True)
        sys.exit(1)
