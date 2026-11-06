"""CEO 循环机制实测：走 CEO.talk() 跑真实简单任务，观察决策循环行为

观察点：
- CEO 是否正常决策（split/single → 派发节点 → report/finish）
- 是否过早汇报 / 纯文本退出（应被新机制拦截）
- 疑似回复循环检测 + 提示是否触发
- 上下文压缩是否触发
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.config import config
from utils.chat import set_debug_level
from core.ceo import CEO

WS = config.set_workspace(str(Path(__file__).resolve().parent.parent / "data" / "live_train"))
set_debug_level(2)  # 打印每个 LLM 的工具调用（能看到 CEO 每步决策）

TASKS = [
    "写一个 fibonacci.py：实现 fib(n) 返回斐波那契数列第 n 项（fib(0)=0, fib(1)=1），"
    "并写 test_fibonacci.py 用 pytest 验证前 10 项 [0,1,1,2,3,5,8,13,21,34]。"
    "产物写到工作区，运行 pytest 确认全部通过。",

    "写一个 json_config.py：Config 类，构造时读 JSON 文件，支持 get('a.b.c', default) "
    "点号嵌套访问，值自动类型转换（'123'→int、'true'→bool），缺失键返回默认值。"
    "写 demo_config.py 演示。产物写到工作区。",

    "写一个 todo.py：命令行待办事项工具，支持 add <内容> / list / done <序号> 三个子命令，"
    "数据持久化到 todos.json。写使用说明。产物写到工作区。",
]


def run_one(idx, task):
    ceo = CEO()
    print(f"\n{'='*64}\n[任务 {idx+1}/{len(TASKS)}] 提交", flush=True)
    t0 = time.time()
    msg = task
    final = ""
    turn = 0
    for turn in range(8):
        try:
            reply = ceo.talk(msg)
        except Exception as e:
            print(f"  !! CEO 异常: {type(e).__name__}: {e}", flush=True)
            break
        reply = str(reply)
        print(f"  [turn {turn}] 返回 {time.time()-t0:.0f}s: {reply[:120].strip()!r}", flush=True)
        final = reply
        if "CEO 提问" in reply:
            msg = "（系统代答）请自行判断，按任务书执行即可，信息已足够，不要频繁提问。"
            continue
        if "CEO 错误" in reply or "决策上限" in reply:
            break
        if "任务完成汇报" in reply:
            break
        break  # 其他返回视为本轮结束

    files = [f.name for f in Path(WS).glob("*.py")] if Path(WS).exists() else []
    print(f"  [产物] 工作区 .py: {files}", flush=True)
    return {
        "task": idx + 1,
        "turns": turn + 1,
        "loop_detected": ceo._loop_detected,
        "compress_count": ceo._compress_count,
        "dispatch": len(ceo.workflow_log),
        "final": final[:160],
        "files": files,
    }


if __name__ == "__main__":
    print(f"工作区: {WS}", flush=True)
    from core.registry import registry
    print(f"活动节点: {registry.count()} | 存档: {len(registry.archived())}", flush=True)

    results = []
    for i, t in enumerate(TASKS):
        results.append(run_one(i, t))

    print("\n" + "=" * 64)
    print("【实测结果汇总】")
    for r in results:
        print(f"  任务{r['task']}: 交互{r['turns']}轮 | 派发{r['dispatch']}次 | "
              f"循环检测={r['loop_detected']} | 压缩={r['compress_count']}次", flush=True)
        print(f"    产物: {r['files']}", flush=True)
        print(f"    结尾: {r['final']}", flush=True)
    print("=" * 64)
