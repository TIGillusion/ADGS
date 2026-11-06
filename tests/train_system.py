"""ADGS v3 训练脚本 — 8 个跨领域长线任务 + 穿插进化

每个任务：构造任务书 → 路由选节点 → NODE.work() 执行闭环
每 2 个任务后触发一次繁衍淘汰（模拟新对话开始）
最后输出系统状态报告（节点分数/成功率/技能库/描述分化）
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


def make_task(name: str, overall: str, goal: str, reqs: list, acc: list,
              deliverables: list, permissions: str, report_path: str) -> str:
    return render_task_md(overall, goal, reqs, acc, deliverables,
                          permissions, report_path=report_path)


# ================================================================
# 8 个训练任务定义
# ================================================================
TASKS = [
    # T1: 数据分析
    make_task(
        "T1-数据分析", "对销售数据做统计分析", "分析 sales.csv：按月汇总销售额，找出销量最高的商品，输出统计报告",
        ["读取 sales.csv", "按月聚合销售额", "统计每种商品的总销量", "输出 summary.csv"],
        ["summary.csv 存在且包含按月汇总", "找出销量最高商品并写入报告"],
        ["analyze_sales.py", "summary.csv", "report.md"],
        f"只允许修改 {BASE/'train_1'} 目录",
        str(BASE / "train_1" / "report.md")),
    # T2: 网页抓取
    make_task(
        "T2-网页抓取", "抓取网页内容", "用 requests + BeautifulSoup 抓取 https://example.com 的标题和所有链接，输出 markdown 报告",
        ["使用 requests 获取页面", "BeautifulSoup 解析标题和 a 标签链接", "输出 links.md"],
        ["links.md 存在且包含页面标题", "链接列表非空"],
        ["scrape.py", "links.md", "report.md"],
        f"只允许修改 {BASE/'train_2'} 目录",
        str(BASE / "train_2" / "report.md")),
    # T3: 文件整理
    make_task(
        "T3-文件整理", "批量整理散乱文件", "把 train_3 目录下散乱文件按扩展名分类移动到 images/docs/code/archives/others 子目录",
        ["识别 jpg→images, pdf/docx→docs, py→code, zip→archives, 其他→others", "移动文件到对应子目录", "打印每个分类移动数量"],
        ["4 个子目录被创建", "所有 12 个文件都被移动", "输出移动统计"],
        ["organize.py", "report.md"],
        f"只允许修改 {BASE/'train_3'} 目录",
        str(BASE / "train_3" / "report.md")),
    # T4: Web 服务
    make_task(
        "T4-Web服务", "用 Flask 写一个极简 API", "写 Flask 应用：GET /api/status 返回 JSON {status:ok, time:...}，GET /api/echo?msg=x 返回 {echo:x}",
        ["Flask 路由两个接口", "返回 JSON 格式", "脚本能 import 不报错（不要求常驻运行）"],
        ["app.py 可被 python -c 'import app' 导入", "两个路由函数存在"],
        ["app.py", "report.md"],
        f"只允许修改 {BASE/'train_4'} 目录",
        str(BASE / "train_4" / "report.md")),
    # T5: 正则提取
    make_task(
        "T5-正则提取", "从文本中提取联系信息", "从 contact.txt 提取所有邮箱、电话、URL，分别输出到 emails.txt / phones.txt / urls.txt",
        ["正则匹配邮箱", "正则匹配电话（含 138-1234-5678 和 +86 格式）", "正则匹配 URL"],
        ["emails.txt 含 4 个邮箱", "phones.txt 含 3 个电话", "urls.txt 含 2 个链接"],
        ["extract.py", "emails.txt", "phones.txt", "urls.txt", "report.md"],
        f"只允许修改 {BASE/'train_5'} 目录",
        str(BASE / "train_5" / "report.md")),
    # T6: 单元测试
    make_task(
        "T6-单元测试", "为计算器写单元测试", "实现 calculator.py（add/sub/mul/div + 除零抛 ValueError），并用 pytest 写 test_calculator.py 覆盖",
        ["calculator.py 四个函数", "div 除零抛 ValueError", "pytest 测试至少 6 个用例"],
        ["pytest 运行全部通过", "测试覆盖正常/边界/异常"],
        ["calculator.py", "test_calculator.py", "report.md"],
        f"只允许修改 {BASE/'train_6'} 目录",
        str(BASE / "train_6" / "report.md")),
    # T7: 数据可视化
    make_task(
        "T7-数据可视化", "生成图表", "用 matplotlib 读取 train_1/sales.csv 生成：按月销售额折线图 sales_trend.png + 商品销量柱状图 product_sales.png",
        ["读取 sales.csv", "折线图按月", "柱状图按商品"],
        ["两个 PNG 文件存在且非空", "图表标题和坐标轴标签齐全"],
        ["plot.py", "sales_trend.png", "product_sales.png", "report.md"],
        f"只允许修改 {BASE/'train_7'} 目录（可读取 {BASE/'train_1'}）",
        str(BASE / "train_7" / "report.md")),
    # T8: 命令行工具
    make_task(
        "T8-命令行工具", "写一个字数统计 CLI 工具", "写 wc.py：python wc.py <file> 输出行数/单词数/字符数，支持 -l -w -c 单独选项",
        ["argparse 解析", "统计行/词/字符", "支持 -l -w -c 选项"],
        ["python wc.py doc_0.txt 输出三列统计", "-l 只输出行数"],
        ["wc.py", "report.md"],
        f"只允许修改 {BASE/'train_8'} 目录",
        str(BASE / "train_8" / "report.md")),
]


def run_task(idx: int, task_md: str):
    print(f"\n{'='*60}\n[任务 {idx+1}/8] 路由中...", flush=True)
    node = route(task_md, reg=registry)
    if not node:
        print("  !! 无可用节点", flush=True)
        return
    print(f"  → 选中 {node.id} (score={node.score}, sr={node.success_rate})", flush=True)
    t0 = time.time()
    try:
        r = NODE(node.id).work(task_md, upper="ceo")
    except Exception as e:
        print(f"  !! 执行异常: {e}", flush=True)
        return
    dt = time.time() - t0
    print(f"  耗时 {dt:.0f}s | success={r.get('success')} | "
          f"eval={r.get('evaluation', {}).get('score')} | "
          f"detail={str(r.get('evaluation', {}).get('detail'))[:80]}", flush=True)
    REPORT.append({"task": idx + 1, "node": node.id, "success": r.get("success"),
                   "score": r.get("evaluation", {}).get("score"),
                   "time": round(dt)})
    # 任务后：描述分化（读 hermes 实际技能库）
    try:
        NODE(node.id).evolve_describe()
    except Exception as e:
        print(f"  描述更新失败: {e}", flush=True)


def main():
    print("========== ADGS 训练开始 ==========", flush=True)
    for idx, task_md in enumerate(TASKS):
        # 每 2 个任务后触发进化（任务1前不进化，保持初始池）
        if idx > 0 and idx % 2 == 0:
            print(f"\n>>> 触发进化（第 {idx//2} 轮）...", flush=True)
            rep = evolve()
            print(f"    种群 {rep['n_before']} → {rep['n_after']} | "
                  f"繁衍 {len(rep['births'])} 淘汰 {len(rep['eliminations'])}", flush=True)
        run_task(idx, task_md)

    # ============ 最终状态报告 ============
    print("\n" + "=" * 60, flush=True)
    print("【训练结果汇总】", flush=True)
    for r in REPORT:
        print(f"  任务{r['task']}: {r['node']} success={r['success']} "
              f"score={r['score']} 耗时{r['time']}s", flush=True)

    print("\n【节点状态】", flush=True)
    for n in sorted(registry.active(), key=lambda x: x.score, reverse=True):
        sk = HermesInstance.list_skills(n.id)
        skn = list(sk.keys())
        print(f"  {n.id:8s} score={n.score:6.1f} sr={n.success_rate:.3f} "
              f"tasks={n.tasks_done} gen={n.gen} 技能={skn}", flush=True)

    print("\n【种群】", flush=True)
    print(f"  活动节点: {registry.count()} | 存档: {len(registry.archived())}", flush=True)

    # 保存报告
    out = {"results": REPORT,
           "registry": str(registry.path)}
    Path("data/train_report.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n报告已保存: data/train_report.json", flush=True)
    print("========== 训练结束 ==========", flush=True)


if __name__ == "__main__":
    main()
