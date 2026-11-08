"""ADGS Web 前端入口 — Flask + SSE（对话 / 实时图谱 / 节点详情）

启动：python web_app.py  或  双击 start_web.bat
前端：web/static/index.html（单页，Canvas 图谱 + 对话 + 节点详情）
事件：GET /api/events（SSE 流）；core 埋点经 web.event_bus 推送
"""
import json
import logging
import os
import queue
import re
import sys
import datetime
import sqlite3
import threading
import time
import uuid
from functools import wraps
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory
from web.control_api import authenticate_control, create_control_api

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.config import config
from core.execution_store import WORKER_HOLD_REASON, recovery_grant, worker_launch_on_hold
from core.session_context import session_scope, runtime_session, submit_context
from core.project_catalog import project_catalog, session_workspace
from core.registry import registry
from web.event_bus import bus, set_task_session, get_task_session, event_scope

# 路径消毒（2026-08-30 P2-14 修复）：会话名/技能名直接参与文件路径拼接，
# 未消毒时 /api/session/load 传 ../../x 可把文件写到 data/sessions 之外。
_SESS_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")
_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9_\-\.]{1,120}$")


def _sanitize_name(value, pattern):
    """校验并返回安全名称（会话名/技能名/节点 id），非法返回 None"""
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s or ".." in s or "\\" in s or "/" in s:
        return None
    return s if pattern.match(s) else None

WEB_DIR = Path(__file__).resolve().parent          # 项目根（web_app.py 在根）
STATIC_DIR = WEB_DIR / "web" / "static"            # 前端静态文件
PORT = 8021

app = Flask(__name__, static_folder=str(STATIC_DIR),
            static_url_path="/static")
from web.execution_api import execution_api
app.register_blueprint(execution_api)
app.before_request(authenticate_control)

@app.before_request
def _runtime_proxy():
    if app.config.get("ADGS_PROXY_RUNTIME") and request.path.startswith("/api/"):
        from core.runtime_host import proxy_request
        return proxy_request(request)


@app.before_request
def _validate_json_object():
    if request.path.startswith('/api/v1/'):
        return
    if request.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH"}:
        data = request.get_json(force=True, silent=True)
        if request.content_length and not isinstance(data, dict):
            return jsonify({"ok": False, "error": "请求体必须是 JSON 对象"}), 400



# ---- Web2 新版前端（2026-09-02 重写版，旧版保留在 /）----
# 新版静态文件放项目根 web2/ 目录，通过 /web2/ 访问；两版并存互不影响。
WEB2_DIR = ROOT / "web2"


def _path_under(base: Path, p: Path) -> bool:
    """p 是否位于 base 目录内（commonpath 比较）。

    startswith 不带尾分隔符时可被同级前缀目录绕过（如 base=web3 时
    web3-evil、web3dist2 都能通过前缀检查），2026-09-27 P1 修复。
    """
    try:
        return os.path.commonpath((str(base), str(p))) == str(base)
    except ValueError:
        return False


@app.route("/web2/")
@app.route("/web2/<path:filename>")
def web2_index(filename="index.html"):
    """新版前端入口（旧版 / 保留不动，新旧并存对照）"""
    p = (WEB2_DIR / filename).resolve()
    if not _path_under(WEB2_DIR.resolve(), p) or not p.exists():
        return "not found", 404
    return send_from_directory(WEB2_DIR, filename)


# ---- Web3 全新前端（2026-09-02 从零重写版：React+Vite+TS+Canvas 实时图谱）----
# 构建产物在 web3/dist/（Vite 相对 base，资源相对 HTML 引用），经 /web3/ 提供；
# 与旧版 / 及 web2 /web2/ 三版并存互不影响。
WEB3_DIR = ROOT / "web3" / "dist"


@app.route("/web3/")
@app.route("/web3/<path:filename>")
def web3_index(filename="index.html"):
    """web3 前端入口（提供 web3/dist 构建产物）"""
    p = (WEB3_DIR / filename).resolve()
    if not _path_under(WEB3_DIR.resolve(), p) or not p.exists():
        return "not found", 404
    return send_from_directory(WEB3_DIR, filename)

# ---------------- CEO 单例 + 任务锁 ----------------

_ceo = None
_ceo_lock = threading.Lock()
_busy = {"state": False}
_running_sessions = {}
_run_stop_events = {}
_session_ceos = {}
_task_lock = threading.RLock()
# 进化互斥锁：启动进化线程与手动 /evolve 并发会交叉操作 registry
# （两个 evolve 同时 next_id/add/archive → 报告数字错乱），必须串行
_evolve_lock = threading.Lock()
# web 启动时间戳：前端据此识别后端重启（重启后 CEO 上下文/事件历史清空，
# 前端图谱也应清空保持同步，2026-08-24）
BOOT_TS = time.time()


def _idle_mutation(fn):
    @wraps(fn)
    def guarded(*args, **kwargs):
        with _task_lock:
            if _busy["state"]:
                return jsonify(ok=False, error="任务执行中，暂不能修改此状态"), 409
            return fn(*args, **kwargs)
    return guarded


def get_ceo():
    global _ceo
    session = runtime_session.get()
    if session:
        with _ceo_lock:
            if session not in _session_ceos:
                from core.ceo import CEO
                _session_ceos[session] = CEO()
            return _session_ceos[session]
    if _ceo is None:
        with _ceo_lock:
            if _ceo is None:
                from core.ceo import CEO
                _ceo = CEO()
    return _ceo


def _ceo_for_session(session):
    with session_scope(session):
        return get_ceo()


def _session_busy(session):
    if session in _running_sessions:
        return True
    # Old callers/tests can expose an aggregate busy without an identified owner.
    return bool(_busy["state"] and not _running_sessions and (not session or get_task_session() in (None, session)))


def _startup_evolve():
    """启动时后台触发一轮进化（与 main.py「新对话触发进化」语义一致），
    事件推送到前端图谱展示；失败不阻塞启动。"""
    if worker_launch_on_hold():
        return
    try:
        from core.evolution import evolve
        from core.registry import registry as reg
        # 锁外预热 embedding（2026-08-30）：首次加载 bge 模型 ~18s（D盘慢
        # 时更久），若在进化锁内会阻塞所有任务（用户提交任务后长时间无反应）。
        # 预热后再进锁，evolve 内直接命中缓存，锁内只做纯计算。
        try:
            from utils import embedding as _emb
            _emb.get_embedding("预热")
        except Exception:
            pass
        # 空池补初始化（2026-08-30 P0 修复）：web 首启无 ensure_initial，
        # 任务路由会"无可用节点"、evolve 也会崩——先建初始种群再进化。
        with _evolve_lock:
            with _task_lock:
                if _busy["state"]:
                    bus.emit("system", msg="启动进化已延后：当前任务优先")
                    return
            report = evolve()
        bus.emit("system",
                 msg=f"启动进化：繁衍 {len(report['births'])} 淘汰 "
                     f"{len(report['eliminations'])}，种群 "
                     f"{report['n_before']}→{report['n_after']}")
    except Exception as e:
        bus.emit("system", msg=f"启动进化失败（忽略）：{str(e)[:200]}")


# ---------------- 路由 ----------------

@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/api/state")
def api_state():
    """初始快照：种群 / 模型 / 节点列表 / 最近事件 / 忙碌状态"""
    nodes = []
    for n in registry.active():
        nodes.append({
            "id": n.id, "describe": (n.describe or "")[:80],
            "score": n.score, "success_rate": n.success_rate,
            "tasks_done": n.tasks_done, "birth_type": n.birth_type,
            "gen": n.gen,
        })
    from core.evolution import effective_capacity
    selected = request.args.get("session")
    meta = project_catalog.session(selected) if selected else None
    pending = _queue_snapshot(selected)
    owner = selected or _current_sess.get("name")
    ceo_state = getattr(_session_ceos.get(owner), "_states", {}).get(owner, {})
    if owner and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", owner) and not ceo_state:
        try:
            ceo_state = json.loads((config.DATA_DIR / "sessions" / (owner + ".json")).read_text(
                encoding="utf-8")).get("session_state") or {}
        except (OSError, ValueError):
            pass
    ceo_busy = _session_busy(selected) if selected else _busy["state"]
    ceo_activity = {**(ceo_state.get("_ceo_activity") or {}), "engine": "hermes", "role": "coordinator",
                    "model": config.ceo_model, "execution_id": ceo_state.get("_hermes_execution_id"),
                    "hermes_session_id": ceo_state.get("_hermes_session_id")}
    if not ceo_busy and ceo_activity.get("phase") in {"deciding", "inspecting", "waiting_children", "reviewing", "reporting"}:
        ceo_activity["phase"] = "awaiting_user" if ceo_state.get("_awaiting_input") else "interrupted"
    ceo_activity.setdefault("phase", "deciding" if ceo_busy else "idle")
    from core import node as node_runtime
    capacity = node_runtime.execution_capacity() if hasattr(node_runtime, "execution_capacity") else {}
    from web.execution_api import runtime_fields
    execution_states = [{**row, **runtime_fields(row['execution_id'], row.get('node_id'), row)}
                        for row in execution_store.session_states(owner)]
    snapshot_at = time.time()
    return jsonify({
        "ok": True,
        "workspace": meta["workspace"] if meta else config.workspace,
        "capabilities": {"projects": True, "concurrent_sessions": True, "queue_guidance": True},
        "runs": {name: {"busy": True, **value} for name, value in _running_sessions.copy().items()},
        "population": {"active": registry.count(),
                       "capacity": effective_capacity(registry.active(),
                                                      update_ema=False)},
        "models": {"ceo": config.ceo_model, "brain": config.brain_model,
                   "worker": config.worker_model},
        "runtime": {"max_workers": config.max_workers_effective, **capacity},
        "advanced": {"tau": config.tau,
                     "explore_beta": config.router_explore_beta},
        "nodes": nodes,
        "events": [_display_event(e) for e in bus.history(1500) if not selected or e.get("session") == selected],
        "event_id": bus.last_id,
        "ceo_execution_id": ceo_state.get("_hermes_execution_id"),
        "ceo": ceo_activity,
        "executions": execution_states,
        "snapshot_at": snapshot_at,
        "busy": _session_busy(selected) if selected else _busy["state"],
        "worker_hold": worker_launch_on_hold(),
        "mesh": _mesh_status,
        "queue_len": len(pending),
        "queue": pending,
        "boot_ts": BOOT_TS,
    })


# ---------------- 模型配置（2026-09-27 web 配置入口） ----------------

def _models_payload() -> dict:
    """当前模型配置（不含 key——密钥不回传前端）"""
    return {
        "ceo": {"model": config.ceo_model, "url": config.ceo_url},
        "brain": {"model": config.brain_model, "url": config.brain_url},
        "worker": {"model": config.worker_model, "url": config.worker_url},
    }


@app.get("/api/models")
def api_models_get():
    """当前模型配置（CEO / 决策大脑 / hermes 执行器）"""
    return jsonify({"ok": True, "models": _models_payload()})


@app.post("/api/models")
def api_models_set():
    """更新模型配置并热生效（不用重启）

    - ceo：CEO 单例的聊天客户端立即切换（进行中的下一次 LLM 调用起生效）
    - brain：决策大脑每次派发新建 AGENT，改完即对下一个任务生效；
      CEO 澄清回答复用 brain 模型（缓存客户端），置空让其重建
    - worker（hermes）：已存在的节点 profile 钉住出生时的模型继续使用，
      之后新建的节点（繁衍/自愈补建）才用新模型 —— 旧的保持，新起的用新
    """
    data = request.get_json(force=True, silent=True) or {}
    changes = {}
    for group in ("ceo", "brain", "worker"):
        ch = data.get(group)
        if not isinstance(ch, dict):
            continue
        entry = {}
        for field in ("model", "url", "key"):
            v = ch.get(field)
            if isinstance(v, str) and v.strip():
                entry[field] = v.strip()
        if "url" in entry and not entry["url"].startswith(("http://", "https://")):
            return jsonify({"ok": False, "error": f"{group}.url 需为 http(s):// 地址"})
        if entry:
            changes[group] = entry
    if not changes:
        return jsonify({"ok": False, "error": "没有可应用的修改（模型/URL/Key 均为空）"})
    try:
        applied = config.set_models(changes)
    except Exception as e:
        return jsonify({"ok": False, "error": f"写入配置失败: {e}"})

    # ---- 热生效 ----
    for ceo in ([_ceo] if _ceo is not None else []) + list(_session_ceos.copy().values()):
        if "ceo" in applied:
            ceo.chat.reconfigure(url=config.ceo_url, model=config.ceo_model, key=config.ceo_key)
        if "brain" in applied:
            ceo._clarify_chat = None

    parts = []
    if "ceo" in applied:
        parts.append(f"CEO → {applied['ceo']['model']}（立即生效）")
    if "brain" in applied:
        parts.append(f"决策大脑 → {applied['brain']['model']}（下一任务生效）")
    if "worker" in applied:
        parts.append(f"hermes 执行 → {applied['worker']['model']}"
                     f"（仅之后新建的节点使用；已有节点保持各自模型）")
    msg = "模型配置已更新：" + "；".join(parts)
    bus.emit("system", msg=msg)
    return jsonify({"ok": True, "msg": msg, "models": _models_payload()})


