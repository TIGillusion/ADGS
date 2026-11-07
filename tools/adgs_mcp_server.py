"""ADGS 节点种群 MCP server（A2A 节点大脑合并,2026-10-22 设计）

streamable-http 形态，由 ADGS runtime 进程内嵌托管（web_app._start_mesh_mcp，
uvicorn 线程），所有节点 worker(hermes) 通过插件 adgs-mesh 的 mcp.json
（type=streamable-http + token header）共享同一个 server：

- list_nodes            查询种群目录（谁擅长什么）——节点间的"服务发现"
- delegate_to_node      把子任务委派给指定节点（立即返回回执，不阻塞）
- wait_delegation       长轮询等待/跟进委派结果（每次 ≤150s，规避工具超时）
- report_delegation_verdict  父节点复核子汇报并给出判定（驱动进化分）

与 hermes 自带的 delegate_task（进程内 spawn 内部 subagent）完全隔离：
节点间委派经 /internal/mesh/* 端点在独立进程/独立 hermes 会话中执行。
与 runtime 同进程 → 事件（a2a.delegate/a2a.verdict）天然进入事件总线。

数据访问经 MeshProvider 回调注入（web_app 侧实现），本模块不 import
web_app，避免循环依赖；`python tools/adgs_mcp_server.py --standalone`
仅为联调用（HTTP 直跑，provider 为空时工具返回错误）。
"""
from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP

_ROOT = Path(__file__).resolve().parents[1]
_STATE_DIR = Path(os.environ.get("ADGS_STATE_DIR") or (_ROOT / ".adgs_runtime"))
# hermes 的 MCP 工具调用默认超时 300s；wait 每次调用必须留足余量。
_MAX_WAIT_SECONDS = 150

mcp = FastMCP("adgs-mesh")


class MeshProvider:
    """web_app 侧注入的数据访问（委派注册表 / registry / 事件总线）。"""

    def __init__(self):
        self.directory = Callable[[], dict]  # noqa: 仅类型提示占位
        self.probe = Callable[[str, str], dict]
        self.delegate = Callable[[str, str, str, list], dict]
        self.continue_delegation = Callable[[str, str, str], dict]
        self.wait = Callable[[str, float], dict]
        self.verdict = Callable[[str, bool, str, float], dict]
        self.escalate = Callable[[str, str, bool, str, float], dict]


_provider: MeshProvider | None = None


def configure_provider(**callbacks: Callable) -> None:
    global _provider
    _provider = MeshProvider()
    for name, fn in callbacks.items():
        setattr(_provider, name, fn)


def _call(name: str, *args) -> dict:
    if _provider is None:
        raise RuntimeError("adgs-mesh 未接入 ADGS runtime（standalone 模式无数据）")
    return getattr(_provider, name)(*args)


