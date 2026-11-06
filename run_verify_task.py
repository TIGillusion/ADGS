"""ADGS 完整链路真实验证：evolve → CEO 拆解派发 → 节点执行 → 验收汇报 → 状态对比

验证点：
  【1】力量一：evolve 报告 pressure 数值 + 淘汰/繁衍
  【2】返工闭环：验收路径走通（evaluate 正常），若判 False 观察返工日志
  【3】死代码清理：启动加载新 settings.yaml 不报错
  【4】record_score：多节点并行/克隆场景下 tasks_done/score 更新是否合理（无双计）
"""
import json
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from config.config import config
from core.registry import registry
from core.evolution import evolve
from core.ceo import CEO


def snapshot():
    return {nid: {"tasks_done": n.tasks_done, "score": n.score,
                  "success_rate": n.success_rate,
                  "describe": (n.describe or "")[:40]}
            for nid, n in registry._nodes.items()}


def main():
    ws = config.set_workspace(str(ROOT / "data" / "verify_ws4"))
    print(f"[verify] 工作区: {ws}", flush=True)
    before = snapshot()
    n_before = registry.count()
    print(f"[verify] 跑前活动节点: {n_before}", flush=True)

    # ---- 启动进化（main.py 同款逻辑）----
    t0 = time.time()
    report = evolve()
    print(f"[verify] evolve({time.time()-t0:.0f}s): "
          f"繁衍 {len(report['births'])} 淘汰 {len(report['eliminations'])} "
          f"种群 {report['n_before']}→{report['n_after']} "
          f"基础压力={report['pressure']} 淘汰目标={report['elimination_target']}",
          flush=True)
    for b in report["births"]:
        print(f"  [出生] {b['id']} type={b['type']} parents={b['parents']} "
              f"skills={b['skills']}", flush=True)

    # ---- CEO 完整任务 ----
    # 3 个互相独立的模块任务：诱导 CEO 一轮拆多个并行子任务，
    # 触发"同节点多任务 → 临时克隆"保护（上一轮串行依赖任务未触发）
    task = """在 data/verify_ws4 工作区内实现 3 个互相独立的纯标准库 Python 工具模块（禁止任何第三方依赖）：

1. string_utils.py：实现三个纯函数
   - snake_case(s)：转蛇形命名
   - camel_to_snake(s)：驼峰转蛇形
   - truncate(s, n)：超长截断（n 为最大长度，保留省略号标记）
2. math_utils.py：实现三个纯函数
   - is_prime(n)：素数判断（含 0/1/负数边界）
   - gcd(a, b)：最大公约数
   - lcm(a, b)：最小公倍数
3. date_utils.py：实现三个纯函数
   - is_leap_year(y)：闰年判断（含世纪年边界）
   - days_between(d1, d2)：两个 ISO 日期字符串之间的天数差
   - weekday_name(y, m, d)：返回星期几中文名

每个模块配套一个 pytest 测试文件（test_string_utils.py / test_math_utils.py / test_date_utils.py），每文件至少 5 个边界用例。
实际运行 pytest 并确保全部通过，输出保存到 test_output.txt。
写 README.md：每个函数用法说明 + 至少 2 个示例。

验收标准：pytest 全部通过（test_output.txt 有记录）、6 个交付文件齐全、README 含示例。
完成后的执行报告写入 report_verify.md。"""

    print(f"\n[verify] 发送任务给 CEO ...", flush=True)
    ceo = CEO()
    t0 = time.time()
    reply = ceo.talk(task)
    dt = time.time() - t0
    print(f"\n[verify] CEO 回复（耗时 {dt:.0f}s）:", flush=True)
    print(reply, flush=True)

    # ---- 节点流转记录 ----
    print(f"\n[verify] 节点流转记录（{len(ceo.workflow_log)} 条）:", flush=True)
    for w in ceo.workflow_log:
        print(f"  {w}", flush=True)

    # ---- 状态对比 ----
    after = snapshot()
    print(f"\n[verify] 跑后活动节点: {registry.count()}", flush=True)
    print("[verify] 节点状态变化（tasks_done 增加者）:", flush=True)
    changed = 0
    for nid, n in registry._nodes.items():
        b = before.get(nid)
        if b and n.tasks_done > b["tasks_done"]:
            changed += 1
            print(f"  {nid}: tasks_done {b['tasks_done']}→{n.tasks_done} | "
                  f"score {b['score']}→{n.score} | sr {b['success_rate']}→{n.success_rate}",
                  flush=True)
            if n.describe != b["describe"]:
                print(f"     描述分化: {b['describe'][:26]}... → {n.describe[:26]}...",
                      flush=True)
    if not changed:
        print("  （无节点状态变化）", flush=True)
    print("\n[verify] 验证结束", flush=True)


if __name__ == "__main__":
    main()