# ---------------- 运行/调优参数（2026-09-28 web 系统设置） ----------------

# 字段 → (最小值, 最大值, 类型)。范围外一律拒绝（防手滑把超时改成 0 之类）。
_RUNTIME_SPEC = {
    "max_workers": (1, 32, int),
    "max_delegation_depth": (1, 10, int),
    "worker_timeout": (60, 86400, int),
    "inactivity_timeout": (60, 14400, int),
}
_ADVANCED_SPEC = {
    "tau": (0.0, 20.0, float),
    "sim_weight": (0.0, 10.0, float),
    "score_weight": (0.0, 10.0, float),
    "explore_beta": (0.0, 3.0, float),
    "top_k": (1, 20, int),
    "temperature": (0.0, 2.0, float),
}


def _tuning_payload() -> dict:
    return {
        "runtime": {
            "max_workers": config.max_workers_effective,
            "max_delegation_depth": config.max_delegation_depth,
            "worker_timeout": config.worker_timeout,
            "inactivity_timeout": config.inactivity_timeout,
        },
        "advanced": {
            "tau": config.tau,
            "sim_weight": config.router_sim_weight,
            "score_weight": config.router_score_weight,
            "explore_beta": config.router_explore_beta,
            "top_k": config.top_k,
            "temperature": config.temperature,
        },
    }


def _parse_tuning(data, spec):
    """按 spec 校验并转换字段；非法抛 ValueError（消息含字段名与范围）"""
    out = {}
    for name, (lo, hi, typ) in spec.items():
        if name not in data or data[name] is None or data[name] == "":
            continue
        try:
            v = typ(data[name])
        except (TypeError, ValueError):
            raise ValueError(f"{name} 需为数字")
        if not (lo <= v <= hi):
            raise ValueError(f"{name} 需在 {lo}~{hi} 内")
        out[name] = v
    return out


@app.get("/api/settings")
def api_settings_get():
    """当前运行/调优参数（每轮进化/每次派发实时读取，改动即时生效）"""
    return jsonify({"ok": True, **_tuning_payload()})


@app.post("/api/settings")
def api_settings_set():
    """更新运行/调优参数并热生效（不用重启，落盘 settings_overrides.json）

    - 并行上限：派发线程池按次构造 + 信号量 resize，立即生效（不中断执行中任务）
    - 生存压力/路由权重等：每轮进化/每次派发实时读取，下一轮/下一任务生效
    """
    data = request.get_json(force=True, silent=True) or {}
    try:
        runtime = _parse_tuning(data.get("runtime") or {}, _RUNTIME_SPEC)
        advanced = _parse_tuning(data.get("advanced") or {}, _ADVANCED_SPEC)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)})
    if not runtime and not advanced:
        return jsonify({"ok": False, "error": "没有可应用的修改"})
    changed_r, changed_a = config.set_tuning(runtime, advanced)
    if "max_workers" in changed_r:
        from core.node import resize_exec_semaphore
        resize_exec_semaphore(changed_r["max_workers"])

    parts = []
    if "max_workers" in changed_r:
        parts.append(f"并行上限 → {changed_r['max_workers']}（立即生效）")
    for label, key in (("委派深度", "max_delegation_depth"),
                       ("模型请求预算", "worker_timeout"),
                       ("无活动超时", "inactivity_timeout")):
        if key in changed_r:
            parts.append(f"{label} → {changed_r[key]}s" if "超时" in label
                         else f"{label} → {changed_r[key]}")
    if "tau" in changed_a:
        parts.append(f"生存压力 τ → {changed_a['tau']:g}")
    if "sim_weight" in changed_a or "score_weight" in changed_a:
        parts.append(f"分配权重：专精 {config.router_sim_weight:g} / "
                     f"得分 {config.router_score_weight:g}")
    if "explore_beta" in changed_a:
        parts.append(f"探索系数 → {changed_a['explore_beta']:g}"
                     + ("（关闭额外探索加成）" if changed_a["explore_beta"] == 0 else ""))
    for label, key in (("路由候选", "top_k"), ("采样温度", "temperature")):
        if key in changed_a:
            parts.append(f"{label} → {changed_a[key]:g}")
    msg = "参数已更新：" + "；".join(parts)
    bus.emit("system", msg=msg)
    return jsonify({"ok": True, "msg": msg, **_tuning_payload()})


@app.post("/api/chat")
def api_chat():
    """提交任务给 CEO（后台线程跑 talk，事件经 SSE 推送）

    mode=queue 等待整个任务结束；mode=interrupt 等待已派发批次汇报后引导。
    """
    data = request.get_json(force=True, silent=True) or {}
    if not isinstance(data.get("text"), str):
        return jsonify({"ok": False, "error": "text 必须是字符串"}), 400
    text = data["text"].strip()
    if not text:
        return jsonify({"ok": False, "error": "消息为空"})
    if text.startswith("/"):
        return jsonify({"ok": False,
                        "error": "这是系统指令，请在对话框发送（/help 查看可用指令）"})
    sess_name = data.get("session")
    if sess_name is not None and not _sanitize_name(sess_name, _SESS_NAME_RE):
        return jsonify({"ok": False, "error": "会话名非法"}), 400
    mode = data.get("mode", "queue")
    if mode not in ("queue", "interrupt"):
        return jsonify({"ok": False, "error": "mode 必须是 queue 或 interrupt"}), 400
    result = _submit_chat_message(text, sess_name, mode)
    return jsonify(result), 200 if result["ok"] else result.get("http_status", 503)


# 任务队列：当前任务结束后自动执行下一个（/queue 功能）
# 条目：{"id": 稳定标识, "text", "session", "workspace", "queued_at", "mode": queue|interrupt}
from core.message_queue import DurableMessageQueue
_queue = DurableMessageQueue(Path(os.environ.get(
    "ADGS_STATE_DIR", str(ROOT / ".adgs_runtime"))) / "pending_messages.json")
_queue_lock = threading.Lock()


def _checkpoint_queue_locked():
    checkpoint = getattr(_queue, "checkpoint", None)
    if checkpoint:
        checkpoint()


def _queue_snapshot(session=None):
    """线程安全读出队列内容（api_state / GET /api/queue 共用）"""
    with _queue_lock:
        return [{"id": t.get("id"), "text": t.get("text", ""),
                 "session": t.get("session"), "mode": t.get("mode", "queue"),
                 "queued_at": t.get("queued_at")}
                for t in _queue if isinstance(t, dict) and (not session or t.get("session") == session)]


def _has_guidance(session):
    with _queue_lock:
        return any(isinstance(t, dict) and t.get("mode") == "interrupt" and t.get("session") == session
                   for t in _queue)


def _deliver_guidance(session, receive):
    # Keep edits/removals atomic with delivery. A failed receiver leaves every item queued.
    with _queue_lock:
        items = [t for t in _queue if isinstance(t, dict) and t.get("mode") == "interrupt" and t.get("session") == session]
        if not items:
            return False
        receive(items)
        ids = {t["id"] for t in items}
        _queue[:] = [t for t in _queue if not isinstance(t, dict) or t.get("id") not in ids]
    _emit_queue_notifications({"type": "queue.update", "data": {"queue": _queue_snapshot()}}, session=session)
    return True


def _emit_queue_notifications(*events, session=None):
    """队列变更后的通知推送：纯可观测性，失败只记日志不影响请求结果。
    每个事件为 {"type": 事件名, "data": {字段...}}。"""
    for ev in events:
        try:
            bus.emit(ev["type"], **{**ev.get("data", {}), **({"session":session} if session else {})})
        except Exception:
            logging.getLogger(__name__).exception("Queue notification failed")


# ---------------- A2A 节点大脑合并（merged_brain）：种群协作内部端点 ----------------
#
# 每个节点 worker(hermes) 经 MCP server(tools/adgs_mcp_server.py, stdio)调用
# 这些端点完成节点间协作。鉴权：X-ADGS-Mesh-Token 对 .adgs_runtime/mesh_token。

_MESH_TOKEN_FILE = Path(os.environ.get(
    "ADGS_STATE_DIR", str(Path(__file__).resolve().parent / ".adgs_runtime"))) / "mesh_token"


def _mesh_token() -> str:
    """固定协作令牌：首启生成并落盘，重启不变（worker 插件 env 引用同一文件）。"""
    try:
        return _MESH_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        token = uuid.uuid4().hex
        _MESH_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _MESH_TOKEN_FILE.write_text(token, encoding="utf-8")
        return token


def _mesh_auth():
    return request.headers.get("X-ADGS-Mesh-Token", "") == _mesh_token()


_mesh_delegations = {}          # delegation_id -> 记录（含 status/summary/verdict）
_mesh_lock = threading.Condition()
_MESH_PENDING_CAP = 200


def _mesh_delegations_file():
    return _mesh_state_dir() / "mesh_delegations.json"


def _load_delegations():
    """runtime 启动时恢复委派档案（A2A 记录留档，节点可查、重启不丢）。"""
    try:
        f = _mesh_delegations_file()
        if f.is_file():
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _mesh_delegations.update(data)
                changed = False
                # A new runtime has no threads for an old in-flight record.
                # Reconcile durable results or make the original resumable;
                # leaving it running would make wait/continue loop forever.
                for record in data.values():
                    if not isinstance(record, dict) or record.get("status") != "running":
                        continue
                    from core.node import active_cancel
                    eid = record.get("child_execution_id")
                    if eid and active_cancel(eid) is not None:
                        continue
                    child = execution_store.get(eid) if eid else None
                    result = (child or {}).get("result") or {}
                    state = (child or {}).get("state")
                    summary, outputs = _mesh_summary_of(result)
                    record.update(status=state if state in {"passed", "failed", "blocked"} else "interrupted",
                                  summary=summary or "运行时重启中断了等待；原执行与成果已保留，可 continue_delegation 恢复",
                                  deliverables=outputs, ended_at=(child or {}).get("ended_at") or time.time())
                    changed = True
                # Old executions delegated before coordinator markers existed.
                parents = {r.get("parent_execution_id") for r in data.values() if isinstance(r, dict)}
                for parent_id in parents - {None, ""}:
                    saved = execution_store.get(parent_id)
                    if saved:
                        pending = sorted(did for did, r in data.items() if isinstance(r, dict)
                                         and r.get("parent_execution_id") == parent_id
                                         and r.get("child_execution_id") and not r.get("verdict"))
                        execution_store.put(parent_id, coordinator=True, pending_delegation_reviews=pending)
                if changed:
                    _persist_delegations()
    except (OSError, ValueError):
        pass


