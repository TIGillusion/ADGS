"""ADGS v3 第三轮训练 — 8 个复杂工程任务（触发 SKILLS_GUIDANCE）

任务类型：调试排错 / 多文件工程 / 设计权衡 / 可复用框架
目标：让 hermes 在完成任务时自然创建 skill（非平凡工作流）
"""
import sys
import json
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.config import config
from config.prompts import render_task_md
from core.registry import registry
from core.evolution import evolve
from core.router import route
from core.node import NODE
from core.hermes import HermesInstance

BASE = config.PROJECT_ROOT / "data" / "tasks"
REPORT = []


def mk(name, overall, goal, reqs, acc, dels, perm, rp):
    return render_task_md(overall, goal, reqs, acc, dels, perm, report_path=rp)


TASKS = [
    # 1: 调试排错（3 个故意 bug）
    mk("T1-调试订单模块", "修复有 bug 的订单折扣模块", "order_discount.py 有 3 个 bug（索引越界/排序方向/键缺失），修复并用 orders.json 验证",
       ["读懂 order_discount.py 三个函数", "定位并修复 3 个 bug", "编写 pytest 验证修复", "所有测试通过"],
       ["修复后运行无异常", "pytest 全部通过", "Top3 按消费降序", "VIP3 不崩溃"],
       ["order_discount.py", "test_order_discount.py", "report.md"],
       f"只允许修改 {BASE/'t3_1'} 目录", str(BASE / "t3_1" / "report.md")),
    # 2: LRU 缓存库（设计权衡）
    mk("T2-LRU缓存库", "实现线程安全的 LRU 缓存库", "实现 lru_cache.py：线程安全 LRU（容量可配、get/set、过期时间可选），写 pytest 覆盖",
       ["容量限制淘汰最久未用", "threading.Lock 线程安全", "可选 TTL 过期", "pytest 至少 8 用例"],
       ["pytest 全部通过", "并发读写无数据竞争", "TTL 过期后 get 返回 None"],
       ["lru_cache.py", "test_lru_cache.py", "report.md"],
       f"只允许修改 {BASE/'t3_2'} 目录", str(BASE / "t3_2" / "report.md")),
    # 3: 插件化框架（架构模式）
    mk("T3-插件化框架", "实现插件化文本处理器框架", "实现 plugin_runner.py：扫描 plugins/ 目录加载 .py 插件（每个插件有 process(text) 接口），内置 2 个示例插件（大写/去空格），命令行指定插件链处理文本",
       ["importlib 动态加载插件", "插件接口约定 process(text)->text", "2 个示例插件", "命令行 -p plugin1,plugin2 串联"],
       ["运行示例插件链输出正确", "新增插件无需改主程序（架构验证）"],
       ["plugin_runner.py", "plugins/upper.py", "plugins/strip.py", "report.md"],
       f"只允许修改 {BASE/'t3_3'} 目录", str(BASE / "t3_3" / "report.md")),
    # 4: 任务队列（并发设计）
    mk("T4-任务队列", "实现带重试和死信的任务队列", "实现 task_queue.py：生产者-消费者队列，任务失败自动重试（最多3次），重试耗尽进死信队列，支持多消费者线程，写演示",
       ["Queue + 多消费者线程", "失败重试 3 次指数退避", "死信队列单独存放", "演示 10 个任务部分失败场景"],
       ["失败任务重试后成功或进死信", "演示输出完整", "无任务丢失"],
       ["task_queue.py", "demo_queue.py", "report.md"],
       f"只允许修改 {BASE/'t3_4'} 目录", str(BASE / "t3_4" / "report.md")),
    # 5: 数据管道（可组合设计）
    mk("T5-数据管道", "实现可组合的数据处理管道", "实现 pipeline.py：Pipe 类支持 .map(func).filter(pred).reduce(init,fn) 链式调用，惰性求值（迭代器），写演示处理一个整数列表",
       ["链式 API map/filter/reduce", "惰性迭代器实现", "管道可复用（同一管道处理多组数据）", "演示用例"],
       ["链式调用输出正确", "惰性求值验证（不消费不会执行）", "同一管道两次复用结果一致"],
       ["pipeline.py", "demo_pipeline.py", "report.md"],
       f"只允许修改 {BASE/'t3_5'} 目录", str(BASE / "t3_5" / "report.md")),
    # 6: 状态机（设计模式）
    mk("T6-状态机", "实现订单状态机", "实现 state_machine.py：通用状态机（状态/事件/转移表驱动），演示订单流转 pending→paid→shipped→delivered，非法转移抛异常",
       ["状态转移表驱动（字典）", "非法转移抛 ValueError", "支持 on_enter 回调", "订单示例"],
       ["合法流转正常", "非法流转（pending→delivered）抛异常", "回调触发"],
       ["state_machine.py", "demo_order_sm.py", "report.md"],
       f"只允许修改 {BASE/'t3_6'} 目录", str(BASE / "t3_6" / "report.md")),
    # 7: 日志分析+报告（多模块复合）
    mk("T7-日志分析报告", "多模块日志分析工具", "写 loganalyzer/ 包（__init__.py + parser.py + report.py）：解析 access.log（复用 t2_3 格式），生成 HTML 报告（状态码分布+TOP10路径+时间趋势）",
       ["包结构 loganalyzer/ 三个模块", "解析 Apache 日志", "生成 report.html", "main.py 命令行入口"],
       ["report.html 存在且包含图表数据", "模块可独立 import", "main.py 一键生成"],
       ["loganalyzer/__init__.py", "loganalyzer/parser.py", "loganalyzer/report.py", "main.py", "report.html", "report.md"],
       f"只允许修改 {BASE/'t3_7'} 目录（可读取 {BASE/'t3_7'} 下的 access.log）", str(BASE / "t3_7" / "report.md")),
    # 8: 配置热重载（工程模式）
    mk("T8-配置热重载", "实现配置文件热重载", "实现 config_watcher.py：监控 config.json 的 mtime，变化时自动重载并触发回调；主程序模拟长循环，期间手动改配置生效",
       ["mtime 轮询检测", "自动重载 + 回调通知", "演示：长循环中改配置立即生效", "多配置节更新"],
       ["改配置后回调触发且新值生效", "演示程序打印新旧值"],
       ["config_watcher.py", "demo_watcher.py", "config.json", "report.md"],
       f"只允许修改 {BASE/'t3_8'} 目录", str(BASE / "t3_8" / "report.md")),
]


