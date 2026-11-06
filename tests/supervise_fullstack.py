"""监工模式：全栈 Web 图书管理应用训练（走 CEO.talk()）

训练目标：节点全栈开发能力（Flask 后端 + SQLite + 原生前端 + 前后端联调）。
监工职责：CEO 提问时代答、不干预系统，最后交完整表现报告。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.config import config
from utils.chat import set_debug_level
from core.ceo import CEO

WS = config.set_workspace(str(Path(__file__).resolve().parent.parent / "data" / "fullstack_ws"))
set_debug_level(2)

TASK = f"""请开发一个全栈 Web 图书管理应用，训练你的全栈开发能力（工作区：{WS}）。

【技术栈】
- 后端：Python Flask + 标准库 sqlite3（不用 ORM，不引入额外依赖）
- 前端：原生 HTML + CSS + JavaScript（不用框架，单页应用）
- 后端监听 5000 端口

【协议契约 —— 必须严格遵守，拆分任务时把契约写进每一个涉及前后端的子任务，避免前后端接口不一致】
- GET /api/books → JSON 数组 [{{id, title, author, year, status, rating}}]
- POST /api/books → body {{title, author, year, status, rating}}，返回创建的完整对象
- PUT /api/books/<id> → 更新，返回更新后的对象
- DELETE /api/books/<id> → 删除，返回 {{"success": true}}
- GET /api/books?search=关键词 → 按 title/author 模糊搜索
- 字段类型：id=int自增、title=str、author=str、year=int、status=str(reading/finished/want)、rating=float(0-5)
- 前端 index.html 用 fetch 调用上述 API

【功能需求】
1. 后端 REST API（6 个端点），数据持久化到 books.db
2. 前端：图书列表展示、添加表单、搜索框、删除按钮、状态/评分编辑
3. 集成联调：真实操作走通 添加→搜索→编辑→删除 全流程

【验收标准】
1. 用 curl 逐一测试 6 个端点，CRUD 全部正确返回
2. 启动后端后，前端能完成 添加/搜索/删除 真实操作
3. 产物：app.py、templates/index.html、books.db、README.md

【执行要求】
- 分步推进：先后端 API → 再前端 → 最后集成验证
- 自主推进，不要频繁提问；协议已写清，按契约执行即可"""

USER_ANSWERS = [
    "协议契约已在任务书里写清楚：后端 5000 端口，API 路径 /api/books（GET/POST/PUT/DELETE），"
    "JSON 字段 {id,title,author,year,status,rating}。按契约执行，拆分时把契约写进每个子任务。"
    "技术栈 Flask+sqlite3+原生HTML/JS，不要引入额外依赖。直接开始，自主推进。",
    "按你的拆解继续。先后端 API（curl 验证 CRUD），再前端页面，最后集成联调。"
    "遇到问题自行解决，不要频繁提问。",
    "继续推进，完成后做集成验证（真实 HTTP 请求走通 添加→搜索→编辑→删除 全流程），"
    "然后 finish 汇报。",
]


def main():
    print(f"工作区: {WS}", flush=True)
    from core.registry import registry
    print(f"活动节点: {registry.count()} | 存档: {len(registry.archived())}", flush=True)

    ceo = CEO()
    t0 = time.time()
    msg = TASK
    last_reply = ""
    turn = 0
    for turn in range(20):
        print(f"\n{'='*60}\n[轮次 {turn+1}] 提交消息（{msg[:60]}...）", flush=True)
        try:
            reply = ceo.talk(msg)
        except KeyboardInterrupt:
            print("\n[监工] 被中断", flush=True)
            break
        except Exception as e:
            print(f"\n[监工] CEO 异常: {type(e).__name__}: {e}", flush=True)
            break
        reply = str(reply)
        print(f"[CEO 返回]（累计 {time.time()-t0:.0f}s）\n{reply[:400]}", flush=True)
        last_reply = reply

        if "CEO 提问" in reply:
            msg = USER_ANSWERS[min(turn, len(USER_ANSWERS) - 1)]
            continue
        if "任务完成汇报" in reply or "决策上限" in reply or "CEO 错误" in reply:
            break
        # 其他返回（进度/意外）：继续推动
        msg = "继续推进直到完成，完成后调用 finish 汇报。"

    # 工作区产物观察
    files = sorted(p.name for p in Path(WS).glob("*")) if Path(WS).exists() else []
    print(f"\n[监工] 工作区产物: {files}", flush=True)

    print(f"\n[监工] 观察结束，总耗时 {time.time()-t0:.0f}s，共 {turn+1} 轮", flush=True)
    print("\n=== 最后 CEO 回复 ===", flush=True)
    print(last_reply[:1200], flush=True)
    print("=== END ===", flush=True)


if __name__ == "__main__":
    main()