def _persist_delegations():
    """委派档案落盘（每次状态变更后调用；原子写）。"""
    try:
        with _mesh_lock:
            f = _mesh_delegations_file()
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".json." + uuid.uuid4().hex + ".tmp")
            tmp.write_text(json.dumps(_mesh_delegations, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(f)
    except (OSError, ValueError, TypeError):
        pass
# Waiting parents must not consume every thread before descendants can start.
from core.delegation_runtime import DelegationThreads, lend_execution_slot
_MESH_POOL = DelegationThreads()
from core.execution_store import execution_store  # A2A 委派端点：继承父深度/预算/工作区


def _mesh_review_pending(parent_id, delegation_id, pending):
    """Called under _mesh_lock so parallel completions cannot lose obligations."""
    saved = execution_store.get(parent_id)
    if not saved:
        return
    reviews = set(saved.get("pending_delegation_reviews") or [])
    if pending:
        reviews.add(delegation_id)
    else:
        reviews.discard(delegation_id)
    execution_store.put(parent_id, pending_delegation_reviews=sorted(reviews))


# ---- A2A 委派核心逻辑（Flask 内部端点与 MCP provider 共用；无 request 依赖） ----

def _mesh_delegate_impl(child_node_id: str, task_md: str, parent_execution_id: str,
                        shared_resources: list = None) -> dict:
    child_node_id = str(child_node_id or "")
    task_md = str(task_md or "").strip()
    if not child_node_id or not task_md or not parent_execution_id:
        return {"ok": False, "error": "child_node_id / task_md / parent_execution_id 必填"}
    from core.contracts import delegated_task_error
    contract_error = delegated_task_error(task_md)
    if contract_error:
        return {"ok": False, "error": contract_error}
    if registry.get(child_node_id) is None:
        return {"ok": False, "error": f"节点 {child_node_id} 不在种群中"}
    parent = execution_store.get(parent_execution_id)
    if not parent:
        return {"ok": False, "error": "父执行不存在（深度/预算不可继承）"}
    from core.contracts import delegation_restriction
    restriction = delegation_restriction(parent.get("task_md", ""))
    if restriction:
        return {"ok": False, "error": restriction}
    if parent.get("depth", 0) >= config.max_delegation_depth:
        return {"ok": False,
                "error": f"已达最大委派深度 {config.max_delegation_depth}，请自己完成"}
    from core.scheduling import external_resources, resources_conflict
    resources = external_resources({"shared_resources": shared_resources or []})
    delegation_id = uuid.uuid4().hex
    child_execution_id = uuid.uuid4().hex
    with _mesh_lock:
        if sum(r.get('status') == 'running' for r in _mesh_delegations.values()) >= _MESH_PENDING_CAP:
            return {"ok": False, "error": "在途委派已达上限，请先等待已有下属汇报"}
        # 外部资源互斥：与同父进行中的委派声明了相同外部资源 → 拒绝，强制串行
        # （否则两个子执行并行操作同一台设备会互相打架，2026-10-24 实测教训）。
        for other in _mesh_delegations.values():
            if (other.get("status") == "running"
                    and other.get("parent_execution_id") == parent_execution_id
                    and resources_conflict(resources, other.get("shared_resources") or frozenset())):
                clash = sorted(resources & (other.get("shared_resources") or frozenset()))
                return {"ok": False,
                        "error": f"你已有一个进行中的委派占用相同外部资源 {clash}。"
                                 f"外部资源互斥：请 wait_delegation 完成并复核后再发本委派，"
                                 f"或去掉 shared_resources 中的重复声明。"}
        parent_round_id = parent.get("interaction_round_id")
        concurrent = next((r for r in _mesh_delegations.values()
                           if r.get("parent_execution_id") == parent_execution_id
                           and r.get("parent_round_id") == parent_round_id
                           and r.get("batch_id")
                           and r.get("status") == "running"), None)
        batch_id = (concurrent or {}).get("batch_id") or uuid.uuid4().hex
        _mesh_delegations[delegation_id] = {
            "delegation_id": delegation_id, "parent_execution_id": parent_execution_id,
            "parent_node_id": parent.get("node_id"), "child_node_id": child_node_id,
            "task_md": task_md, "status": "running",
            "child_execution_id": child_execution_id, "batch_id": batch_id, "session_name": parent.get("session_name"),
            "parent_round_id": parent_round_id,
            "shared_resources": sorted(resources),
            "created_at": time.time(), "summary": "", "deliverables": [], "verdict": None,
        }
        _mesh_review_pending(parent_execution_id, delegation_id, True)
    bus.emit("a2a.delegate", delegation_id=delegation_id, node=child_node_id,
             parent=parent.get("node_id"), parent_execution_id=parent_execution_id,
             child_execution_id=child_execution_id, batch_id=batch_id, parent_round_id=parent_round_id,
             status="running", summary=task_md, session=parent.get("session_name"))
    _persist_delegations()
    try:
        execution_store.put(parent_execution_id, coordinator=True, phase="coordinating")
        if not parent.get("coordinator"):
            bus.emit("node.brain", node=parent.get("node_id"), execution_id=parent_execution_id,
                     action="coordinating", detail="已转为协调者，业务代码交给下属执行",
                     session=parent.get("session_name"))
        if parent.get("attempt_id"):
            from core.terminal_runtime import terminal_store
            terminal_store.paths(parent_execution_id, parent["attempt_id"]).joinpath("coordinator.flag").touch()
        _MESH_POOL.submit(_mesh_run_child, delegation_id, child_node_id, task_md, parent_execution_id)
    except Exception as exc:
        with _mesh_lock:
            _mesh_delegations[delegation_id].update(status='interrupted', summary=f'委派启动失败: {type(exc).__name__}', ended_at=time.time())
            _mesh_lock.notify_all()
        _persist_delegations()
        bus.emit('a2a.delegate', delegation_id=delegation_id, node=child_node_id,
                 parent=parent.get('node_id'), parent_execution_id=parent_execution_id,
                 child_execution_id=child_execution_id, batch_id=batch_id, parent_round_id=parent_round_id,
                 status='interrupted', summary='委派启动失败，原任务仍由上级保留', session=parent.get('session_name'))
        return {'ok':False,'error':'委派未能启动，原任务仍由上级保留'}
    return {"ok": True, "delegation_id": delegation_id, "child_node_id": child_node_id}


def _mesh_continue_impl(parent_execution_id: str, delegation_id: str, instruction: str) -> dict:
    """A parent adds a new input to its own child's existing execution/session."""
    instruction = str(instruction or "").strip()
    if not parent_execution_id or not delegation_id or not instruction:
        return {"ok": False, "error": "parent_execution_id / delegation_id / instruction 必填"}
    parent = execution_store.get(parent_execution_id)
    if not parent:
        return {"ok": False, "error": "原父执行不存在"}
    from core.node import active_cancel
    from core.scheduling import resources_conflict
    with _mesh_lock:
        record = _mesh_delegations.get(delegation_id)
        if not record or record.get("parent_execution_id") != parent_execution_id:
            return {"ok": False, "error": "委派不属于当前父执行"}
        eid = record.get("child_execution_id")
        child = execution_store.get(eid) if eid else None
        if not child or child.get("parent_execution_id") != parent_execution_id:
            return {"ok": False, "error": "原子执行不存在或上级关系不匹配"}
        if record.get("status") == "running" or active_cancel(eid) is not None:
            return {"ok": False, "error": "原委派仍在执行或排队，请先 wait_delegation"}
        session_id = child.get("session_id") or (child.get("result") or {}).get("session_id")
        if not session_id and (child.get("result") or {}).get("action") != "split_tasks":
            from core.execution_store import preworker_attempts_safe, preworker_resume_evidence
            from core.terminal_runtime import terminal_store
            if not (preworker_resume_evidence(child) and preworker_attempts_safe(terminal_store.list_attempts(execution_id=eid))):
                return {"ok": False, "error": "原执行缺少可恢复的 Hermes 会话或启动前检查点"}
        if child.get("node_id") != record.get("child_node_id") or child.get("workspace") != parent.get("workspace"):
            return {"ok": False, "error": "原节点或工作区关系不匹配"}
        live = [r for r in _mesh_delegations.values() if r.get("status") == "running"]
        if len(live) >= _MESH_PENDING_CAP:
            return {"ok": False, "error": "在途委派已达上限，请先等待下属汇报"}
        resources = frozenset(record.get("shared_resources") or [])
        if any(r.get("parent_execution_id") == parent_execution_id and resources_conflict(resources, frozenset(r.get("shared_resources") or [])) for r in live):
            return {"ok": False, "error": "相同外部资源已有进行中的委派，请先 wait_delegation"}
        round_id = parent.get("interaction_round_id")
        batch_id = next((r["batch_id"] for r in live if r.get("parent_execution_id") == parent_execution_id
                         and r.get("parent_round_id") == round_id and r.get("batch_id")), None) or uuid.uuid4().hex
        record.setdefault("previous_results", []).append({k: record.get(k) for k in ("status", "summary", "verdict", "ended_at", "batch_id", "last_instruction")})
        record.update(status="running", summary="", deliverables=[], verdict=None, ended_at=None,
                      last_instruction=instruction, resume_count=record.get("resume_count", 0)+1,
                      batch_id=batch_id, parent_round_id=round_id, resumed_at=time.time())
        _mesh_review_pending(parent_execution_id, delegation_id, True)
    _persist_delegations()
    bus.emit("a2a.delegate", delegation_id=delegation_id, node=record["child_node_id"], parent=parent.get("node_id"),
             parent_execution_id=parent_execution_id, child_execution_id=eid, batch_id=batch_id,
             parent_round_id=round_id, context_mode="continue", status="running", summary=instruction,
             session=parent.get("session_name"))
    try:
        _MESH_POOL.submit(_mesh_run_child, delegation_id, record["child_node_id"], record["task_md"],
                          parent_execution_id, resume_instruction=instruction)
    except Exception as exc:
        with _mesh_lock:
            record.update(status="interrupted", summary=f"原委派恢复启动失败: {type(exc).__name__}", ended_at=time.time())
            _mesh_lock.notify_all()
        _persist_delegations()
        bus.emit("a2a.delegate", delegation_id=delegation_id, node=record["child_node_id"], parent=parent.get("node_id"),
                 parent_execution_id=parent_execution_id, child_execution_id=eid, batch_id=batch_id,
                 status="interrupted", summary=record["summary"], session=parent.get("session_name"))
        return {"ok": False, "error": "原委派恢复未能启动，已保留原执行和历史结果"}
    return {"ok": True, "delegation_id": delegation_id, "child_execution_id": eid,
            "child_node_id": record["child_node_id"], "session_id": session_id}


def _mesh_wait_impl(delegation_id: str, wait: float) -> dict:
    deadline = time.monotonic() + max(0.0, min(float(wait or 0), 150.0))
    with _mesh_lock:
        record = _mesh_delegations.get(delegation_id)
        if record is None:
            return {"ok": False, "error": "委派不存在"}
        waiting_parent = record.get('parent_execution_id') if record['status']=='running' and time.monotonic()<deadline else None
    try:
        # Reacquire outside the mesh condition: descendants need that condition
        # to publish completion and release their own slots.
        with lend_execution_slot(waiting_parent):
            with _mesh_lock:
                while record["status"] == "running" and time.monotonic() < deadline:
                    _mesh_lock.wait(timeout=min(1.0, max(0.05, deadline - time.monotonic())))
                result = {"ok": True, **{k: record.get(k) for k in
                          ("delegation_id", "child_node_id", "parent_node_id", "status",
                           "summary", "deliverables", "verdict")},
                          "elapsed": int(time.time() - (record.get("resumed_at") or record["created_at"]))}
        return result
    except TimeoutError as exc:
        return {'ok':False,'error':str(exc)}


def _mesh_verdict_impl(delegation_id: str, passed: bool, note: str, score=None) -> dict:
    if not isinstance(passed, bool):
        return {"ok": False, "error": "passed 必须是布尔值"}
    note = str(note or "")[:2000]
    raw = None
    if score is not None:
        try:
            raw = max(-10.0, min(10.0, float(score)))
        except (TypeError, ValueError):
            return {"ok": False, "error": "score 必须是 -10~+10 的数字"}
    with _mesh_lock:
        record = _mesh_delegations.get(delegation_id)
        if record is None:
            return {"ok": False, "error": "委派不存在"}
        if record["status"] == "running":
            return {"ok": False, "error": "委派尚未结束，先 wait_delegation"}
        record["verdict"] = {"passed": passed, "note": note, "score": raw, "at": time.time()}
        child_node_id = record["child_node_id"]
        _mesh_review_pending(record.get("parent_execution_id"), delegation_id, False)
    _persist_delegations()
    # 直系上级的最终打分（覆盖自评预打分）：score(-10~+10)×delta_coef，缺省 ±10
    raw_effective = raw if raw is not None else (10.0 if passed else -10.0)
    try:
        from core.node import NODE
        node = NODE(child_node_id)
        node._score_base = node.reg.get(child_node_id).score
        node._execution_id = f"mesh-verdict-{delegation_id[:12]}"
        node._update_node(passed, node._score_base + raw_effective * config.delta_coef)
    except Exception as exc:
        logging.getLogger(__name__).warning("mesh verdict score update failed: %s", exc)
    bus.emit("a2a.verdict", delegation_id=delegation_id, node=child_node_id,
             parent=record.get("parent_node_id"), passed=passed, detail=note,
             score=raw_effective, session=record.get("session_name") or (execution_store.get(record.get("parent_execution_id")) or {}).get("session_name"))
    return {"ok": True, "child_node_id": child_node_id}


def _mesh_escalate_impl(delegation_id: str, parent_execution_id: str,
                        passed: bool, note: str, score) -> dict:
    """越级纠错：祖父级节点对孙辈委派复核（可扣可加，须附证据）。

    授权链：调用者(parent_execution_id) → 其直系下属(委派的 parent_node_id) → 委派 child。
    校验「调用者确是委派父的直系上级」（执行存档里存在 child=委派父、upper=调用者 的执行），
    防止无关节点乱打分。幅度与其它打分动作一致（score×delta_coef）。"""
    if not isinstance(passed, bool):
        return {"ok": False, "error": "passed 必须是布尔值"}
    note = str(note or "").strip()
    if not note or len(note) < 10:
        return {"ok": False, "error": "越级纠错必须附核验证据（note 至少 10 字：发现了什么、如何验证）"}
    try:
        raw = max(-10.0, min(10.0, float(score)))
    except (TypeError, ValueError):
        return {"ok": False, "error": "score 必须是 -10~+10 的数字（负=扣分，正=纠正加分）"}
    caller = execution_store.get(str(parent_execution_id or ""))
    caller_node = str((caller or {}).get("node_id") or "")
    if not caller_node:
        return {"ok": False, "error": "parent_execution_id 不存在（无法确认调用者身份）"}
    with _mesh_lock:
        record = _mesh_delegations.get(delegation_id)
        if record is None:
            return {"ok": False, "error": "委派不存在"}
        middle_node = str(record.get("parent_node_id") or "")
        child_node_id = record.get("child_node_id")
        if not middle_node or middle_node == caller_node:
            return {"ok": False, "error": "这是你对直系下属的委派，请用 report_delegation_verdict 打分"}
    import sqlite3
    try:
        conn = sqlite3.connect(str(_mesh_state_dir() / "executions.sqlite"), timeout=10)
        try:
            row = conn.execute("""SELECT COUNT(*) FROM executions
                WHERE json_extract(payload,'$.node_id')=?
                  AND json_extract(payload,'$.result.upper')=?""",
                (middle_node, caller_node)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return {"ok": False, "error": f"授权链校验失败: {exc}"}
    if not row or not row[0]:
        return {"ok": False, "error":
                f"越级校验不通过：你（{caller_node}）不是 {middle_node} 的直系上级，"
                f"无权对 {child_node_id} 的委派纠错"}
    # 授权成立：修正孙辈分数（可扣可加），留档
    try:
        from core.node import NODE
        node = NODE(child_node_id)
        node._score_base = node.reg.get(child_node_id).score
        node._execution_id = f"mesh-escalate-{delegation_id[:12]}"
        node._update_node(passed, node._score_base + raw * config.delta_coef)
    except Exception as exc:
        logging.getLogger(__name__).warning("mesh escalate score update failed: %s", exc)
    with _mesh_lock:
        record.setdefault("escalations", []).append(
            {"by": caller_node, "passed": passed, "score": raw, "note": note[:2000],
             "at": time.time()})
    _persist_delegations()
    bus.emit("a2a.escalate", delegation_id=delegation_id, node=child_node_id,
             by=caller_node, middle=middle_node, passed=passed, detail=note,
             score=raw, session=(caller or {}).get("session_name") or record.get("session_name"))
    return {"ok": True, "child_node_id": child_node_id, "applied": raw * config.delta_coef}


def _mesh_state_dir() -> Path:
    return Path(os.environ.get(
        "ADGS_STATE_DIR", str(Path(__file__).resolve().parent / ".adgs_runtime")))


def _mesh_collab_index() -> dict:
    """mesh 委派协作史：(child_node_id, parent_node_id) -> {count, passed, last_ts}。

    数据源是执行存档里 task_id 为 mesh_* 的子执行（result.upper 记录了委派父节点）。
    这是节点间「认识谁、信任谁」的客观依据，不依赖各 worker 的主观记忆。
    """
    import sqlite3
    index = {}
    db = _mesh_state_dir() / "executions.sqlite"
    try:
        conn = sqlite3.connect(str(db), timeout=10)
        try:
            rows = conn.execute("""
                SELECT json_extract(payload,'$.node_id'), json_extract(payload,'$.result.upper'),
                       json_extract(payload,'$.result.evaluation.passed'),
                       json_extract(payload,'$.ended_at')
                FROM executions WHERE json_extract(payload,'$.task_id') LIKE 'mesh_%'
            """).fetchall()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return index
    for child, parent, passed, ended in rows:
        if not child or not parent:
            continue
        cell = index.setdefault((str(child), str(parent)), {"count": 0, "passed": 0, "last_ts": 0.0})
        cell["count"] += 1
        cell["passed"] += 1 if passed else 0
        cell["last_ts"] = max(cell["last_ts"], float(ended or 0))
    return index


def _mesh_explore_boost(n, node_count: int) -> float:
    from core.router import explore_boost
    return round(explore_boost(n, node_count), 4)


def _mesh_directory_impl(query: str = "", top: int = 8, parent_execution_id: str = "") -> list:
    """Use the CEO's fit shortlist; derive missing queries from the parent task.

    Cumulative score and familiarity do not replace task fit. Both raw semantic
    similarity and the policy baseline remain visible to the selecting worker.
    """
    from core.router import ranked_candidates
    nodes = registry.active()
    caller = execution_store.get(str(parent_execution_id)) if parent_execution_id else None
    effective_query = str(query or '').strip() or str((caller or {}).get('task_md') or '')
    ranked = ranked_candidates(effective_query, nodes)
    try:
        limit = max(1, min(int(top or 8), 15))
    except (TypeError, ValueError):
        limit = 8
    ranked = ranked[:limit]
    my_id = str((caller or {}).get("node_id") or "")
    collab = _mesh_collab_index() if my_id else {}
    result = []
    for n, fit in ranked:
        boost = round(fit['explore_boost'],4)
        row = {"id": n.id, "describe": (n.describe or "")[:300], "score": n.score,
               "success_rate": n.success_rate, "tasks_done": n.tasks_done,
               "explore_boost": boost, "explore_recommended": boost >= 1.10,
               "skills": sorted(getattr(n, "skills", {}) or {})}
        row.update(similarity=round(fit['similarity'],4) if fit['similarity'] is not None else None,
                   relevance=round(fit['relevance'],4), routing_score=round(fit['score'],6),
                   generalist=fit['generalist'], embedding_available=fit['embedding_available'], same_role=n.id==my_id)
        if my_id:
            cell = collab.get((n.id, my_id)) or {}
            row["collab_with_me"] = {"count": cell.get("count", 0), "passed": cell.get("passed", 0),
                                     "last_ts": cell.get("last_ts", 0.0)}
        result.append(row)
    return result


def _mesh_probe_impl(node_id: str, parent_execution_id: str = "") -> dict:
    """节点深度档案（A2A probe_node 数据源）：描述全文 + 技能包 + 近期战绩 + 忙闲 + 协作史。

    技能包现读该节点 hermes profile 的 skills 目录（最真实的能力证据，比描述宣称可靠）；
    近期执行来自执行存档；协作史给双向记录（我委派给它 / 它委派给我）。"""
    from core.hermes import HermesInstance
    node_id = str(node_id or "").strip()
    node = registry.get(node_id)
    if node is None:
        return {"ok": False, "error": f"节点 {node_id} 不在种群中（先 list_nodes 查目录）"}
    if getattr(node, "status", "active") != "active":
        return {"ok": False, "error": f"节点 {node_id} 已被淘汰（status={node.status}），请选活跃节点"}
    try:
        skills = {name: str(desc)[:80] for name, desc in
                  HermesInstance.list_skills(node_id).items()}
    except Exception:
        skills = dict(getattr(node, "skills", {}) or {})
    recent, running_now = [], 0
    import sqlite3
    import time as _time
    try:
        conn = sqlite3.connect(str(_mesh_state_dir() / "executions.sqlite"), timeout=10)
        try:
            rows = conn.execute("""
                SELECT json_extract(payload,'$.task_id'), json_extract(payload,'$.state'),
                       json_extract(payload,'$.result.evaluation.detail'),
                       json_extract(payload,'$.ended_at'), json_extract(payload,'$.updated_at'),
                       json_extract(payload,'$.execution_id')
                FROM executions WHERE json_extract(payload,'$.node_id')=?
                ORDER BY json_extract(payload,'$.updated_at') DESC LIMIT 12
            """, (node_id,)).fetchall()
        finally:
            conn.close()
        from core.node import active_cancel
        for task_id, state, detail, ended, updated, execution_id in rows:
            if state == "running":
                # runtime 重启可能留下僵尸 running：只有近期仍有活动的才算真忙
                if active_cancel(execution_id) is not None:
                    running_now += 1
                continue
            if len(recent) < 5:
                recent.append({"task_id": task_id, "state": state,
                               "detail": str(detail or "")[:120]})
    except (OSError, sqlite3.Error):
        pass
    caller = execution_store.get(str(parent_execution_id)) if parent_execution_id else None
    my_id = str((caller or {}).get("node_id") or "")
    collab = None
    if my_id:
        idx = _mesh_collab_index()

        def _cell(key):
            return idx.get(key) or {"count": 0, "passed": 0, "last_ts": 0.0}

        collab = {"i_delegated_to": _cell((node_id, my_id)),
                  "delegated_to_me": _cell((my_id, node_id))}
    return {"ok": True,
            "node": {"id": node.id, "describe": (node.describe or "")[:500],
                     "score": node.score, "success_rate": node.success_rate,
                     "tasks_done": node.tasks_done,
                     "explore_boost": _mesh_explore_boost(node, len(registry.active())),
                     "skills": dict(sorted(skills.items()))},
            "recent": recent, "running_now": running_now, "collab": collab}


# ---------------- 后端事件控制台输出（2026-10-23 用户需求：终端不要黑屏） ----------------
#
# runtime 进程把全部事件写入共享 events.sqlite；壳/单进程开一个轮询线程读新行，
# 按事件类型打印人类可读摘要（内容一律截前 20 字），让「谁派给谁、谁对谁说了什么」
# 直接在启动窗口可见。terminal.output/hermes.line 等海量噪音事件不打印。

from web.console_log import _CONSOLE_EVENT_TYPES, _short, _format_event, run_console


def _console_event_printer(poll_interval: float = 0.6):
    """Display shared runtime events without owning the task process."""
    run_console(poll_interval=poll_interval, tail=0)


def _mesh_provider():
    """A2A MCP server 的数据访问（同进程直调，事件天然进入本进程事件总线）。"""
    return {"directory": _mesh_directory_impl,
            "probe": _mesh_probe_impl,
            "delegate": _mesh_delegate_impl,
            "continue_delegation": _mesh_continue_impl,
            "wait": _mesh_wait_impl,
            "verdict": _mesh_verdict_impl,
            "escalate": _mesh_escalate_impl}

_mesh_starting = False
_mesh_status = {"ready": False, "error": "A2A 服务尚未启动"}


def _start_mesh_mcp():
    """在 runtime 进程内嵌 A2A MCP server（streamable-http,uvicorn 线程）。

    端口偏好 8022，被占则向后扫描；url/token 落 mesh_mcp.json 供
    hermes 插件刷新（_profile_ready → _mesh_plugin_current 校验一致性）。
    幂等：runtime 进程生命周期内只起一次。
    """
    global _mesh_starting, _mesh_status
    if _mesh_starting:
        return
    _mesh_starting = True
    server = None
    listener = None
    thread = None
    state_file = Path(os.environ.get("ADGS_STATE_DIR", str(ROOT / ".adgs_runtime"))) / "mesh_mcp.json"
    try:
        # A previous registration must not satisfy a new host's readiness check.
        previous_token = None
        if state_file.is_file():
            try:
                previous_token = json.loads(state_file.read_text(encoding="utf-8")).get("token")
            except (ValueError, AttributeError):
                pass
            state_file.unlink()
        _load_delegations()
        import socket
        import uvicorn
        from tools.adgs_mcp_server import build_mesh_asgi, configure_provider, write_mesh_state
        configure_provider(**_mesh_provider())
        app, token = build_mesh_asgi(token=previous_token)
        for port in (8022, 18022, 18023, 18024, 18025):
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                listener.bind(("127.0.0.1", port))
            except OSError:
                listener.close()
                listener = None
                continue
            server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                                   log_level="warning"))
            thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]},
                                      daemon=True, name="adgs-mesh-mcp")
            thread.start()
            deadline = time.monotonic() + 5
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.05)
            if not server.started or not thread.is_alive():
                raise RuntimeError("A2A 服务未能开始监听")
            write_mesh_state(f"http://127.0.0.1:{port}/mcp", token)
            _mesh_status = {"ready": True, "port": port}
            try:
                bus.emit("system", msg=f"A2A 种群协作入口就绪（:{port}）")
            except Exception:
                logging.getLogger(__name__).exception("无法记录 A2A 启动事件")
            return
        raise RuntimeError("A2A 服务端口全部被占用")
    except Exception as exc:
        if server is not None:
            server.should_exit = True
        if thread is not None and thread.is_alive():
            thread.join(timeout=1)
        if listener is not None:
            listener.close()
        _mesh_status = {"ready": False, "error": f"{type(exc).__name__}: {exc}"}
        logging.getLogger(__name__).exception("mesh MCP 启动失败")
        _mesh_starting = False