def run_task(idx, task_md):
    print(f"\n{'='*60}\n[任务 {idx+1}/8] 路由中...", flush=True)
    node = route(task_md, reg=registry)
    if not node:
        print("  !! 无可用节点", flush=True)
        return
    sk_before = set(HermesInstance.list_skills(node.id).keys())
    print(f"  → 选中 {node.id} (score={node.score}, 技能={list(sk_before)})", flush=True)
    t0 = time.time()
    try:
        r = NODE(node.id).work(task_md, upper="ceo")
    except Exception as e:
        print(f"  !! 执行异常: {e}", flush=True)
        return
    dt = time.time() - t0
    sk_after = set(HermesInstance.list_skills(node.id).keys())
    new_skills = sk_after - sk_before
    print(f"  耗时 {dt:.0f}s | success={r.get('success')} | "
          f"eval={r.get('evaluation', {}).get('score')} | "
          f"新建技能={list(new_skills) if new_skills else '无'}", flush=True)
    REPORT.append({"task": idx + 1, "node": node.id, "success": r.get("success"),
                   "score": r.get("evaluation", {}).get("score"), "time": round(dt),
                   "new_skills": list(new_skills)})


def main():
    print("========== 第三轮训练（复杂任务）开始 ==========", flush=True)
    for idx, task_md in enumerate(TASKS):
        if idx > 0 and idx % 2 == 0:
            print(f"\n>>> 触发进化（第 {idx//2} 轮）...", flush=True)
            rep = evolve()
            print(f"    种群 {rep['n_before']} → {rep['n_after']} | "
                  f"繁衍 {len(rep['births'])} 淘汰 {len(rep['eliminations'])}", flush=True)
        run_task(idx, task_md)

    print("\n" + "=" * 60, flush=True)
    print("【第三轮训练结果】", flush=True)
    for r in REPORT:
        print(f"  任务{r['task']}: {r['node']} success={r['success']} "
              f"score={r['score']} 耗时{r['time']}s 新技能={r['new_skills']}", flush=True)
    print("\n【技能基因库】", flush=True)
    gene = {}
    for n in registry.active():
        for k in HermesInstance.list_skills(n.id):
            gene.setdefault(k, []).append(n.id)
    for k, v in sorted(gene.items(), key=lambda x: -len(x[1])):
        print(f"  {k}: {len(v)} 节点 {v[:6]}{'...' if len(v)>6 else ''}", flush=True)
    print(f"\n【种群】活动: {registry.count()} | 存档: {len(registry.archived())}", flush=True)
    Path("data/train3_report.json").write_text(
        json.dumps({"results": REPORT}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("========== 第三轮训练结束 ==========", flush=True)


if __name__ == "__main__":
    main()
