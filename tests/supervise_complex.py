"""复杂工程测试：实时日志监控告警系统（故意留关键信息缺口）

测试目标：验证 CEO 能否识别"方向性信息缺失"并正确 ask_user 提问，
（既不过度问细节，也不盲目开工），拿到补充信息后完成任务。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.config import config
from utils.chat import set_debug_level
from core.ceo import CEO

WS = config.set_workspace(str(Path(__file__).resolve().parent.parent / "data" / "complex_ws"))
set_debug_level(1)

# 故意留 3 个方向性缺口：日志来源 / 告警通道 / 异常规则定义
TASK = f"""请构建一个实时日志监控与告警系统（工作区：{WS}）。

【总体目标】
实时监控服务日志流，检测到异常时自动触发告警，并输出监控统计。

【功能要求】
1. 实时读取日志流，持续处理新产生的日志
2. 异常检测：基于可配置的规则识别异常
3. 告警触发：检测到异常时按配置的方式发出告警
4. 监控统计：统计异常数量、频率、类型分布等

【交付物】
- 监控核心模块（读日志 + 检测）
- 告警模块
- 规则配置文件
- README.md（完整使用说明）
- 示例日志数据（用于测试）

【验收标准】
- 系统能持续处理日志输入，不遗漏
- 检测到符合规则的异常时能成功触发告警
- 能生成监控统计报告
- README 说明清晰，能按文档跑起来

【执行要求】
- 分步推进，自主拆解
- 先理解需求；如有影响架构方向的关键信息缺失，先向我提问确认，不要盲目假设"""

# 代答：CEO 提问时补充关键信息（分轮）
USER_ANSWERS = [
    """补充关键信息：
1. 日志来源：监控本地日志文件（持续 tail 式读取新增内容），同时支持从 stdin 读取（管道输入）。
2. 告警通道：实现两种——①控制台打印 ②webhook（HTTP POST 到指定 URL）。
3. 异常规则（全部可配置）：①错误关键词匹配（ERROR/FATAL/CRITICAL 等，可在配置自定义）
   ②时间窗口内错误频率阈值（如 5 分钟内错误超过 N 条触发告警，N 可配置）③支持规则组合。
4. 技术栈：Python 标准库为主，webhook 可用 requests。
其余细节你自行决策，开始实施。""",
    "按补充信息继续推进，自主拆解执行，完成后 finish 汇报。",
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
        print(f"[CEO 返回]（{time.time()-t0:.0f}s）\n{reply[:600]}", flush=True)
        last_reply = reply

        if "CEO 提问" in reply:
            ask_count += 1
            ask_questions.append(reply[:200])
            msg = USER_ANSWERS[min(ask_count - 1, len(USER_ANSWERS) - 1)]
            continue
        if "任务完成汇报" in reply or "决策上限" in reply or "CEO 错误" in reply:
            break
        msg = "继续推进直到完成，完成后 finish 汇报。"

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
