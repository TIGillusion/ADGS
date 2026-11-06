"""ADGS v3 第五轮训练 — 8 个新领域复杂任务（LLM 描述机制已启用）

新领域：模板引擎 / Excel处理 / 设计模式 / WebSocket / 基准测试 / 静态文件服务器 / 迷你ORM / ML预处理
每任务完成后：节点智能体 LLM 生成/微调描述（≤500字），生态位随训练演化
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
    # 1: 模板引擎
    mk("T1-模板引擎", "实现模板渲染引擎", "实现 template_engine.py：解析 {{ var }} 语法，支持默认值、列表循环 {% for %}、条件 {% if %}，用 template.txt + context.json 验证",
       ["{{ var }} 变量替换", "{% for %} 循环", "{% if %} 条件", "默认值处理"],
       ["渲染结果正确替换变量", "循环和条件语法工作"],
       ["template_engine.py", "demo_render.py", "report.md"],
       f"只允许修改 {BASE/'t5_1'} 目录", str(BASE / "t5_1" / "report.md")),
    # 2: Excel 处理
    mk("T2-Excel处理", "Excel 生成与汇总", "用 openpyxl 生成 employees.xlsx：写入 20 条员工记录（姓名/部门/薪资/入职年份），并生成汇总 sheet（各部门平均薪资），最后打印总平均薪资",
       ["openpyxl 写入 xlsx", "20 条记录", "汇总 sheet 各部门均值", "打印总均值"],
       ["employees.xlsx 存在", "汇总 sheet 数据正确"],
       ["excel_gen.py", "employees.xlsx", "report.md"],
       f"只允许修改 {BASE/'t5_2'} 目录", str(BASE / "t5_2" / "report.md")),
    # 3: 设计模式库
    mk("T3-设计模式库", "设计模式实现", "实现 patterns.py：单例（线程安全）、工厂方法、观察者（订阅/发布）、策略模式，每个配简短示例，pytest 覆盖",
       ["单例线程安全", "工厂方法", "观察者 pub/sub", "策略模式", "pytest 覆盖"],
       ["pytest 全部通过", "单例唯一性验证", "观察者通知顺序正确"],
       ["patterns.py", "test_patterns.py", "report.md"],
       f"只允许修改 {BASE/'t5_3'} 目录", str(BASE / "t5_3" / "report.md")),
    # 4: WebSocket 聊天
    mk("T4-WebSocket", "简单 WebSocket 服务器", "用 websockets 库实现 chat_server.py：多客户端连接、广播消息、客户端连接/断开日志，写客户端测试脚本验证收发",
       ["websockets 库", "多客户端并发", "消息广播", "连接/断开日志"],
       ["服务器可启动", "客户端能收发消息", "广播到所有客户端"],
       ["chat_server.py", "chat_client.py", "report.md"],
       f"只允许修改 {BASE/'t5_4'} 目录", str(BASE / "t5_4" / "report.md")),
    # 5: 基准测试工具
    mk("T5-基准测试", "基准测试工具", "实现 benchmark.py：对 bench_functions.py 中的函数计时（timeit），多次取中位数，输出对比表（函数名/次数/中位数/标准差），支持 --repeat 参数",
       ["timeit 计时", "多次运行取中位数", "对比表输出", "--repeat 参数"],
       ["对比表含全部6个函数", "中位数合理", "标准差列存在"],
       ["benchmark.py", "bench_results.txt", "report.md"],
       f"只允许修改 {BASE/'t5_5'} 目录", str(BASE / "t5_5" / "report.md")),
    # 6: 静态文件服务器
    mk("T6-静态服务器", "静态文件服务器", "实现 fileserver.py：基于 http.server，提供 www/ 目录静态文件服务，支持 MIME 类型（html/css/js/json），目录列表页，安全处理路径穿越",
       ["http.server 扩展", "MIME 类型正确", "目录列表", "路径穿越防护"],
       ["服务器可启动", "访问 index.html 返回正确内容", "路径穿越被拒绝"],
       ["fileserver.py", "report.md"],
       f"只允许修改 {BASE/'t5_6'} 目录", str(BASE / "t5_6" / "report.md")),
    # 7: 迷你 ORM
    mk("T7-迷你ORM", "SQLite 迷你 ORM", "实现 mini_orm.py：Model 基类（字段声明→建表）、create/get/all/filter/update/delete 方法，Book 模型从 seed.json 导入数据，演示 CRUD",
       ["字段声明自动建表", "CRUD 方法", "filter 条件查询", "seed.json 导入"],
       ["CRUD 演示输出正确", "filter 查询正确", "表结构符合字段声明"],
       ["mini_orm.py", "demo_orm.py", "books.db", "report.md"],
       f"只允许修改 {BASE/'t5_7'} 目录", str(BASE / "t5_7" / "report.md")),
    # 8: ML 预处理
    mk("T8-ML预处理", "机器学习数据预处理", "实现 preprocess.py：读取 data.json，数值列标准化（z-score）和归一化（min-max）、类别列 one-hot 编码、缺失值检查，输出 processed.json",
       ["z-score 标准化", "min-max 归一化", "类别 one-hot", "缺失值检查"],
       ["processed.json 生成", "数值列均值≈0标准差≈1（z-score）", "one-hot 列正确"],
       ["preprocess.py", "processed.json", "report.md"],
       f"只允许修改 {BASE/'t5_8'} 目录", str(BASE / "t5_8" / "report.md")),
]


def run_task(idx, task_md):
    print(f"\n{'='*60}\n[任务 {idx+1}/8] 路由中...", flush=True)
    node = route(task_md, reg=registry)
    if not node:
        print("  !! 无可用节点", flush=True)
        return
    sk_before = set(HermesInstance.list_skills(node.id).keys())
    desc_before = node.describe[:30]
    print(f"  → 选中 {node.id} (score={node.score}) | 描述: {desc_before}...", flush=True)
    t0 = time.time()
    try:
        r = NODE(node.id).work(task_md, upper="ceo")
    except Exception as e:
        print(f"  !! 执行异常: {e}", flush=True)
        return
    dt = time.time() - t0
    sk_after = set(HermesInstance.list_skills(node.id).keys())
    new_skills = sk_after - sk_before
    desc_after = registry.get(node.id).describe[:60] if registry.get(node.id) else "?"
    print(f"  耗时 {dt:.0f}s | success={r.get('success')} | "
          f"eval={r.get('evaluation', {}).get('score')} | "
          f"新技能={list(new_skills) if new_skills else '无'}", flush=True)
    print(f"  新描述: {desc_after}", flush=True)
    REPORT.append({"task": idx + 1, "node": node.id, "success": r.get("success"),
                   "score": r.get("evaluation", {}).get("score"), "time": round(dt),
                   "new_skills": list(new_skills)})


def main():
    print("========== 第五轮训练开始（LLM 描述机制）==========", flush=True)
    for idx, task_md in enumerate(TASKS):
        if idx > 0 and idx % 2 == 0:
            print(f"\n>>> 触发进化（第 {idx//2} 轮）...", flush=True)
            rep = evolve()
            print(f"    种群 {rep['n_before']} → {rep['n_after']} | "
                  f"繁衍 {len(rep['births'])} 淘汰 {len(rep['eliminations'])}", flush=True)
        run_task(idx, task_md)

    print("\n" + "=" * 60, flush=True)
    print("【第五轮训练结果】", flush=True)
    for r in REPORT:
        print(f"  任务{r['task']}: {r['node']} success={r['success']} "
              f"score={r['score']} 耗时{r['time']}s 新技能={r['new_skills']}", flush=True)
    print("\n【技能基因库】", flush=True)
    gene = {}
    for n in registry.active():
        for k in HermesInstance.list_skills(n.id):
            gene.setdefault(k, []).append(n.id)
    for k, v in sorted(gene.items(), key=lambda x: -len(x[1])):
        print(f"  {k}: {len(v)} 节点 {v[:5]}{'...' if len(v)>5 else ''}", flush=True)
    print(f"\n【种群】活动: {registry.count()} | 存档: {len(registry.archived())}", flush=True)
    Path("data/train5_report.json").write_text(
        json.dumps({"results": REPORT}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("========== 第五轮训练结束 ==========", flush=True)


if __name__ == "__main__":
    main()
