"""ADGS v3 第二轮训练 — 8 个新领域任务串行 + 穿插进化

新领域：SQLite 数据库 / 并发下载 / 日志分析 / 嵌套JSON / CSV合并 / 图片处理 / 排序算法 / 日期工具
work() 已自动沉淀描述分化；路由会利用第一轮训练出的专长
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
    # 1: SQLite 数据库
    mk("T1-SQLite", "SQLite 数据库操作", "用 sqlite3 读取 seed.json 数据建 users 表，写入30条记录，查询：年龄>30的用户数、按城市分组统计人数，输出 db_stats.json",
       ["sqlite3 建表 users(id,name,age,city)", "从 seed.json 导入数据", "查询年龄>30 用户数", "按城市分组统计", "输出 db_stats.json"],
       ["users.db 存在且有30条记录", "db_stats.json 含年龄>30用户数和城市统计"],
       ["build_db.py", "users.db", "db_stats.json", "report.md"],
       f"只允许修改 {BASE/'t2_1'} 目录", str(BASE / "t2_1" / "report.md")),
    # 2: 并发下载
    mk("T2-并发下载", "并发下载", "写 downloader.py：用 ThreadPoolExecutor 并发下载 https://example.com 和 https://example.org 的首页 HTML 保存为 page1.html / page2.html",
       ["ThreadPoolExecutor 并发", "requests 下载两个页面", "保存为 page1.html/page2.html"],
       ["page1.html 和 page2.html 存在且非空", "代码使用 ThreadPoolExecutor"],
       ["downloader.py", "page1.html", "page2.html", "report.md"],
       f"只允许修改 {BASE/'t2_2'} 目录", str(BASE / "t2_2" / "report.md")),
    # 3: 日志分析
    mk("T3-日志分析", "分析访问日志", "解析 access.log：统计总请求数、各状态码数量、访问量 TOP5 路径、独立 IP 数，输出 log_stats.json",
       ["解析 Apache 格式日志", "统计状态码分布", "TOP5 路径", "独立 IP 数", "输出 log_stats.json"],
       ["log_stats.json 含状态码分布和TOP5路径", "统计数字与日志内容一致"],
       ["parse_log.py", "log_stats.json", "report.md"],
       f"只允许修改 {BASE/'t2_3'} 目录", str(BASE / "t2_3" / "report.md")),
    # 4: 嵌套 JSON 处理
    mk("T4-嵌套JSON", "处理嵌套订单数据", "分析 orders.json：计算订单总额=所有items价格×数量之和，统计各状态订单数，找出最贵订单，输出 order_stats.json",
       ["解析嵌套 items 结构", "计算每单总额", "统计 status 分布", "找出最贵订单", "输出 order_stats.json"],
       ["order_stats.json 含总订单数、总额、状态分布、最贵订单"],
       ["analyze_orders.py", "order_stats.json", "report.md"],
       f"只允许修改 {BASE/'t2_4'} 目录", str(BASE / "t2_4" / "report.md")),
    # 5: CSV 合并
    mk("T5-CSV合并", "合并多个CSV", "把 part_1.csv part_2.csv part_3.csv 合并为 all_students.csv（去重表头），并统计平均分，输出到 merge_report.txt",
       ["合并3个CSV", "只保留一个表头行", "计算平均分", "输出 merge_report.txt"],
       ["all_students.csv 有30行数据", "merge_report.txt 含平均分"],
       ["merge_csv.py", "all_students.csv", "merge_report.txt", "report.md"],
       f"只允许修改 {BASE/'t2_5'} 目录", str(BASE / "t2_5" / "report.md")),
    # 6: 图片处理
    mk("T6-图片处理", "图片缩略图", "用 Pillow 把 source.png 缩放为 100x100 缩略图 thumb.png，并保存为 JPEG 格式 thumb.jpg",
       ["Pillow 打开 source.png", "缩放到 100x100", "保存 thumb.png 和 thumb.jpg"],
       ["thumb.png 和 thumb.jpg 存在且尺寸为 100x100"],
       ["make_thumb.py", "thumb.png", "thumb.jpg", "report.md"],
       f"只允许修改 {BASE/'t2_6'} 目录", str(BASE / "t2_6" / "report.md")),
    # 7: 排序算法
    mk("T7-排序算法", "实现排序算法", "实现 quicksort.py：快速排序函数 + 测试：对随机1000个整数排序，验证与 sorted() 结果一致，输出排序前5和后5个元素",
       ["实现快速排序", "随机生成1000个整数", "与 sorted() 结果比对", "输出前5和后5元素"],
       ["程序输出与 sorted() 一致", "前5后5元素打印正确"],
       ["quicksort.py", "report.md"],
       f"只允许修改 {BASE/'t2_7'} 目录", str(BASE / "t2_7" / "report.md")),
    # 8: 日期工具
    mk("T8-日期工具", "日期范围工具", "写 dateutils.py：给定 2026-08-01 到 2026-08-31，统计工作日天数、周末天数，并列出所有周六周日的日期，输出到 date_stats.txt",
       ["处理日期范围", "计算工作日/周末", "列出所有周末日期", "输出 date_stats.txt"],
       ["date_stats.txt 含工作日天数", "周末日期列表完整（8月共9个周末日）"],
       ["dateutils.py", "date_stats.txt", "report.md"],
       f"只允许修改 {BASE/'t2_8'} 目录", str(BASE / "t2_8" / "report.md")),
]


def run_task(idx, task_md):
    print(f"\n{'='*60}\n[任务 {idx+1}/8] 路由中...", flush=True)
    node = route(task_md, reg=registry)
    if not node:
        print("  !! 无可用节点", flush=True)
        return
    print(f"  → 选中 {node.id} (score={node.score}, sr={node.success_rate}, 技能={list(HermesInstance.list_skills(node.id).keys())})", flush=True)
    t0 = time.time()
    try:
        r = NODE(node.id).work(task_md, upper="ceo")
    except Exception as e:
        print(f"  !! 执行异常: {e}", flush=True)
        return
    dt = time.time() - t0
    print(f"  耗时 {dt:.0f}s | success={r.get('success')} | "
          f"eval={r.get('evaluation', {}).get('score')}", flush=True)
    print(f"  描述→ {registry.get(node.id).describe[:60]}", flush=True)
    REPORT.append({"task": idx + 1, "node": node.id, "success": r.get("success"),
                   "score": r.get("evaluation", {}).get("score"), "time": round(dt)})


def main():
    print("========== 第二轮训练开始 ==========", flush=True)
    for idx, task_md in enumerate(TASKS):
        if idx > 0 and idx % 2 == 0:
            print(f"\n>>> 触发进化（第 {idx//2} 轮）...", flush=True)
            rep = evolve()
            print(f"    种群 {rep['n_before']} → {rep['n_after']} | "
                  f"繁衍 {len(rep['births'])} 淘汰 {len(rep['eliminations'])}", flush=True)
        run_task(idx, task_md)

    # 最终报告
    print("\n" + "=" * 60, flush=True)
    print("【第二轮训练结果】", flush=True)
    for r in REPORT:
        print(f"  任务{r['task']}: {r['node']} success={r['success']} "
              f"score={r['score']} 耗时{r['time']}s", flush=True)
    print("\n【节点状态】", flush=True)
    for n in sorted(registry.active(), key=lambda x: x.score, reverse=True):
        sk = HermesInstance.list_skills(n.id)
        print(f"  {n.id:8s} score={n.score:6.1f} sr={n.success_rate:.3f} "
              f"tasks={n.tasks_done} gen={n.gen} 技能={list(sk.keys())}", flush=True)
    print(f"\n【种群】活动: {registry.count()} | 存档: {len(registry.archived())}", flush=True)
    Path("data/train2_report.json").write_text(
        json.dumps({"results": REPORT}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("========== 第二轮训练结束 ==========", flush=True)


if __name__ == "__main__":
    main()