def _mesh_summary_of(result):
    """从 NODE.work 结果提取给父 worker 的汇报要素。"""
    evaluation = result.get("evaluation") or {}
    summary = (result.get("summary") or evaluation.get("detail")
               or result.get("work_log") or result.get("error") or "")
    summary = str(summary)[-4000:]
    deliverables = evaluation.get("deliverables") or []
    return summary, [str(d) for d in deliverables][:20]


def _mesh_run_child(delegation_id, child_node_id, task_md, parent_execution_id, *, resume_instruction=None):
    """线程体：在子节点上执行委派任务，结束写回注册表并唤醒等待者。"""
    record = _mesh_delegations.get(delegation_id, {})
    parent = {}
    try:
        parent = execution_store.get(parent_execution_id) or {}
        parent_depth = int(parent.get("depth", 0))
        from core.node import NODE, active_cancel
        with session_scope(parent.get("session_name"), parent.get("workspace")), event_scope(session=parent.get("session_name"), batch_id=record.get("batch_id")):
            node = NODE(child_node_id)
            if resume_instruction is not None:
                child = execution_store.get(record["child_execution_id"]) or {}
                result = node.continue_execution(record["child_execution_id"], resume_instruction,
                    task_id=child.get("task_id"), run_id=child.get("run_id"), parent_cancel=active_cancel(parent_execution_id))
            else:
                result = node.work(
                    task_md, upper=record.get("parent_node_id", "mesh"),
                    timeout=None, depth=parent_depth + 1,
                    task_id=f"mesh_{delegation_id[:8]}", execution_id=record.get("child_execution_id"),
                    run_id=parent.get("run_id"),
                    parent_cancel=active_cancel(parent_execution_id),
                    workspace=parent.get("workspace"), parent_execution_id=parent_execution_id)
        status = "passed" if result.get("success") and result.get("outcome") == "passed" else \
                 result.get("outcome") if result.get("outcome") in ("blocked", "failed") else "interrupted"
        summary, deliverables = _mesh_summary_of(result)
    except Exception as exc:
        status, summary, deliverables = "interrupted", f"委派执行异常: {type(exc).__name__}: {exc}"[:2000], []
    with _mesh_lock:
        record.update({"status": status, "summary": summary, "deliverables": deliverables,
                       "ended_at": time.time()})
        _mesh_delegations[delegation_id] = record
        # Publish this result before a waiter can begin a new continuation of
        # the same record; its batch and status belong to this attempt.
        bus.emit("a2a.delegate", delegation_id=delegation_id, node=record.get("child_node_id"),
                 parent=record.get("parent_node_id"), parent_execution_id=parent_execution_id,
                 child_execution_id=record.get("child_execution_id"), batch_id=record.get("batch_id"),
                 status=status, summary=summary, session=parent.get("session_name"))
        _mesh_lock.notify_all()
    _persist_delegations()


