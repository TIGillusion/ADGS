"""第 2 轮多任务训练：日志分析 / 数据可视化 / 文件批处理（走 CEO.talk()）

训练目标：扩充节点技能面（数据处理、matplotlib 可视化、CLI 批处理工具），
并验证新提示词是否减少 CEO 过度 ask_user。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.config import config
from utils.chat import set_debug_level
from core.ceo import CEO

WS = config.set_workspace(str(Path(__file__).resolve().parent.parent / "data" / "train_round2_ws"))
set_debug_level(1)

TASKS = [
    ("日志分析", "写一个 log_analyzer.py：读取工作区的 access.log（Apache/Nginx 格式），统计："
     "①总请求数 ②各状态码(2xx/3xx/4xx/5xx)分布 ③访问量 Top10 的 IP ④访问量 Top10 的 URL，"
     "结果输出到 analysis_report.md。先用脚本生成一个含 100 条随机记录的示例 access.log，再分析它。"
     "产物：log_analyzer.py、access.log、analysis_report.md。用标准库实现，不要引入外部依赖。"),

    ("数据可视化", "写一个 plot_sales.py：读取 sales.csv（含 月份,销售额,利润 三列，12 个月数据），"
     "用 matplotlib 生成两张图：①每月销售额柱状图 ②销售额+利润双折线图，"
     "保存为 sales_bar.png 和 sales_line.png。先写脚本生成示例 sales.csv 再绘图。"
     "产物：plot_sales.py、sales.csv、sales_bar.png、sales_line.png。matplotlib 如未安装请先安装。"),

    ("文件批处理", "写一个 file_organizer.py：命令行工具，把指定目录下的文件按扩展名分类移动到子目录"
     "（images/、docs/、code/、other/），支持 --dry-run（只打印移动计划不实际移动）和 --target 指定目标目录。"
     "先造一批测试文件验证分类正确。产物：file_organizer.py、README.md（说明用法）。用标准库实现。"),
]

AUTO_ANSWER = "任务书已足够详细，按任务书直接执行，不要提问确认细节，自主拆解推进直到完成。"


def run_one(idx, name, task):
    ceo = CEO()
    print(f"\n{'='*64}\n[任务 {idx+1}/{len(TASKS)}] {name}", flush=True)
    t0 = time.time()
    msg = task
    ask_count = 0
    final = ""
    turn = 0
    for turn in range(8):
        try:
            reply = ceo.talk(msg)
        except Exception as e:
            print(f"  !! CEO 异常: {type(e).__name__}: {e}", flush=True)
            break
        reply = str(reply)
        print(f"  [turn {turn}] {time.time()-t0:.0f}s: {reply[:100].strip()!r}", flush=True)
        final = reply
        if "CEO 提问" in reply:
            ask_count += 1
            msg = AUTO_ANSWER
            continue
        if "任务完成汇报" in reply or "决策上限" in reply or "CEO 错误" in reply:
            break
        break

    files = sorted(p.name for p in Path(WS).glob("*")) if Path(WS).exists() else []
    return {"name": name, "ask_count": ask_count, "turns": turn + 1,
            "loop": ceo._loop_active, "compress": ceo._compress_count,
            "dispatch": len(ceo.workflow_log), "final": final[:120],
            "files": files}


if __name__ == "__main__":
    print(f"工作区: {WS}", flush=True)
    from core.registry import registry
    print(f"活动节点: {registry.count()}", flush=True)

    results = []
    for i, (name, task) in enumerate(TASKS):
        results.append(run_one(i, name, task))

    print("\n" + "=" * 64)
    print("【第 2 轮训练结果】")
    for r in results:
        print(f"  {r['name']}: 提问{r['ask_count']}次 交互{r['turns']}轮 派发{r['dispatch']}次 "
              f"循环={r['loop']} 压缩={r['compress']}次", flush=True)
        print(f"    产物: {r['files']}", flush=True)
        print(f"    结尾: {r['final']}", flush=True)
    print("=" * 64)