@mcp.tool()
def list_nodes(query: str = "", top: int = 8, parent_execution_id: str = "") -> str:
    """查询 ADGS 节点种群目录：每个活跃节点的编号、生态位描述（擅长什么）、技能包、
    分数、成功率、完成任务数、探索加成，以及（填 parent_execution_id 时）与你的历史协作次数。

    这是委派选人的起点，鼓励多次、多角度调查：query 填子任务的一个侧面（如"设备控制"），
    换个角度再查一次（如"测试 报告"），返回任务最适配的 top 个节点；不填 query 时，
    有 parent_execution_id 则使用父任务目标检索，否则按中性适配与冷启动推荐。
    缺少匹配专长时通用节点优先；累计分不能替代任务匹配。对候选 probe_node 深查后再决定。
    explore_recommended=True 是「探索推荐」——同类候选中优先给它们机会，不要永远只用熟节点；
    collab_with_me 是该节点与你过去的协作记录（系统客观记录），熟人可靠，但权衡着用。"""
    try:
        nodes = _call("directory", str(query or ""), int(top if top else 8),
                      str(parent_execution_id or ""))
    except (TypeError, ValueError):
        nodes = _call("directory", str(query or ""), 8, str(parent_execution_id or ""))
    if not nodes:
        return "种群当前没有其他活跃节点。"
    header = (f"种群目录（按与「{query}」的任务适配排序，显示前 {len(nodes)} 个）"
              if str(query or "").strip() else
              f"种群目录（当前任务适配优先，冷启动与有限质量修正，显示前 {len(nodes)} 个）")
    lines = [header + "："]
    for n in nodes:
        mark = "【探索推荐】" if n.get("explore_recommended") else ""
        sim = f"｜相关度 {n['similarity']:.2f}" if n.get("similarity") is not None else ""
        fit = f"｜有效适配 {n['relevance']:.2f}" if n.get('relevance') is not None else ''
        kind = '【通用】' if n.get('generalist') else ''
        identity = '【同角色，可新开实例】' if n.get('same_role') else ''
        collab = ""
        c = n.get("collab_with_me")
        if c is not None:
            collab = f"｜与你协作 {c.get('count', 0)} 次（通过 {c.get('passed', 0)}）"
        lines.append(
            f"- {kind}{mark}{identity}{n['id']}{fit}{sim}｜{n.get('describe', '')[:110]}｜累计分 {n.get('score', 50):.1f}｜"
            f"成功率 {n.get('success_rate', 0):.0%}｜完成 {n.get('tasks_done', 0)} 任务"
            f"｜探索加成 ×{n.get('explore_boost', 1.0)}{collab}"
            + (f"｜技能 {', '.join(n['skills'])}" if n.get("skills") else ""))
    lines.append("（可换 query 再查其他角度；对候选 probe_node 深查技能与战绩后再委派）")
    return "\n".join(lines)


@mcp.tool()
def probe_node(node_id: str, parent_execution_id: str = "") -> str:
    """深查一个节点的档案，委派前的尽职调查：生态位描述全文、技能包清单（名字+用途）、
    最近执行记录（任务与结果）、当前忙闲、与你的双向协作史。
    用法：list_nodes 圈定 2-3 个候选 → 分别 probe_node 对比 → 选定后 delegate_to_node。
    parent_execution_id 填你自己的执行 ID（任务书「种群协作」一节里有）。"""
    data = _call("probe", str(node_id or ""), str(parent_execution_id or ""))
    if not data.get("ok"):
        raise RuntimeError(data.get("error") or "probe 失败")
    n = data["node"]
    lines = [f"节点 {n['id']} 档案：",
             f"- 生态位描述：{n.get('describe', '')}",
             f"- 分数 {n.get('score', 50):.1f}｜成功率 {n.get('success_rate', 0):.0%}｜"
             f"完成 {n.get('tasks_done', 0)} 任务｜探索加成 ×{n.get('explore_boost', 1.0)}"]
    skills = n.get("skills") or {}
    if skills:
        lines.append(f"- 技能包（{len(skills)} 个）：")
        for name, desc in skills.items():
            lines.append(f"  · {name}：{desc}")
    else:
        lines.append("- 技能包：无自定义技能（能力以生态位描述为准）")
    recent = data.get("recent") or []
    if recent:
        lines.append(f"- 近期执行（最近 {len(recent)} 条）：")
        for r in recent:
            lines.append(f"  · [{r.get('state')}] {r.get('task_id')}｜{(r.get('detail') or '')[:100]}")
    else:
        lines.append("- 近期执行：无记录（新节点或尚未接任务）")
    lines.append("- 当前状态：" + (
        f"执行中（{data['running_now']} 个任务进行中，委派前考虑排队）"
        if data.get("running_now") else "空闲"))
    collab = data.get("collab")
    if collab is not None:
        a = collab.get("i_delegated_to") or {}
        b = collab.get("delegated_to_me") or {}
        lines.append(f"- 与我的协作史：我委派给它 {a.get('count', 0)} 次（通过 {a.get('passed', 0)}）；"
                     f"它委派给我 {b.get('count', 0)} 次（通过 {b.get('passed', 0)}）")
    return "\n".join(lines)