@app.post("/internal/mesh/delegations")
def internal_mesh_delegate():
    if not _mesh_auth():
        return jsonify({"ok": False, "error": "协作令牌无效"}), 401
    data = request.get_json(force=True, silent=True) or {}
    body = _mesh_delegate_impl(data.get("child_node_id"), data.get("task_md"),
                               data.get("parent_execution_id"),
                               data.get("shared_resources") or [])
    return jsonify(body), 200 if body.get("ok") else (409 if "深度" in body.get("error", "") else 404)


@app.get("/internal/mesh/delegations/<delegation_id>")
def internal_mesh_wait(delegation_id):
    if not _mesh_auth():
        return jsonify({"ok": False, "error": "协作令牌无效"}), 401
    try:
        wait = min(float(request.args.get("wait", 0)), 150.0)
    except ValueError:
        wait = 0.0
    body = _mesh_wait_impl(delegation_id, wait)
    return jsonify(body), 200 if body.get("ok") else 404


@app.post("/internal/mesh/delegations/<delegation_id>/continue")
def internal_mesh_continue(delegation_id):
    if not _mesh_auth():
        return jsonify(ok=False, error="协作令牌无效"), 401
    data = request.get_json(force=True, silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(ok=False, error="请求体必须是 JSON 对象"), 400
    result = _mesh_continue_impl(data.get("parent_execution_id"), delegation_id, data.get("instruction"))
    return jsonify(result), 200 if result.get("ok") else 409


@app.post("/internal/mesh/delegations/<delegation_id>/verdict")
def internal_mesh_verdict(delegation_id):
    if not _mesh_auth():
        return jsonify({"ok": False, "error": "协作令牌无效"}), 401
    data = request.get_json(force=True, silent=True) or {}
    body = _mesh_verdict_impl(delegation_id, data.get("passed"), data.get("note"),
                              data.get("score"))
    return jsonify(body), 200 if body.get("ok") else (409 if "尚未结束" in body.get("error", "") else 404)


@app.post("/internal/mesh/delegations/<delegation_id>/escalate")
def internal_mesh_escalate(delegation_id):
    """越级纠错（祖父级对孙辈委派复核；须附证据；可扣可加）"""
    if not _mesh_auth():
        return jsonify({"ok": False, "error": "协作令牌无效"}), 401
    data = request.get_json(force=True, silent=True) or {}
    body = _mesh_escalate_impl(delegation_id, data.get("parent_execution_id"),
                               data.get("passed"), data.get("note"), data.get("score"))
    return jsonify(body), 200 if body.get("ok") else (403 if "越级校验" in body.get("error", "") else 404)


@app.get("/internal/mesh/directory")
def internal_mesh_directory():
    if not _mesh_auth():
        return jsonify({"ok": False, "error": "协作令牌无效"}), 401
    return jsonify({"ok": True, "nodes": _mesh_directory_impl()})


@app.get("/api/mesh/delegations")
def api_mesh_delegations():
    """A2A 协作档案：mesh 委派记录（含 verdict/escalations/shared_resources）。

    ?node=ags_N 过滤出与该节点相关的记录（作为委派父或被委派子）；
    不传则返回全部（按创建时间倒序，limit 截断）。"""
    node = request.args.get("node", "").strip() or None
    try:
        limit = max(1, min(int(request.args.get("limit", 200)), 1000))
    except ValueError:
        limit = 200
    with _mesh_lock:
        records = [dict(r) for r in _mesh_delegations.values()]
    records.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
    if node:
        records = [r for r in records
                   if r.get("parent_node_id") == node or r.get("child_node_id") == node]
    # 脱敏：task_md 很长，只回摘要
    for r in records:
        if r.get("task_md"):
            r["task_md"] = r["task_md"][:300]
    return jsonify({"ok": True, "delegations": records[:limit], "total": len(records)})


def _catalog_refresh():
    project_catalog.discover(config.DATA_DIR / "sessions", config.workspace)


@app.get("/api/projects")
def api_projects():
    _catalog_refresh()
    projects = project_catalog.listing(include_archived=request.args.get("include_archived") == "1")
    running = _running_sessions.copy()
    for project in projects:
        for chat in project["sessions"]:
            chat["busy"] = chat["id"] in running
    return jsonify(ok=True, projects=projects)


@app.post("/api/projects")
def api_project_create():
    data = request.get_json(force=True, silent=True) or {}
    try:
        return jsonify(ok=True, project=project_catalog.create_project(data.get("name"), data.get("workspace")))
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.patch("/api/projects/<project_id>")
def api_project_update(project_id):
    data = request.get_json() or {}
    try:
        with _task_lock:
            if data.get("archived") is True:
                for session_id in _running_sessions:
                    session = project_catalog.session(session_id)
                    if session and session["project_id"] == project_id:
                        return jsonify(ok=False, error="项目中有正在工作的会话，请等待它结束再归档"), 409
            item = project_catalog.update_project(project_id, name=data.get("name"), archived=data.get("archived"))
        return jsonify(ok=True, project=item)
    except (ValueError, KeyError) as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.post("/api/projects/<project_id>/sessions")
def api_project_session_create(project_id):
    item = None
    try:
        data = request.get_json(force=True, silent=True) or {}
        item = project_catalog.create_session(project_id, data.get("title", "新会话"))
        from core.ceo import atomic_write_session
        atomic_write_session(config.DATA_DIR / "sessions" / (item["id"] + ".json"),
                             {"name": item["id"], "history": [], "workflow_log": [], "graph": []})
        return jsonify(ok=True, session=item)
    except (ValueError, KeyError, OSError) as exc:
        if item is not None:
            project_catalog.discard_new_session(item["id"])
        return jsonify(ok=False, error=str(exc)), 400


@app.patch("/api/sessions/<session_id>")
def api_project_session_update(session_id):
    data = request.get_json(force=True, silent=True) or {}
    try:
        with _task_lock:
            if data.get("archived") is True and _session_busy(session_id):
                return jsonify(ok=False, error="运行中的会话暂不能归档"), 409
            item = project_catalog.update_session(session_id, data.get("title"), data.get("archived"))
        return jsonify(ok=True, session=item)
    except (ValueError, KeyError) as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.post("/api/queue/<item_id>/guide")
def api_queue_guide(item_id):
    with _task_lock:
        if worker_launch_on_hold():
            return jsonify(ok=False, error=WORKER_HOLD_REASON), 409
        with _queue_lock:
            item = next((t for t in _queue if isinstance(t, dict) and t.get("id") == item_id), None)
            if item is None:
                return jsonify(ok=False, error="消息已被处理或撤销"), 404
            item["mode"] = "interrupt"
            _checkpoint_queue_locked()
            session = item.get("session")
            start_now = not _session_busy(session)
            if start_now:
                _queue.remove(item)
        if start_now:
            result = _start_task(item["text"], session, item.get("workspace"), mode="interrupt")
            if not result["ok"]:
                with _queue_lock:
                    _queue.insert(0, item)
                return jsonify(result), 503
    _emit_queue_notifications({"type": "queue.update", "data": {"queue": _queue_snapshot()}}, session=session)
    return jsonify(ok=True, mode="interrupt", started=start_now)


@app.get("/api/queue")
def api_queue_get():
    """当前待执行队列（任务执行中提交的消息会排队）"""
    return jsonify({"ok": True, "queue": _queue_snapshot(request.args.get("session"))})


@app.put("/api/queue/<item_id>")
def api_queue_update(item_id):
    """编辑一条排队消息的文本（仅队列中可改，已开始执行的任务不可撤回）"""
    data = request.get_json(force=True, silent=True) or {}
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        return jsonify({"ok": False, "error": "text 必须是非空字符串"}), 400
    text = text.strip()
    with _queue_lock:
        for t in _queue:
            if isinstance(t, dict) and t.get("id") == item_id:
                old = t["text"]
                t["text"] = text
                _checkpoint_queue_locked()
                break
        else:
            return jsonify({"ok": False,
                            "error": "排队消息不存在或已开始执行"}), 404
    _emit_queue_notifications(
        {"type": "system", "data": {"msg": f"排队消息已修改：{(old[:40] + '…') if len(old) > 40 else old} → "
                                           f"{(text[:40] + '…') if len(text) > 40 else text}"}},
        {"type": "queue.update", "data": {"queue": _queue_snapshot()}}, session=t.get("session"))
    return jsonify({"ok": True})


@app.delete("/api/queue/<item_id>")
def api_queue_remove(item_id):
    """撤销一条排队消息（不执行、不产生 CEO 消息）"""
    with _queue_lock:
        for i, t in enumerate(_queue):
            if isinstance(t, dict) and t.get("id") == item_id:
                removed = _queue.pop(i)
                break
        else:
            return jsonify({"ok": False,
                            "error": "排队消息不存在或已开始执行"}), 404
    text = removed.get("text", "")
    _emit_queue_notifications(
        {"type": "system", "data": {"msg": f"已撤销排队消息：{(text[:50] + '…') if len(text) > 50 else text}"}},
        {"type": "queue.update", "data": {"queue": _queue_snapshot()}}, session=removed.get("session"))
    return jsonify({"ok": True})


# 当前任务的会话名（节点级自动保存：每完成一个节点覆盖保存一次，
# 保存点 = 最后完成节点之后的 CEO 上下文；恢复时正在执行的节点自然不在其中）
_current_sess = {"name": None}


def _save_current_session(ceo, name: str, quiet: bool = False):
    """保存会话上下文到指定会话（覆盖同名文件）

    talk 运行中保存时用任务自己锁定的会话（用户可能已切换查看其它历史
    会话，_current_sess["name"] 已变——任务数据必须落到它自己的会话）。
    """
    try:
        sess_name = name or getattr(ceo, "_talk_session", None)
        hist = getattr(ceo, "_histories", {}).get(sess_name) or []
        if not hist or len(hist) < 2:
            return
        ceo.save_conversation(sess_name)
        if not quiet:
            bus.emit("system", msg=f"会话已保存到节点完成点（{len(hist)} 条消息）")
    except Exception as e:
        if not quiet:
            bus.emit("system", msg=f"自动保存会话失败（忽略）：{str(e)[:120]}")


def _session_save_listener():
    """监听 CEO 每次回复 / 节点完成 → 覆盖保存当前任务会话

    2026-08-29 规则：每次 CEO 触发回复（工具决策/提问/汇报/完成）后
    自动保存一次 —— 中断后点进该会话发"继续"，CEO 从保存点接续执行
    不丢信息（history 每轮 append 后事件才发出，保存即含最新决策）。
    """
    q = bus.subscribe()
    while True:
        try:
            ev = q.get(timeout=5)
        except queue.Empty:
            continue
        t = ev.get("type")
        event_session = ev.get("session")
        if not event_session:
            continue
        # CEO 回复类事件 + 节点完成事件（节点完成也属任务进展）
        if t in ("chat.ceo_tool", "chat.ceo_ask", "chat.ceo_report",
                 "chat.ceo_finish", "chat.ceo_end", "chat.ceo_activity") \
                or (t == "node.status"
                    and ev["data"].get("status") in ("done", "failed")):
            try:
                _save_current_session(_ceo_for_session(event_session), event_session, quiet=True)
            except Exception:
                pass


def _submit_chat_message(text, session, mode):
    return _start_task(text, session, mode=mode, allow_recovery_chat=True)


def _start_task(text: str, sess_name=None, workspace=None, mode="queue", *, allow_recovery_chat=False,
                recovery_execution=None):
    """Atomically accept/queue a run; queue entries carry their own immutable context."""
    import datetime
    with _task_lock:
        held = worker_launch_on_hold()
        protected_resume = held and recovery_grant(recovery_execution)
        recovery_chat = held and not protected_resume
        if recovery_chat and (not allow_recovery_chat or mode != "queue"):
            return {"ok": False, "error": WORKER_HOLD_REASON, "http_status": 409}
        session = sess_name or _current_sess["name"]
        metadata = project_catalog.session(session) if session else None
        if metadata and (metadata.get("archived") or metadata.get("project_archived")):
            return {"ok": False, "error": "请先恢复已归档的项目或会话", "http_status": 409}
        ws = workspace or (metadata["workspace"] if metadata else config.workspace)
        occupied = session in _running_sessions or (_busy["state"] and not _running_sessions)
        if occupied:
            if recovery_chat or protected_resume:
                return {"ok": False, "error": "此会话正在处理消息，请稍后再发送", "http_status": 409}
            active_session = session if session in _running_sessions else get_task_session()
            if mode == "interrupt":
                if active_session and session and session != active_session:
                    return {"ok": False, "error": "打断引导只能发送给当前正在执行的会话", "http_status": 409}
                session = active_session or session
                if not session:
                    return {"ok": False, "error": "无法确认当前执行会话", "http_status": 409}
            with _queue_lock:
                item = {"id": uuid.uuid4().hex[:12], "text": text, "session": session, "mode": mode,
                        "workspace": str(ws), "queued_at": time.time()}
                _queue.append(item)
                position = len(_queue)
            try:
                # 实时推送队列快照（前端面板不依赖 8s 轮询）；纯可观测性，
                # 失败不得影响已完成的入队接受。
                with event_scope(session=session):
                    bus.emit("queue.update", queue=_queue_snapshot())
                    bus.emit("system", msg=f"任务已入队（第 {position} 位）")
            except Exception:
                # Acceptance already happened. Never report failure and invite
                # a duplicate POST just because its notification could not save.
                logging.getLogger(__name__).exception("Queued task notification failed")
            return {"ok": True, "queued": True, "position": position, "mode": mode, 'queue_id':item['id'], 'session':session}
        _busy["state"] = True
        if not session:
            session = f"auto-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
            _current_sess["name"] = session
        _running_sessions[session] = {"workspace": str(ws), "started_at": time.time()}
        if protected_resume:
            _running_sessions[session]["recovery_execution"] = recovery_execution
        stop_event = threading.Event()
        _run_stop_events[session] = stop_event
        if metadata:
            project_catalog.touch(session, text)
    def runner_body():
        ceo = None
        session_prepared = False
        run_failed = False
        try:
            if recovery_chat:
                ceo = get_ceo()
                ceo.activate_session(session)
                ceo.talk_in_recovery(text)
                return
            # Wait for preparation, but never hold the evolution mutex throughout a task.
            if not _evolve_lock.acquire(timeout=config.worker_timeout):
                raise TimeoutError("等待种群准备时任务预算耗尽")
            try:
                if registry.count() == 0:
                    from core.evolution import _default_describe
                    registry.ensure_initial(_default_describe())
                if registry.count() == 0:
                    raise RuntimeError("种群准备失败：没有配置完整的 AGS")
            finally:
                _evolve_lock.release()
            ceo = get_ceo()
            ceo.activate_session(session)
            if not protected_resume:
                ceo._guidance_pending = lambda: _has_guidance(session)
                ceo._guidance_source = lambda receive: _deliver_guidance(session, receive)
            ceo._stop_requested = stop_event.is_set
            session_prepared = True
            if protected_resume:
                ceo.talk(text, recovery_execution=recovery_execution)
            else:
                ceo.talk(text)
        except Exception as exc:
            run_failed = True
            try:
                if recovery_chat:
                    logging.getLogger(__name__).error("Recovery conversation failed (%s)", type(exc).__name__)
                    bus.emit("chat.recovery_reply", text="事故恢复对话出错，请刷新会话确认消息是否已保存。")
                elif ceo is not None and session_prepared:
                    # Preserve progress; the next call repairs incomplete tool exchanges.
                    ceo.mark_interrupted(session, f"CEO 执行异常（{type(exc).__name__}）：{exc}")
                else:
                    bus.emit("chat.ceo_end", status="exception",
                             reason=f"会话启动失败（{type(exc).__name__}）：{exc}"[:600])
            except Exception:
                logging.getLogger(__name__).exception("Failed to record task interruption")
        finally:
            try:
                if ceo is not None and session_prepared:
                    _save_current_session(ceo, session, quiet=True)
                bus.emit("system", msg="当前运行已结束")
            except Exception:
                run_failed = True
                logging.getLogger(__name__).exception("Task finalization failed; preserving pending queue")
            if ceo is not None:
                ceo._guidance_pending = None
                ceo._guidance_source = None
                ceo._stop_requested = None
            next_item = None
            with _task_lock:
                # 引导优先在最后交接窗口接续；普通队列只在任务明确结束后处理。
                # 等待用户回答、异常或仍有未完成义务时保留普通消息。
                # 队列提取与 busy 切换保持在同一任务锁内，避免双 runner。
                _running_sessions.pop(session, None)
                _run_stop_events.pop(session, None)
                _busy["state"] = bool(_running_sessions)
                states = getattr(ceo, "_states", {}) if ceo is not None else {}
                awaiting_input = bool(states.get(session, {}).get("_awaiting_input"))
                with _queue_lock:
                    if not held and not run_failed and not stop_event.is_set() and _queue:
                        # A guidance message arriving in the final handoff window remains
                        # higher priority than ordinary queued tasks, including ask_user.
                        index = next((i for i, t in enumerate(_queue) if isinstance(t, dict)
                                      and t.get("mode") == "interrupt" and t.get("session") == session), None)
                        finished = bool(states.get(session, {}).get("_task_done", True))
                        if index is not None:
                            next_item = _queue.pop(index)
                        elif not awaiting_input and finished:
                            available = next((i for i, t in enumerate(_queue)
                                              if not isinstance(t, dict) or t.get("session") == session), None)
                            if available is not None:
                                next_item = _queue.pop(available)
                # 出队（开始接续）与空闲恢复都实时推送一次队列快照；
                # emit 失败绝不能跳过 _busy 清理（否则 runner 卡死 busy）。
                try:
                    bus.emit("queue.update", queue=_queue_snapshot())
                except Exception:
                    logging.getLogger(__name__).exception("Queue update notification failed")
                _busy["state"] = bool(_running_sessions)
                if next_item is not None:
                    item = next_item if isinstance(next_item, dict) else {"text": next_item}
                    result = _start_task(item["text"], item.get("session"), item.get("workspace"))
                    if not result["ok"]:
                        with _queue_lock:
                            _queue.insert(0, next_item)
    def runner():
        with session_scope(session, ws), event_scope(session=session):
            runner_body()
    with _task_lock:
        try:
            if not recovery_chat:
                with event_scope(session=session):
                    bus.emit("system", msg="任务已提交，CEO 开始处理…")
            threading.Thread(target=runner, daemon=True, name=f"adgs-run-{session}").start()
        except Exception as exc:
            _running_sessions.pop(session, None)
            _run_stop_events.pop(session, None)
            _busy["state"] = bool(_running_sessions)
            logging.getLogger(__name__).exception("Task launch failed")
            return {"ok": False, "queued": False, "session": session,
                    "error": f"任务启动失败，可重试：{type(exc).__name__}: {exc}"[:300]}
    result = {"ok": True, "queued": False, "session": session}
    if recovery_chat:
        result["recovery_chat"] = True
    return result


WEB_HELP = """可用指令（与 CLI 一致）：
  /workspace <path>   切换工作区（如 /workspace D:\\my_project）
  /evolve             触发一轮繁衍+淘汰
  /queue              查看任务队列（任务执行中发消息自动入队；/queue clear 清空）
  /status             查看当前状态（工作区/种群/模型/忙碌）
  /debug <0-3>        调试级别：0=关闭 1=工具概要 2=+耗时 3=+完整模型回复
  /save [name]        保存当前对话（默认名 auto）
  /load <name>        恢复指定对话
  /sessions           列出所有已保存会话
  /history            查看当前对话历史
  /help               显示本帮助
其他输入直接作为任务描述发给 CEO（任务执行中发送会自动排队，当前任务结束后自动执行）。"""


def _session_request(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        data = request.get_json(force=True, silent=True) or {}
        session = data.get("session")
        if session is not None and not _sanitize_name(session, _SESS_NAME_RE):
            return jsonify(ok=False,error="会话名非法"),400
        metadata = project_catalog.session(session) if session else None
        with session_scope(session, metadata["workspace"] if metadata else None):
            return fn(*args, **kwargs)
    return wrapped


@app.post("/api/command")
@_session_request
def api_command():
    """系统指令（/workspace /evolve /save ...）—— 本地执行，不走 CEO"""
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text.startswith("/"):
        return jsonify({"ok": False, "error": "非指令消息"})
    parts = text.split()
    cmd, cmd_args = parts[0].lower(), parts[1:]

    try:
        if cmd == "/workspace":
            if not cmd_args:
                msg = f"当前工作区: {config.workspace}"
            else:
                with _task_lock:
                    if _busy["state"]:
                        return jsonify({"ok": False, "error": "任务执行中，暂不能切换工作区"}), 409
                    ws = config.set_workspace(" ".join(cmd_args))
                    msg = f"已切换工作区: {ws}"
        elif cmd == "/evolve":
            if _busy["state"]:
                return jsonify({"ok": False, "error": "任务执行中，暂不能进化"})
            from core.evolution import evolve
            with _evolve_lock:
                with _task_lock:
                    if _busy["state"]:
                        return jsonify({"ok": False, "error": "任务执行中，暂不能进化"}), 409
                report = evolve()
            msg = (f"繁衍 {len(report['births'])} 淘汰 "
                   f"{len(report['eliminations'])}，种群 "
                   f"{report['n_before']}→{report['n_after']}")
        elif cmd == "/queue":
            # 查看队列状态 / 清空队列（/queue 或 /queue clear）
            with _queue_lock:
                q = list(_queue)
            if cmd_args and cmd_args[0].lower() in ("clear", "清空"):
                with _queue_lock:
                    _queue.clear()
                msg = f"已清空队列（原 {len(q)} 条）"
            elif not q:
                msg = "队列为空（任务执行中发消息会自动入队）"
            else:
                lines = [f"队列 {len(q)} 条待执行："]
                for i, t in enumerate(q, 1):
                    task_text = t.get("text", "") if isinstance(t, dict) else t
                    lines.append(f"  {i}. {task_text[:60]}{'…' if len(task_text) > 60 else ''}")
                msg = "\n".join(lines)
        elif cmd == "/status":
            msg = (f"工作区: {config.workspace} | "
                   f"种群: {registry.count()}/{config.capacity} | "
                   f"CEO: {config.ceo_model} | "
                   f"忙碌: {'是' if _busy['state'] else '否'}")
        elif cmd == "/debug":
            from utils.chat import get_debug_level, set_debug_level
            if not cmd_args:
                msg = f"当前调试级别: {get_debug_level()}（0-3）"
            else:
                try:
                    lv = int(cmd_args[0])
                    set_debug_level(max(0, min(3, lv)))
                    msg = f"调试级别已设为 {get_debug_level()}"
                except ValueError:
                    msg = "用法: /debug <0-3>"
        elif cmd == "/save":
            name = _sanitize_name(cmd_args[0] if cmd_args else (_current_sess["name"] or "auto"), _SESS_NAME_RE)
            if not name:
                return jsonify({"ok": False, "error": "会话名非法"}), 400
            msg = get_ceo().save_as(name)
        elif cmd == "/load":
            if _busy["state"]:
                return jsonify({"ok": False, "error": "任务执行中，暂不能切换会话"})
            if not cmd_args:
                msg = "用法: /load <会话名>（/sessions 查看已有）"
            else:
                name = _sanitize_name(cmd_args[0], _SESS_NAME_RE)
                if not name:
                    return jsonify({"ok": False, "error": "会话名非法"}), 400
                msg = get_ceo().load_conversation(name)
                _current_sess["name"] = name
        elif cmd == "/sessions":
            msg = get_ceo().list_sessions()
        elif cmd == "/history":
            msg = get_ceo().show_history()
        elif cmd == "/help":
            msg = WEB_HELP
        else:
            return jsonify({"ok": False, "error": f"未知指令: {cmd}（/help 查看所有指令）"})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"})

    bus.emit("system", msg=msg)
    return jsonify({"ok": True, "msg": msg})


@app.post("/api/evolve")
def api_evolve():
    """手动触发一轮进化（前端「进化一轮」按钮）"""
    if _busy["state"]:
        return jsonify({"ok": False, "error": "任务执行中，暂不能进化"})
    try:
        from core.evolution import evolve
        with _evolve_lock:
            with _task_lock:
                if _busy["state"]:
                    return jsonify({"ok": False, "error": "任务执行中，暂不能进化"}), 409
            report = evolve()
        return jsonify({"ok": True,
                        "msg": f"繁衍 {len(report['births'])} 淘汰 "
                               f"{len(report['eliminations'])}，种群 "
                               f"{report['n_before']}→{report['n_after']}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:300]})


@app.get("/api/sessions/graphs")
def api_sessions_graphs():
    """所有历史会话的图谱（任务总览用）：每个会话的完整拓扑"""
    sess_dir = config.DATA_DIR / "sessions"
    items = []
    if sess_dir.exists():
        files = sorted(sess_dir.glob("*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for f in files:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                graph = d.get("graph") or []
                preview = ""
                for m in (d.get("history") or []):
                    if m.get("role") == "user" and m.get("content"):
                        c = str(m["content"]).strip()
                        # 2026-08-30：跳过压缩摘要/系统注入消息（compress 把
                        # 摘要写成 role=user 的"[系统提示]..."，会混进历史预览）
                        if c.startswith("[系统提示]") or c.startswith("["):
                            continue
                        preview = c[:60]
                        break
                # 空图谱任务也返回（前端用 CEO 节点占位，2026-08-29）
                items.append({
                    "name": d.get("name", f.stem),
                    "saved_at": d.get("saved_at", ""),
                    "preview": preview,
                    "graph": graph,
                })
            except Exception:
                pass
    return jsonify({"ok": True, "sessions": items})


@app.get("/api/sessions")
def api_sessions():
    """历史会话列表（GPT 式侧栏）：data/sessions/*.json，按保存时间倒序"""
    sess_dir = config.DATA_DIR / "sessions"
    items = []
    if sess_dir.exists():
        files = sorted(sess_dir.glob("*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for f in files[:60]:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                h = d.get("history") or []
                preview = ""
                for m in h:
                    if m.get("role") == "user" and m.get("content"):
                        c = str(m["content"]).strip()
                        # 2026-08-30：跳过压缩摘要/系统注入消息
                        if c.startswith("[系统提示]") or c.startswith("["):
                            continue
                        preview = c[:60]
                        break
                items.append({
                    "name": d.get("name", f.stem),
                    "saved_at": d.get("saved_at", ""),
                    "msg_count": len(h),
                    "preview": preview,
                    "workspace": session_workspace(d),
                })
            except Exception:
                items.append({"name": f.stem, "saved_at": "?",
                              "msg_count": 0, "preview": "（损坏）"})
    return jsonify({"ok": True, "sessions": items})


def _safe_json(s):
    try:
        return json.loads(s)
    except Exception:
        return {"raw": str(s)[:300]}


def _session_messages(history):
    """会话 history → 前端聊天渲染结构（user 文本 / CEO 文本 / 工具调用+结果）

    2026-09-03：CEO 的话术不再退化成工具条目——ask_user/report/finish 三个
    "面向用户输出"工具按话术 kind 还原（前端历史恢复能看到 CEO 提问/进度汇报/
    最终结论卡片），其余工具仍输出 kind=tool。三个话术工具回填的 tool 结果是
    占位文本（（已向用户提问：…）等），不随 kind 附加，避免与话术内容重复。
    """
    from core.ceo import render_recovery_reply

    out = []
    i, n = 0, len(history)

    def _args_of(fn):
        return _safe_json(fn.get("arguments", ""))

    while i < n:
        m = history[i]
        role = m.get("role")
        if role == "system":
            i += 1
            continue
        if role == "user":
            content = str(m.get("content", ""))
            # 2026-08-30：压缩摘要（role=user 的"[系统提示]..."）不作为
            # 用户消息渲染——恢复会话时对话区不应出现摘要占位
            if content.strip().startswith("[系统提示]"):
                i += 1
                continue
            out.append({"kind": "user", "text": content})
            i += 1
            continue
        if role == "assistant":
            tcs = m.get("tool_calls")
            if tcs:
                for tc in tcs:
                    fn = tc.get("function", {})
                    name = fn.get("name", "?")
                    args = _args_of(fn)
                    a = args if isinstance(args, dict) else {}
                    if name == "ask_user":
                        out.append({"kind": "ask", "name": name,
                                    "text": str(a.get("question") or "请详细说明你的需求")})
                    elif name == "report":
                        out.append({"kind": "report", "name": name,
                                    "text": str(a.get("summary") or ""),
                                    "reason": str(a.get("next_plan") or "")})
                    elif name == "finish":
                        out.append({"kind": "finish", "name": name,
                                    "status": a.get("status") or "completed",
                                    "text": str(a.get("summary") or ""),
                                    "workflow": str(a.get("workflow") or ""),
                                    "artifacts": [str(x) for x in (a.get("artifacts") or [])]})
                    else:
                        out.append({"kind": "tool", "name": name, "args": args})
                i += 1
                res = []
                while i < n and history[i].get("role") == "tool":
                    res.append(str(history[i].get("content", ""))[:300])
                    i += 1
                # 占位结果只附在最后一条"普通工具"上；CEO 话术（ask/report/finish）
                # 已自带内容，其占位 tool 结果（（已向用户提问：…）等）直接丢弃
                if res and out and out[-1]["kind"] == "tool":
                    out[-1]["result"] = "\n".join(res)[:500]
                continue
            out.append({"kind": "text", "text": render_recovery_reply(str(m.get("content", "")))})
            i += 1
            continue
        i += 1
    return out


@app.post("/api/session/graph")
def api_session_graph():
    """前端把当前图谱拓扑存入当前任务会话（历史会话恢复图谱用）"""
    data = request.get_json(force=True, silent=True) or {}
    graph = data.get("graph")
    name = _sanitize_name(data.get("name") or _current_sess["name"], _SESS_NAME_RE)
    if not name or graph is None:
        return jsonify({"ok": False, "error": "无当前会话或图谱数据"})
    try:
        path = config.DATA_DIR / "sessions" / f"{name}.json"
        if not path.exists():
            return jsonify({"ok": False, "error": "会话不存在"})
        # 防"任务总览"视图污染（2026-09-03 用户报告：auto-20260828-133107 被
        # 旧前端总览自动保存写入 29 个会话的混合图谱、含 29 个 "会话::ceo" 节点）。
        # 旧前端总览模式把全部会话节点 key 加 "${会话名}::" 前缀画到同一画布，
        # 该视图被自动保存写进了某会话文件。判定：合法单会话图谱的 key 要么是
        # 本会话自己的无前缀键（round-N / tN_M_x / node@task），要么只可能带
        # 本会话名的前缀（旧前端自身残留）；出现任何"其它会话名::"前缀的键，
        # 整图必为跨会话总览 dump → 拒绝写入。
        if isinstance(graph, list) and any(
            isinstance(n, dict) and isinstance(n.get("key"), str)
            and "::" in n["key"]
            and n["key"].split("::", 1)[0] != name
            for n in graph
        ):
            return jsonify({"ok": False, "error":
                "图谱含其它会话前缀的节点（疑似任务总览视图污染），拒绝写入"})
        from core.ceo import atomic_write_session, _session_io_lock
        with _session_io_lock:
            d = json.loads(path.read_text(encoding="utf-8"))
            # 防倒退覆盖（2026-08-30 多标签/测试页面污染事故修复）：
            # _current_sess 是服务端全局单值，任何第二个页面 loadSession 后收到
            # chat.user 都会解除前端抑制，把该页面当前图谱写回被查看的会话。
            # 正常任务中节点只增不减 → 新图谱显著少于现有 = 异常写入，拒绝。
            old_graph = d.get("graph") or []
            if len(old_graph) >= 5 and len(graph) < len(old_graph) * 0.6:
                return jsonify({"ok": False, "error":
                    f"图谱节点数异常减少 {len(old_graph)}→{len(graph)}，拒绝写入"
                    f"（多页面污染防护）"})
            d["graph"] = graph
            # 2026-08-30：原子写（锁 + tmp + replace），与节点保存监听/auto_save
            # 并发写同一会话文件防损坏（此前 write_text 并发导致 JSON 拼接损坏）
            atomic_write_session(path, d)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]})


@app.post("/api/session/load")
def api_session_load():
    """加载历史会话：图谱/消息/CEO 上下文立即切换（多会话隔离）

    每个会话有独立的 CEO 上下文——切换即激活目标会话的上下文，
    正在执行的任务锁定自己的会话不受影响（2026-08-28 用户规则）。
    """
    data = request.get_json(force=True, silent=True) or {}
    name = _sanitize_name(data.get("name"), _SESS_NAME_RE)
    if not name:
        return jsonify({"ok": False, "error": "会话名非法（仅允许字母数字-_）"})
    try:
        path = config.DATA_DIR / "sessions" / f"{name}.json"
        if not path.exists():
            return jsonify({"ok": False, "error": f"会话 {name} 不存在"})
        d = json.loads(path.read_text(encoding="utf-8"))
        # 同步当前任务会话名（后续保存/继续都落到该会话）+ 激活其上下文
        _current_sess["name"] = name
        ceo = _ceo_for_session(name)
        msg = ceo.load_conversation(name)
        # 该会话的任务是否正在执行（talk 锁定该会话）——前端据此判断
        # 恢复的 running 节点是真实执行中（保留）还是历史遗留（降级）
        sess_running = (getattr(ceo, "_talk_session", None) == name
                        and _session_busy(name))
        history = ceo._histories.get(name, d.get("history") or []) if sess_running else d.get("history") or []
        events = bus.session_graph_events(name)
        bus.emit("system", msg=msg, session=name)
        return jsonify({"ok": True, "msg": msg, "busy": _session_busy(name),
                        "running": sess_running,
                        "messages": _session_messages(history), "events": events,
                        "graph": d.get("graph") or []})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:300]})


@app.post("/api/session/new")
@_idle_mutation
def api_session_new():
    """新建对话：重置 CEO 上下文（历史保留在列表，可随时切回）"""
    if _busy["state"]:
        return jsonify({"ok": False, "error": "任务执行中，暂不能新建对话"})
    try:
        get_ceo().reset_if_task_done(force=True)
        _current_sess["name"] = None  # 新对话：下一个任务生成新的会话文件
        bus.emit("system", msg="已新建对话（CEO 上下文已重置，历史仍保存在列表）")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:300]})


