"""ADGS v3 第四轮训练 — 8 个新领域复杂任务（模型已切换：CEO=flash, 节点=qwen）

新领域：REST API 综合 / 图算法 / 定时调度 / 数据校验 / 静态站点 / 密码安全 / 报告生成 / 倒排索引
技能固化链路已修复（NODE 层强命令前置），预期每个复杂任务都建 skill
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
    # 1: REST API 综合（Flask + 数据存储 + 分页 + 错误处理）
    mk("T1-REST-API", "完整的 REST API 服务", "实现 todo_api.py：Flask 提供 Todo CRUD REST API（GET/POST/PUT/DELETE），内存存储 + JSON 持久化到 todos.json，支持分页 (?page&per_page) 和 404 错误处理",
       ["四个 REST 方法齐全", "数据持久化到 todos.json", "分页参数", "404 错误 JSON 返回"],
       ["接口文档齐全", "curl 测试 POST/GET 成功", "重启后数据仍在（持久化）"],
       ["todo_api.py", "todos.json", "api_test.py", "report.md"],
       f"只允许修改 {BASE/'t4_1'} 目录", str(BASE / "t4_1" / "report.md")),
    # 2: 图算法库
    mk("T2-图算法库", "图算法库", "实现 graph.py：邻接表 Graph 类 + BFS/DFS 遍历、Dijkstra 最短路径、拓扑排序、环检测，pytest 覆盖",
       ["邻接表数据结构", "BFS/DFS", "Dijkstra", "拓扑排序+环检测", "pytest 至少 10 用例"],
       ["pytest 全部通过", "Dijkstra 正确性验证", "有环图检测正确"],
       ["graph.py", "test_graph.py", "report.md"],
       f"只允许修改 {BASE/'t4_2'} 目录", str(BASE / "t4_2" / "report.md")),
    # 3: 定时任务调度器（cron 表达式）
    mk("T3-定时调度器", "cron 表达式定时调度器", "实现 scheduler.py：解析 cron 表达式（分 时 日 月 周），计算下一次触发时间，支持注册多个任务，演示未来 5 次触发时间",
       ["cron 5 字段解析", "next_run() 计算下次触发", "多任务注册", "演示输出"],
       ["cron '*/5 * * * *' 每5分钟正确", "next_run 计算正确", "非法表达式抛异常"],
       ["scheduler.py", "demo_scheduler.py", "report.md"],
       f"只允许修改 {BASE/'t4_3'} 目录", str(BASE / "t4_3" / "report.md")),
    # 4: 数据校验库（schema）
    mk("T4-数据校验库", "schema 数据校验库", "实现 validator.py：声明式校验（类型/范围/必填/枚举/嵌套对象），校验 sample_payload.json 输出违规列表",
       ["字段类型校验", "数值范围校验", "必填字段", "枚举值校验", "嵌套对象校验"],
       ["对 sample_payload.json 校验出全部违规", "自定义规则可扩展"],
       ["validator.py", "validate_payload.py", "report.md"],
       f"只允许修改 {BASE/'t4_4'} 目录", str(BASE / "t4_4" / "report.md")),
    # 5: 静态站点生成器
    mk("T5-静态站点", "markdown 静态站点生成器", "实现 sitegen.py：读取 posts/ 目录 markdown 文件，解析 frontmatter（标题/日期/标签），生成 index.html（文章列表）+ 每篇 post_N.html，用简单 CSS",
       ["markdown → HTML 转换", "frontmatter 解析", "index 列表页", "内联 CSS 样式"],
       ["index.html 列出全部文章", "每篇 post_N.html 生成", "标题日期标签正确渲染"],
       ["sitegen.py", "index.html", "post_1.html", "report.md"],
       f"只允许修改 {BASE/'t4_5'} 目录", str(BASE / "t4_5" / "report.md")),
    # 6: 密码安全工具
    mk("T6-密码安全", "密码哈希与强度评估", "实现 password_tools.py：加盐哈希（PBKDF2/hashlib）、密码验证、强度评估（长度/字符集/常见密码黑名单），CLI 演示",
       ["PBKDF2 加盐哈希", "verify 验证", "强度打分 0-100", "黑名单检测"],
       ["哈希不存明文", "验证正确", "弱密码低分强密码高分", "黑名单密码直接拒绝"],
       ["password_tools.py", "demo_pass.py", "report.md"],
       f"只允许修改 {BASE/'t4_6'} 目录", str(BASE / "t4_6" / "report.md")),
    # 7: 多格式报告生成器
    mk("T7-报告生成器", "多格式数据报告生成", "实现 reporter.py：输入数据字典，输出 CSV / JSON / HTML 三种格式报告（含汇总统计：均值/最大/最小/计数），CLI 指定格式",
       ["三种格式输出", "汇总统计字段", "--format 参数", "--output 参数"],
       ["三种格式文件都生成正确", "HTML 含表格样式", "统计数值正确"],
       ["reporter.py", "report.csv", "report.json", "report.html", "report.md"],
       f"只允许修改 {BASE/'t4_7'} 目录", str(BASE / "t4_7" / "report.md")),
    # 8: 倒排索引搜索引擎
    mk("T8-倒排索引", "倒排索引搜索引擎", "实现 search.py：对 docs.json 建倒排索引（分词→词→文档列表），支持 AND/OR 布尔查询，输出相关文档",
       ["分词+小写化", "倒排索引结构", "AND/OR 查询", "查询结果文档"],
       ["'python AND programming' 返回 doc1", "'python OR java' 返回多文档", "无结果返回空"],
       ["search.py", "build_index.py", "report.md"],
       f"只允许修改 {BASE/'t4_8'} 目录", str(BASE / "t4_8" / "report.md")),
]


def run_task(idx, task_md):
    print(f"\n{'='*60}\n[任务 {idx+1}/8] 路由中...", flush=True)
    node = route(task_md, reg=registry)
    if not node:
        print("  !! 无可用节点", flush=True)
        return
    sk_before = set(HermesInstance.list_skills(node.id).keys())
    print(f"  → 选中 {node.id} (score={node.score}, 技能={list(sk_before)[:4]})", flush=True)
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
    print("========== 第四轮训练开始（CEO=flash, 节点=qwen）==========", flush=True)
    for idx, task_md in enumerate(TASKS):
        if idx > 0 and idx % 2 == 0:
            print(f"\n>>> 触发进化（第 {idx//2} 轮）...", flush=True)
            rep = evolve()
            print(f"    种群 {rep['n_before']} → {rep['n_after']} | "
                  f"繁衍 {len(rep['births'])} 淘汰 {len(rep['eliminations'])}", flush=True)
        run_task(idx, task_md)

    print("\n" + "=" * 60, flush=True)
    print("【第四轮训练结果】", flush=True)
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
    Path("data/train4_report.json").write_text(
        json.dumps({"results": REPORT}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("========== 第四轮训练结束 ==========", flush=True)


if __name__ == "__main__":
    main()