@mcp.tool()
def delegate_to_node(parent_execution_id: str, node_id: str, task_md: str,
                     shared_resources: list[str] = None) -> str:
    """把一个边界清晰的子任务委派给指定节点（它在独立进程、独立 hermes 会话中执行）。

    parent_execution_id = 你当前执行的执行 ID（任务书「种群协作」一节里有）。
    task_md 必须是完整子任务书：目标、要求、验收标准、交付物、允许写入的范围。
    node_id 可以填写你自己的节点 ID，表示同角色的全新执行实例和对话；共享记忆技能，
    不恢复本次上下文。必须缩小子任务范围，不允许把父任务原封不动递归转发。
    shared_resources：该子任务会操作的外部互斥资源（真实设备/端口/远端服务，如
    "米家:测试插座"）。你同时发出的多个委派若操作同一外部资源，只有先发的会受理，
    后发的会被拒绝——请 wait 完成并复核后再发下一个，否则它们会互相打架。
    立即返回委派回执（delegation_id），不阻塞——随后用 wait_delegation 跟进结果。
    困难可拆任务应优先分工；可并行探索、跨专长协作、独立验收，也可按阶段串行交接。"""
    data = _call("delegate", node_id, task_md, parent_execution_id,
                 [str(s) for s in (shared_resources or []) if str(s).strip()])
    if not data.get("ok"):
        raise RuntimeError(data.get("error") or "委派被拒绝")
    return (f"委派已受理：delegation_id={data['delegation_id']}，"
            f"节点 {node_id} 开始执行。用 wait_delegation 跟进结果。")


@mcp.tool()
def continue_delegation(parent_execution_id: str, delegation_id: str, instruction: str) -> str:
    """连续上下文返工/恢复自己的原委派，保留原节点、execution 和 Hermes 会话。

    仅用于同一任务补证据、修正缺项或继续工作；独立新上下文用 delegate_to_node。
    原委派必须已返回（含 passed/failed/interrupted），不能重复恢复正在执行或排队的任务。
    新输入增加子节点轮次，父轮次不变。返回后仍用原 delegation_id 调 wait_delegation，
    汇报返回后重新 report_delegation_verdict。不要重复派发整份已完成任务。
    """
    data = _call("continue_delegation", parent_execution_id, delegation_id, instruction)
    if not data.get("ok"):
        raise RuntimeError(data.get("error") or "原委派恢复被拒绝")
    return (f"原委派已续接：delegation_id={data['delegation_id']}，execution_id={data['child_execution_id']}；"
            "保留原节点与 Hermes 上下文。请 wait_delegation 等待本次汇报。")


@mcp.tool()
def wait_delegation(delegation_id: str, timeout_s: int = 120) -> str:
    """等待/跟进一个委派的结果。完成时返回子节点的完整汇报；未完成时返回当前
    进度（可再次调用继续等待，或先并行处理别的事务）。timeout_s 上限 150 秒。"""
    wait = max(1, min(int(timeout_s), _MAX_WAIT_SECONDS))
    data = _call("wait", delegation_id, wait)
    if not data.get("ok"):
        raise RuntimeError(data.get("error") or "委派不存在")
    status = data.get("status", "running")
    if status == "running":
        elapsed = int(data.get("elapsed", 0))
        return (f"委派 {delegation_id} 仍在执行（已 {elapsed}s，节点 {data.get('child_node_id')}）。"
                f"可继续 wait_delegation，或先处理其他事务。")
    parts = [f"委派 {delegation_id} 已结束，状态：{status}（节点 {data.get('child_node_id')}）。"]
    if data.get("summary"):
        parts.append(f"子节点汇报：\n{data['summary']}")
    if data.get("deliverables"):
        parts.append("交付物：" + ", ".join(data["deliverables"]))
    if data.get("verdict"):
        parts.append(f"你已复核：{'通过' if data['verdict'].get('passed') else '未通过'}"
                     f"（{data['verdict'].get('note', '')}）")
    else:
        parts.append("请复核这份汇报，并调用 report_delegation_verdict 给出判定。")
    return "\n".join(parts)