def _display_event(event):
    if event.get("type") == "chat.recovery_reply":
        from core.ceo import render_recovery_reply
        data = event.get("data") or {}
        return {**event, "data": {**data, "text": render_recovery_reply(str(data.get("text", "")))}}
    return event


@app.get("/api/events")
def api_events():
    """Durable SSE cursor: replay and subscription overlap is deduplicated by event id."""
    try:
        after = int(request.headers.get("Last-Event-ID") or request.args.get("after", bus.last_id))
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "非法事件游标"}), 400
    q = bus.subscribe()

    def gen():
        cursor = max(0, after)
        try:
            while True:
                events = bus.history_after(cursor, 1000)
                if events:
                    for ev in events:
                        cursor = ev["id"]
                        yield f"id: {cursor}\ndata: {json.dumps(_display_event(ev), ensure_ascii=False)}\n\n"
                    continue
                try:
                    q.get(timeout=15)
                    # The durable source fills queue overflows and deduplicates replay.
                except queue.Empty:
                    yield "event: heartbeat\ndata: {}\n\n"
        finally:
            bus.unsubscribe(q)
    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/node/<node_id>")
def api_node(node_id):
    """节点详情：registry 状态 + 技能基因（含描述与 SKILL.md 内容）"""
    n = registry.get(node_id)
    if not n:
        return jsonify({"ok": False, "error": "节点不存在"})
    skills = []
    try:
        from core.hermes import HermesInstance
        sk = HermesInstance.list_skills(node_id)
        for name, desc in sk.items():
            skills.append({
                "name": name,
                "desc": (desc or "")[:300],
                "content": HermesInstance.get_skill(node_id, name) or "",
            })
    except Exception:
        pass
    # 节点钉住的 hermes 模型（profile 出生时写入；无钉住值=用全局 worker 配置）
    from core.hermes import HermesInstance as _HI
    node_dict = n.to_dict()
    node_dict["model"] = _HI._read_model_default(node_id) or config.worker_model
    return jsonify({"ok": True, "node": node_dict, "skills": skills})


