"""browser-use Agent 演示（2026-08-30）：LLM 驱动的"像人一样"操作。

用 deepseek-v4-flash 驱动 browser-use Agent 打开 ADGS web 页面，
自主观察元素、点击、描述所见。browser-use 0.13.8 + langchain ChatOpenAI。
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from browser_use import Agent, Browser
from browser_use.llm.openai.chat import ChatOpenAI

TASK = (
    "打开 http://127.0.0.1:8021 这个页面。"
    "1) 先观察页面：列出你看到的所有按钮、输入框及其文字；"
    "2) 在欢迎页输入框输入'请直接回复：browser-use 测试通过。不要拆解任务。'并点击发送；"
    "3) 等待几秒，观察界面变化（是否有图谱节点、聊天区是否有回复）；"
    "4) 最后描述你看到的结果。"
)


async def main():
    cfg = json.load(open("config/AI_config.json", encoding="utf-8"))
    # deepseek 不支持 browser-use 强制的 response_format json_schema(400)，
    # 用本地 vLLM qwen(实测支持 json_schema)
    llm_cfg = cfg["worker"]
    llm = ChatOpenAI(model=llm_cfg["model"], base_url=llm_cfg["url"],
                     api_key=llm_cfg["key"], temperature=0.1)
    browser = Browser(headless=True, enable_default_extensions=False,
                      minimum_wait_page_load_time=0.5,
                      wait_for_network_idle_page_load_time=1.0)
    agent = Agent(task=TASK, llm=llm, browser=browser)
    print(f"[browser-use Agent] 开始任务（模型 {llm_cfg['model']} @ vLLM）…", flush=True)
    history = await agent.run()
    print("[Agent] 完成，动作历史：", flush=True)
    for i, h in enumerate(history):
        st = str(h)[:150]
        print(f"  {i}: {st}", flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"AGENT FAIL: {type(e).__name__}: {str(e)[:300]}", flush=True)
        sys.exit(1)