@mcp.tool()
def report_delegation_verdict(delegation_id: str, passed: bool, note: str = "",
                              score: float = None) -> str:
    """复核子节点汇报后给出你的验收判定（你是该委派的直系上级，打分是你的职责）。
    passed=True 表示该子任务验收通过；note 写明复核依据或不满之处；score 用 -10~+10
    区分质量差异（+10 卓越 / 0 勉强达标 / -10 完全失败；缺省按通过与否取 ±10）。
    建议先派一个未参与该工作的节点独立验证（见协作纪律第 5 条），再综合打分。
    判定会驱动该子节点的进化分数——请如实；每次幅度有限，不会一次打穿。"""
    data = _call("verdict", delegation_id, bool(passed), note, score)
    if not data.get("ok"):
        raise RuntimeError(data.get("error") or "复核失败")
    child = data.get("child_node_id", "?")
    applied = data.get("applied")
    detail = f"（分数修正 {applied:+.1f}）" if applied is not None else ""
    return (f"已记录复核判定（节点 {child}：{'通过' if passed else '未通过'}{detail}）。"
            + ("" if passed else " 连续上下文补救请用 continue_delegation 并附上 note；新上下文独立工作才用 delegate_to_node。"))


@mcp.tool()
def escalate_delegation_verdict(delegation_id: str, parent_execution_id: str,
                                passed: bool, note: str, score: float) -> str:
    """越级纠错（例外权）：你（委派父的直系上级）发现下属的下属（孙辈）的委派成果
    有明显问题——而它的直系上级没有发现——核验属实后，可越级对该孙辈的委派给出判定。
    passed=False 为扣分、True 为纠正性加分；score -10~+10 表示幅度；note 必须写明
    你发现了什么、如何验证（没有证据不要动用此权）。一般打分只给直系下属；
    系统会校验你确实是该委派父的直系上级，越权会被拒绝并留档。"""
    data = _call("escalate", delegation_id, str(parent_execution_id or ""),
                 bool(passed), str(note or ""), score)
    if not data.get("ok"):
        raise RuntimeError(data.get("error") or "越级纠错失败")
    return (f"越级判定已记录（节点 {data.get('child_node_id')}：{'通过' if passed else '未通过'}，"
            f"分数修正 {data.get('applied', 0):+.1f}），证据已留档。")


def mesh_state_file() -> Path:
    return _STATE_DIR / "mesh_mcp.json"


def read_mesh_state() -> dict | None:
    """runtime 重启后的 MCP 端点/令牌（hermes 插件刷新依据）；未启动返回 None。"""
    try:
        return json.loads(mesh_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_mesh_state(url: str, token: str) -> None:
    mesh_state_file().parent.mkdir(parents=True, exist_ok=True)
    temporary = mesh_state_file().with_suffix(".json.tmp")
    temporary.write_text(json.dumps({"url": url, "token": token}, ensure_ascii=False), encoding="utf-8")
    temporary.replace(mesh_state_file())


def build_mesh_asgi(token: str = None):
    """streamable-http ASGI app（/mcp）+ token 鉴权（供 runtime 内嵌 uvicorn）。

    直接在 FastMCP 自带的 Starlette app 上注入鉴权中间件（不外层包装——
    mount 子应用的 lifespan 不会被 uvicorn 触发，session manager 会
    "Task group is not initialized"，2026-10-23 实测）。
    返回 (app, token)；url/token 写入 mesh_mcp.json 供插件刷新与联调发现。
    token 可传入固定值——runtime 重启复用旧 token，避免全部 profile 的
    mesh 注册（plugins/mcp.json + config mcp_servers）同时失效再逐个刷新
    （2026-10-24 实测：token 轮换后 worker 工具静默失联）。
    """
    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    token = token or secrets.token_urlsafe(24)

    class _TokenGate(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if request.headers.get("X-ADGS-Mesh-Token", "") != token:
                return JSONResponse({"error": "mesh token required"}, status_code=401)
            return await call_next(request)

    app = mcp.streamable_http_app()
    app.user_middleware.insert(0, Middleware(_TokenGate))
    app.middleware_stack = None  # 重建 middleware stack（app 已构建过默认栈）
    return app, token


def main_standalone(port: int = 8033) -> None:
    """联调入口：python tools/adgs_mcp_server.py --standalone [--port N]"""
    import uvicorn
    app, token = build_mesh_asgi()
    write_mesh_state(f"http://127.0.0.1:{port}/mcp", token)
    print(f"[adgs-mesh] standalone mesh MCP on http://127.0.0.1:{port}/mcp"
          f"（token 见 {mesh_state_file()}）", file=sys.stderr, flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    port = 8033
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    main_standalone(port)