# ---------------- 节点磁盘历史日志（2026-09-03：web3 抽屉历史任务无日志可看） ----------------
#
# 实时日志（SSE hermes.line/agentlog）只存在于前端内存缓冲：页面刷新或看历史
# 任务后抽屉两个标签（Hermes 执行日志 / 大脑决策）全空。但 hermes 执行的两类
# 日志本身落盘持久化：
#   - workspace/.adgs_logs/{node_id}_live.log  hermes stdout/stderr（逐任务追加）
#   - .hermes/profiles/{node_id}/logs/agent.log hermes-agent 完整日志
#     （API 调用 / 工具 / 上下文压缩 / 错误）
# 本端点供前端抽屉在实时缓冲为空时回退读取（最近 N 行）。
_NODE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")
_TAIL_READ_CAP = 512 * 1024  # 大文件只读尾部 512KB（日志按行截断，足够展示）


def _tail_lines(path: Path, n: int):
    """读文件尾部最近 n 个非空行；文件不存在返回 None"""
    try:
        if not path.exists():
            return None
        size = path.stat().st_size
        mtime = path.stat().st_mtime
        with open(path, "rb") as f:
            if size > _TAIL_READ_CAP:
                f.seek(-_TAIL_READ_CAP, 2)
            data = f.read()
    except OSError:
        return None
    text = data.decode("utf-8", errors="replace")
    lines = [l for l in text.splitlines() if l.strip()]
    return {
        "lines": lines[-n:],
        "total_lines": len(lines),
        "mtime": mtime,
        "size": size,
        "truncated": len(lines) > n or size > _TAIL_READ_CAP,
    }


