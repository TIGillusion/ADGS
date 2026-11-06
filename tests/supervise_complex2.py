"""复杂工程测试 2：分布式任务队列与调度系统（故意留关键信息缺口）

验证目标：
1. CEO 识别方向性信息缺失并提问（上次已验证，本次复验）
2. 验收外包：CEO 最后把验收派发给节点，不再亲自 read_file（上次的 bug）
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.config import config
from utils.chat import set_debug_level
from core.ceo import CEO

WS = config.set_workspace(str(Path(__file__).resolve().parent.parent / "data" / "complex2_ws"))
set_debug_level(1)

# 故意留方向性缺口：队列存储 / 执行模型 / 重试策略 / 任务类型
TASK = f"""请构建一个任务队列与调度系统（工作区：{WS}）。

【总体目标】
实现一个任务队列系统，支持任务提交、排队、调度执行、结果记录，以及失败重试。

【功能要求】
1. 任务提交：生产者能提交任务到队列
2. 队列管理：任务排队、优先级
3. 调度执行：消费者从队列取任务并执行
4. 失败处理：任务失败时能重试
5. 结果与统计：记录执行结果、统计任务成功率/耗时

【交付物】
- 队列核心模块
- 调度/执行模块
- 配置文件
- 示例（生产者 + 消费者示例）
- README.md（完整使用说明）

【验收标准】
- 任务能提交、排队、被消费执行
- 失败任务能按策略重试
- 能输出统计（成功率、耗时等）
- README 说明清晰，能按文档跑起来

【执行要求】
- 分步推进，自主拆解
- 先理解需求；如有影响架构方向的关键信息缺失，先向我提问确认，不要盲目假设"""

USER_ANSWERS = [
    """补充关键信息：
1. 队列存储：SQLite 持久化（标准库 sqlite3），任务状态持久化到数据库。
2. 执行模型：线程池并发消费（worker 数量可配置）。
3. 重试策略：可配置最大重试次数 + 指数退避（如 1s/2s/4s）。
4. 任务类型：Python 函数任务（注册表注册 任务名→函数）+ 命令行任务两种。
5. 优先级：整数越小越优先（可选字段，缺省 0）。
6. 技术栈：标准库为主（sqlite3/threading/queue）。
其余细节自行决策，开始实施。""",
    "按补充信息继续推进，自主拆解执行，完成后把验收也派发给节点做，最后 finish 汇报。",
]


def main():
    print(f"工作区: {WS}", flush=True)
    from core.registry import registry
    print(f"活动节点: {registry.count()}", flush=True)

    ceo = CEO()
    t0 = time.time()
    msg = TASK
    last_reply = ""
    ask_count = 0
    ask_questions = []
    turn = 0
    for turn in range(20):
        print(f"\n{'='*60}\n[轮次 {turn+1}] 提交（{msg[:50]}...）", flush=True)
        try:
            reply = ceo.talk(msg)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[监工] CEO 异常: {type(e).__name__}: {e}", flush=True)
            break
        reply = str(reply)
        print(f"[CEO 返回]（{time.time()-t0:.0f}s）\n{reply[:500]}", flush=True)
        last_reply = reply

        if "CEO 提问" in reply:
            ask_count += 1
            ask_questions.append(reply[:200])
            msg = USER_ANSWERS[min(ask_count - 1, len(USER_ANSWERS) - 1)]
            continue
        if "任务完成汇报" in reply or "决策上限" in reply or "CEO 错误" in reply:
            break
        msg = "继续推进直到完成，完成后把验收派发给节点做，最后 finish 汇报。"

    files = sorted(p.name for p in Path(WS).rglob("*") if p.is_file()) if Path(WS).exists() else []
    print(f"\n[监工] 提问 {ask_count} 次，总耗时 {time.time()-t0:.0f}s，{turn+1} 轮", flush=True)
    for i, q in enumerate(ask_questions):
        print(f"  [提问{i+1}] {q[:180]}", flush=True)
    print(f"\n[监工] 工作区文件: {files}", flush=True)
    print("\n=== 最后 CEO 回复 ===", flush=True)
    print(last_reply[:1500], flush=True)
    print("=== END ===", flush=True)


if __name__ == "__main__":
    main()