@app.get("/api/node/<node_id>/logs")
def api_node_logs(node_id):
    """节点历史执行日志（磁盘持久化回退）：hermes=live.log，agentlog=agent.log"""
    if not _NODE_ID_RE.match(node_id or ""):
        return jsonify({"ok": False, "error": "非法节点 ID"}), 400
    try:
        lines = int(request.args.get("lines", 400))
    except (TypeError, ValueError):
        lines = 400
    lines = max(50, min(lines, 2000))
    from core.hermes import PROFILES_DIR
    hermes = _tail_lines(Path(config.workspace) / ".adgs_logs"
                         / f"{node_id}_live.log", lines)
    agentlog = _tail_lines(PROFILES_DIR / node_id / "logs" / "agent.log", lines)
    return jsonify({"ok": True, "node": node_id,
                    "hermes": hermes, "agentlog": agentlog})


@app.get("/api/nodes")
def api_nodes():
    """节点管理页：活动节点（含技能）+ 已淘汰节点（archived，不读技能避免慢 IO）

    2026-09-02 优化：技能读取并行化——52 个节点逐个 hermes CLI 读技能串行
    ~18s，ThreadPool 并行后 ~3s。
    """
    from concurrent.futures import ThreadPoolExecutor
    from core.hermes import HermesInstance
    actives = registry.active()

    def _with_skills(n):
        sk_names = []
        try:
            sk_names = list(HermesInstance.list_skills(n.id).keys())
        except Exception:
            pass
        return {
            "id": n.id, "describe": n.describe or "",
            "score": n.score, "success_rate": n.success_rate,
            "tasks_done": n.tasks_done, "gen": n.gen,
            "birth_type": n.birth_type, "parents": n.parents,
            "status": n.status, "created_at": n.created_at,
            "updated_at": n.updated_at,
            "skills": sk_names,
            # 该节点 hermes 钉住的执行模型（旧节点保持旧模型的可视化验证）
            "model": HermesInstance._read_model_default(n.id)
                     or config.worker_model,
        }

    nodes = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for item in ex.map(_with_skills, actives):
            nodes.append(item)
    archived = []
    for n in registry.archived():
        archived.append({
            "id": n.id, "describe": n.describe or "",
            "score": n.score, "success_rate": n.success_rate,
            "tasks_done": n.tasks_done, "gen": n.gen,
            "birth_type": n.birth_type, "parents": n.parents,
            "status": n.status, "created_at": n.created_at,
            "updated_at": n.updated_at,
            "skills": [],
        })
    return jsonify({"ok": True, "nodes": nodes, "archived": archived})


@app.post("/api/node/<node_id>/describe")
def api_node_describe(node_id):
    """编辑节点描述"""
    n = registry.get(node_id)
    if not n:
        return jsonify({"ok": False, "error": "节点不存在"})
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("describe") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "描述为空"})
    registry.update(node_id, describe=text[:2000])
    bus.emit("system", msg=f"已更新节点 {node_id} 描述")
    return jsonify({"ok": True})


@app.post("/api/node/<node_id>/skill/remove")
def api_node_skill_remove(node_id):
    """删除节点技能"""
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "未指定技能名"})
    from core.hermes import HermesInstance
    ok = HermesInstance.delete_skill(node_id, name)
    if not ok:
        return jsonify({"ok": False, "error": "删除技能失败"})
    bus.emit("system", msg=f"已删除节点 {node_id} 技能：{name}")
    return jsonify({"ok": True})


@app.post("/api/node/<node_id>/skill/install")
def api_node_skill_install(node_id):
    """从其它节点复制技能到本节点（基因移植）"""
    n = registry.get(node_id)
    if not n:
        return jsonify({"ok": False, "error": "节点不存在"})
    data = request.get_json(force=True, silent=True) or {}
    from_node = _sanitize_name(data.get("from_node"), _SESS_NAME_RE)
    name = _sanitize_name(data.get("name"), _SKILL_NAME_RE)
    if not from_node or not name:
        return jsonify({"ok": False, "error": "需要合法的 from_node 和 name"})
    from core.hermes import HermesInstance
    content = HermesInstance.get_skill_package(from_node, name)
    if not content:
        return jsonify({"ok": False, "error": f"源节点 {from_node} 没有技能 {name}"})
    desc = HermesInstance.list_skills(from_node).get(name, "")
    HermesInstance.install_skill(node_id, name, desc, content=content)
    bus.emit("system", msg=f"节点 {node_id} 已安装技能：{name}（来自 {from_node}）")
    return jsonify({"ok": True})


@app.post("/api/node/<node_id>/revive")
@_idle_mutation
def api_node_revive(node_id):
    """复活已淘汰节点（archived → active，profile 与技能基因保留）"""
    if _busy["state"]:
        return jsonify({"ok": False, "error": "任务执行中，暂不能复活节点"})
    n = registry.revive_node(node_id)
    if not n:
        return jsonify({"ok": False, "error": "节点不存在"})
    if n.get("unchanged"):
        return jsonify({"ok": False, "error": "该节点不是已淘汰状态"})
    bus.emit("system", msg=f"节点 {node_id} 已复活（回到活动种群）")
    return jsonify({"ok": True})


@app.post("/api/node/<node_id>/remove")
@_idle_mutation
def api_node_remove(node_id):
    """移除节点：registry 删除 + 删除 hermes profile（危险，前端需确认）"""
    if _busy["state"]:
        return jsonify({"ok": False, "error": "任务执行中，暂不能移除节点"})
    n = registry.get(node_id)
    if not n:
        return jsonify({"ok": False, "error": "节点不存在"})
    from core.hermes import HermesInstance
    try:
        HermesInstance.delete(node_id)
    except Exception as e:
        # 2026-08-30 P2-16：不再静默吞——profile 删除失败会留幽灵目录
        # （实测已有 375 个残留），registry 照删但提示用户手动清理
        bus.emit("system",
                 msg=f"节点 {node_id} profile 删除失败（registry 已移除）："
                     f"{str(e)[:120]}，可手动清理 profiles/{node_id}")
    registry.remove(node_id)
    bus.emit("system", msg=f"节点 {node_id} 已移除")
    return jsonify({"ok": True})


_bootstrap_lock = threading.Lock()
_bootstrapped = False
_bootstrap_started = set()


def _bootstrap_runtime():
    global _bootstrapped
    with _bootstrap_lock:
        if _bootstrapped:
            return
        with _queue_lock:
            activate_queue = getattr(_queue, "activate", None)
            if activate_queue:
                activate_queue()
        for name, target in (("adgs-evolution", _startup_evolve),
                             ("adgs-autosave", _session_save_listener)):
            if name in _bootstrap_started:
                continue
            for attempt in range(3):
                try:
                    threading.Thread(target=target, daemon=True, name=name).start()
                    _bootstrap_started.add(name)
                    break
                except Exception:
                    logging.exception("Runtime bootstrap thread %s failed (%d/3)", name, attempt + 1)
                    if attempt == 2:
                        raise
                    time.sleep(0.05)
        _bootstrapped = True


# Compatibility with the detached host's bootstrap entry point.
_bootstrap_evolution = _bootstrap_runtime
app.register_blueprint(create_control_api(sys.modules[__name__]))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ADGS 看板/运行时")
    parser.add_argument("--local", action="store_true",
                        help="单进程模式：CEO 与任务直接在本窗口进程执行，不再派生独立 runtime_host。"
                             "启动更直接，但关闭窗口会中断运行中的任务（默认双层模式任务可跨窗口存活）")
    parser.add_argument("--no-console-log", action="store_true",
                        help="关闭后端事件控制台输出（默认开启：谁派给谁/谁说了什么，内容截前 20 字）")
    args = parser.parse_args()

    print(f"[web] ADGS 看板: http://127.0.0.1:{PORT}/web3/", flush=True)
    if args.local:
        from web.event_bus import configure_event_store
        app.config["ADGS_PROXY_RUNTIME"] = False
        configure_event_store(Path(os.environ.get(
            "ADGS_STATE_DIR", str(Path(__file__).resolve().parent / ".adgs_runtime"))) / "events.sqlite")
        _bootstrap_runtime()
        _start_mesh_mcp()
        print("[web] 单进程模式：任务在本窗口进程执行——关闭窗口会中断运行中的任务", flush=True)
    else:
        app.config["ADGS_PROXY_RUNTIME"] = True
    if not args.no_console_log:
        threading.Thread(target=_console_event_printer, daemon=True,
                         name="adgs-console-log").start()
    app.run(host="127.0.0.1", port=PORT, threaded=True, debug=False)
