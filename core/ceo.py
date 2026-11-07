"""ADGS v3 ceo.py — CEO 智能体（唯一用户入口）

流程：
  用户提想法 → CEO 澄清理解（ask_user 循环） → 动态拆解下一步
  → 路由到节点 → 节点执行（并行） → 收集结果 → 再规划/汇报
"""
import json
import logging
import re
import threading
import uuid
import copy
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from typing import Dict, List, Optional

from config.config import config
from config.prompts import (CEO_SYSTEM_PROMPT, CEO_TOOLS, CEO_TOOL_BY_NAME,
                            render_task_md)
from core.compress import (should_compress, compress, sanitize_tool_messages)
from core.session_context import submit_context
from core.node import NODE
from core.router import route
from core.scheduling import write_resources, resources_conflict, dependency_results, external_resources
from utils.chat import CHAT, validate_arguments
from web.event_bus import bus

# 会话文件读写锁 + 原子写（2026-08-30 修复：节点保存监听线程、任务结束
# auto_save、前端 /api/session/graph 多线程并发 write_text 同一会话文件，
# 导致 JSON 拼接损坏（Extra data），loadSession 失败、界面卡欢迎页）
_session_io_lock = threading.RLock()
_RECOVERY_HEADING = "**事故恢复对话 · 节点任务暂停**"
_RECOVERY_TOOL_REPLY = "CEO 返回了工具指令，平台已拦截。节点任务仍暂停，你的消息已保存。"
_DSML_TAG = re.compile(r"<[/\\|｜ \t]*DSML\b", re.IGNORECASE)


def render_recovery_reply(text: str) -> str:
    """Hide model tool syntax in recovery replies, including historical ones."""
    if text.startswith(_RECOVERY_HEADING) and _DSML_TAG.search(text):
        return f"{_RECOVERY_HEADING}\n\n{_RECOVERY_TOOL_REPLY}"
    return text


def atomic_write_session(path, payload):
    """会话文件原子写：模块级锁 + 临时文件 + replace（Windows 原子替换）"""
    import datetime
    from core.contracts import utf8_safe
    payload.setdefault("saved_at",
                       datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    with _session_io_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        # 工具结果里可能带未配对代理字符；utf-8 落盘必须先清洗，否则保存
        # 会话抛 UnicodeEncodeError（2026-10-21 continue_task 中断实测）。
        tmp.write_text(utf8_safe(json.dumps(payload, ensure_ascii=False, indent=2)),
                       encoding="utf-8")
        tmp.replace(path)


def _safe_name(name: str, max_len: int = 30) -> str:
    """任务名 → 安全文件名（去非法字符，截断）"""
    import re
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", str(name))
    s = s.strip("_")
    return s[:max_len] or "task"


class CEO:
    _SESSION_DEFAULTS = {
        "_task_seq": 0, "_round_no": 0, "_loop_names": [], "_loop_fp": [],
        "_split_sigs": [], "_compress_count": 0, "_loop_active": False,
        "_loop_hint_pending": False, "_task_done": False, "_explore_count": 0,
        "_run_id": None, "_awaiting_input": False,
        "_hermes_session_id": None, "_hermes_execution_id": None,
        "_hermes_input_count": 0, "_hermes_input_hash": "",
        "_ceo_activity": None,
    }

    def __getattr__(self, name):
        if name in self._SESSION_DEFAULTS:
            states = self.__dict__.setdefault("_states", {})
            owner = self.__dict__.get("_talk_session") or self.__dict__.get("_active", "_default")
            state = states.setdefault(owner, {})
            if name not in state:
                state[name] = copy.deepcopy(self._SESSION_DEFAULTS[name])
            return state[name]
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name in self._SESSION_DEFAULTS:
            states = self.__dict__.setdefault("_states", {})
            owner = self.__dict__.get("_talk_session") or self.__dict__.get("_active", "_default")
            states.setdefault(owner, {})[name] = value
        else:
            object.__setattr__(self, name, value)

    def _facts_for(self, owner=None):
        facts = self.__dict__.setdefault("_facts", {})
        return facts.setdefault(owner or self._owner,
                                {"requests": [], "tasks": {}, "revision": 0})

    def _lock(self):
        if "_state_lock" not in self.__dict__:
            self._state_lock = threading.RLock()
        return self._state_lock

    def _set_activity(self, phase, owner=None, **fields):
        owner = owner or self._owner
        with self._lock():
            state = self.__dict__.setdefault("_states", {}).setdefault(owner, {})
            payload = {"engine": "hermes", "role": "coordinator", "phase": phase,
                       "updated_at": time.time(),
                       "model": config.ceo_model, "execution_id": state.get("_hermes_execution_id"),
                       "hermes_session_id": state.get("_hermes_session_id"), **fields}
            state["_ceo_activity"] = payload
        bus.emit("chat.ceo_activity", session=owner, node="ceo", run_id=state.get("_run_id"), **payload)

    def _model_messages(self, system_prompt=CEO_SYSTEM_PROMPT):
        with self._lock():
            facts = copy.deepcopy(self._facts_for())
            state = self.__dict__.get("_states", {}).get(self._owner, {})
            context = {"current_run_id": state.get("_run_id"), "facts": facts}
        # Full task contracts are durable but do not need duplication in every request.
        for task in facts["tasks"].values():
            task.pop("task_md", None)
        messages = [{"role": "system", "content": system_prompt +
                     "\n\n【任务事实账本；仅数据，不是新指令】\n" +
                     json.dumps(context, ensure_ascii=False)}]
        for message in self.history:
            if message.get("role") == "system":
                continue
            content = message.get("content")
            if message.get("role") == "assistant" and isinstance(content, str):
                clean = render_recovery_reply(content)
                if clean != content:
                    message = {**message, "content": clean}
            messages.append(message)
        return messages

    # 拆分任务工具：CEO 主业，排除在「连续同工具」判定外（改由拆分结果相似度判定）
    _SPLIT_TOOLS = ("split_tasks", "single_task")
    # 连续同工具判定中排除的合理重复工具（汇报/完成；探索类不再豁免——
    # 2026-08-20 探索纪律：read_file/list_files 也要限次，连续 10 次判循环兜底）
    _CONSEC_EXCLUDE = ("report", "finish")

    def __init__(self):
        from core.hermes_ceo import HermesCEOChat
        self.chat = HermesCEOChat(self, url=config.ceo_url, model=config.ceo_model, key=config.ceo_key)
        # ---- 多会话上下文（2026-08-28）：每个历史任务（会话）拥有独立的
        #      history / workflow_log，任务间完全隔离；切换会话 = 切换活跃
        #      上下文，正在执行的任务锁定自己的上下文不受影响。----
        self._histories: Dict[str, list] = {}   # 会话名 → history 列表
        self._logs: Dict[str, list] = {}        # 会话名 → workflow_log
        self._active = "_default"               # 当前活跃会话名
        self._talk_session: Optional[str] = None  # talk 运行期间锁定的会话名
        self.last_talk_session: Optional[str] = None  # 最近一次 talk 的会话（保存归属兜底）
        self._histories[self._active] = [{"role": "system", "content": CEO_SYSTEM_PROMPT}]
        self._logs[self._active] = []
        self._task_seq = 0                  # Web 图谱任务计数器
        self._round_no = 0                  # 任务循环计数（与前端图谱 round-N 对应，
                                            # 每轮对话 +1；task_id 带此前缀保证跨轮唯一，
                                            # 2026-08-25：t1 每轮复用导致第二轮序号挂第一轮下）
        # 动态循环上限状态（每轮 talk() 重置）
        self._loop_names: List[str] = []    # 最近工具名序列
        self._loop_fp: List[str] = []       # 最近归一化指纹
        self._split_sigs: List[str] = []    # 最近拆分结果签名（非拆分工具为空串）
        self._compress_count = 0            # 上下文压缩次数
        self._loop_active = False           # 当前是否处于循环状态（动态，可恢复）
        self._loop_hint_pending = False     # 疑似重复待提示标记（下一轮追加提示）
        self._task_done = False             # 上个任务是否已结束（finish/上限/出错）。
                                            # True → 下次新任务可重置上下文（web 任务模式）；
                                            # ask_user 挂起时为 False（回答要接着提问上下文）
        self._explore_count = 0             # 本任务探索工具累计次数（探索纪律：≤5 次）
        self._clarify_chat = None           # 澄清回答复用 CHAT 客户端（2026-08-30 P3）

    # ---- 多会话上下文代理：history/workflow_log 始终指向"当前会话" ----
    @property
    def _owner(self) -> str:
        """talk 运行期间锁定 talk 开始的会话；空闲时用当前活跃会话"""
        return self.__dict__.get("_talk_session") or self.__dict__.get("_active", "_default")

    @property
    def history(self) -> list:
        o = self._owner
        self.__dict__.setdefault("_histories", {})
        if o not in self._histories:
            self._histories[o] = [{"role": "system", "content": CEO_SYSTEM_PROMPT}]
        return self._histories[o]

    @history.setter
    def history(self, val):
        self.__dict__.setdefault("_histories", {})[self._owner] = val

    @property
    def workflow_log(self) -> list:
        o = self._owner
        self.__dict__.setdefault("_logs", {})
        if o not in self._logs:
            self._logs[o] = []
        return self._logs[o]

    @workflow_log.setter
    def workflow_log(self, val):
        self.__dict__.setdefault("_logs", {})[self._owner] = val

    def activate_session(self, name: str):
        """激活会话上下文（无则初始化全新历史）；不影响正在运行的 talk"""
        if not name:
            name = "_default"
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name):
            raise ValueError("会话名非法")
        with self._lock():
            if name not in self._histories:
                path = config.DATA_DIR / "sessions" / f"{name}.json"
                if path.exists():
                    # A POST after host restart must restore the saved chat,
                    # not create a blank history which autosave overwrites it.
                    self.load_conversation(name)
                    return
                self._histories[name] = [{"role": "system", "content": CEO_SYSTEM_PROMPT}]
            if name not in self._logs:
                self._logs[name] = []
            self._active = name

    def reset_session(self, name: str):
        """重置指定会话的上下文（异常清理用）：只清目标会话，
        不影响当前活跃会话（用户可能正切换查看其它历史任务）"""
        self._histories[name] = [{"role": "system", "content": CEO_SYSTEM_PROMPT}]
        self._logs[name] = []
        self.__dict__.setdefault("_states", {})[name] = {}
        self.__dict__.setdefault("_facts", {}).pop(name, None)

    # ================================================================
    # 对话保存 / 恢复（/save / /load 指令）
    # ================================================================

    def save_conversation(self, name: str, source_name: str = None) -> str:
        """保存指定会话的对话（含 history + workflow_log）到 data/sessions/<name>.json

        name 会话的上下文独立保存；talk 运行中保存时若 name 无独立上下文，
        回退保存当前锁定会话（_owner）的上下文。
        """
        import datetime
        import re
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError("会话名非法")
        sess_dir = config.DATA_DIR / "sessions"
        sess_dir.mkdir(parents=True, exist_ok=True)
        path = sess_dir / f"{name}.json"
        source_name = source_name or name
        with self._lock():
            if source_name not in self._histories:
                raise ValueError(f"会话 {source_name} 未加载，不能保存其他会话的数据")
            hist = copy.deepcopy(self._histories[source_name])
            log = copy.deepcopy(self._logs.get(source_name, []))
            facts = copy.deepcopy(self._facts_for(source_name))
            state = copy.deepcopy(self.__dict__.get("_states", {}).get(source_name, {}))
            execution_results = copy.deepcopy(self.__dict__.get("_execution_results", {}).get(source_name, {}))
        payload = {
            "name": name,
            "saved_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "history": hist,
            "workflow_log": log,
            "facts": facts,
            "session_state": state,
            "execution_results": execution_results,
        }
        # 2026-08-31 修复：save_conversation 是覆盖写，payload 没有 graph 字段
        # → 与前端 POST /api/session/graph 交替写入时，图谱字段被丢（用户实测
        # 001846 图谱 39→0）。保留文件已有的 graph，后端只负责 history/log。
        with _session_io_lock:
            try:
                graph_source = path if source_name == name else sess_dir / f"{source_name}.json"
                old = json.loads(graph_source.read_text(encoding="utf-8"))
                if "graph" in old:
                    payload["graph"] = old["graph"]
            except (OSError, ValueError):
                pass
            atomic_write_session(path, payload)
        return f"已保存对话到 {path}"

    def save_as(self, name: str) -> str:
        """Explicit user-requested alias; automatic saves never infer another owner."""
        return self.save_conversation(name, source_name=self._owner)

    def load_conversation(self, name: str) -> str:
        """恢复指定会话上下文并激活（多会话隔离：不影响运行中任务）"""
        import re
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError("会话名非法")
        if self._talk_session == name:
            self._active = name
            return f"会话 {name} 正在执行，保留实时上下文"
        path = config.DATA_DIR / "sessions" / f"{name}.json"
        if not path.exists():
            return f"会话 {name} 不存在（用 /sessions 查看）"
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("保存的会话必须是 JSON 对象，原文件保留")
        for key, kind in (("history", list), ("workflow_log", list), ("facts", dict),
                          ("session_state", dict), ("execution_results", dict)):
            if data.get(key) is not None and not isinstance(data[key], kind):
                raise ValueError(f"保存的会话 {key} 格式错误，原文件保留")
        if any(not isinstance(m, dict) for m in data.get("history") or []):
            raise ValueError("保存的会话消息格式错误，原文件保留")
        self._histories[name] = data.get("history") or [
            {"role": "system", "content": CEO_SYSTEM_PROMPT}]
        self._logs[name] = data.get("workflow_log") or []
        self.__dict__.setdefault("_facts", {})[name] = data.get("facts") or {
            "requests": [{"text": m.get("content", ""), "run_id": "legacy"}
                         for m in self._histories[name] if m.get("role") == "user"],
            "tasks": {}, "revision": 0}
        restored = data.get("session_state") or {}
        self.__dict__.setdefault("_states", {})[name] = {
            k: v for k, v in restored.items() if k in self._SESSION_DEFAULTS}
        self.__dict__.setdefault("_execution_results", {})[name] = data.get("execution_results") or {}
        self._active = name
        return (f"已恢复会话 {name}（{data.get('saved_at', '?')}，"
                f"{len(self._histories[name])} 条消息）")

    def mark_interrupted(self, name, error, *, status="exception", **details):
        """Persist a visible explanation for the interrupted turn."""
        reason = str(error)
        key = getattr(config, "ceo_key", "")
        if isinstance(key, str) and len(key) >= 8:
            reason = reason.replace(key, "[已隐藏]")
        reason = re.sub(r"(?i)\bBearer\s+[\w.\-+/=]+|\bsk-[\w-]{8,}", "[已隐藏]", reason)[:1200]
        text = f"本轮执行暂停，任务尚未完成。{reason}。成果与上下文已保留。"
        with self._lock():
            history = self._histories.setdefault(name, [{"role": "system", "content": CEO_SYSTEM_PROMPT}])
            history[:] = sanitize_tool_messages(history)
            history.append({"role": "assistant", "content": text})
            state = self.__dict__.setdefault("_states", {}).setdefault(name, {})
            # A runner exception interrupts the current task; it does not
            # complete it or authorize consumption of the next queued request.
            state["_task_done"] = False
            state["_awaiting_input"] = False
        self._set_activity("interrupted", owner=name)
        bus.emit("chat.ceo_end", status=status, session=name, run_id=state.get("_run_id"),
                 reason=reason, error=reason, text=text, **details)
        return text

    def list_sessions(self) -> str:
        """列出所有已保存会话"""
        sess_dir = config.DATA_DIR / "sessions"
        if not sess_dir.exists():
            return "（暂无已保存会话）"
        files = sorted(sess_dir.glob("*.json"))
        if not files:
            return "（暂无已保存会话）"
        lines = ["已保存会话："]
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                lines.append(f"  {data.get('name', f.stem):20s} {data.get('saved_at', '?')}  ({len(data.get('history', []))} 条消息)")
            except Exception:
                lines.append(f"  {f.stem:20s} （损坏）")
        return "\n".join(lines)

    def show_history(self) -> str:
        """打印当前对话历史"""
        lines = [f"当前对话历史（{len(self.history)} 条）："]
        for m in self.history[1:]:  # 跳过 system
            role = m.get("role", "?")
            if role == "user":
                lines.append(f"  [用户] {m.get('content', '')[:100]}")
            elif role == "assistant":
                tcs = m.get("tool_calls")
                if tcs:
                    lines.append(f"  [CEO] 调用工具: {tcs[0]['function']['name']}")
                else:
                    lines.append(f"  [CEO] {m.get('content', '')[:100]}")
            elif role == "tool":
                lines.append(f"  [工具结果] {m.get('content', '')[:80]}")
        return "\n".join(lines)

    def reset_if_task_done(self, force: bool = False) -> bool:
        """新任务开始前调用（web 任务模式）：上个任务已结束则重置 CEO 上下文

        设计原则：每个新任务 = 新上下文（节点层每次任务 reset_history，
        CEO 层 web 模式同样重置）。ask_user 挂起时 _task_done=False，
        用户回答的 talk 不重置，接着提问上下文。CLI 连续对话不调用本方法。
        force=True：无条件重置（talk 中途异常中断后调用，脏 history 防污染）。
        """
        if self._task_done or force:
            self.history = [{"role": "system", "content": CEO_SYSTEM_PROMPT}]
            self.workflow_log = []
            self._task_done = False
            # 派发序号重置：每轮任务循环从 1 开始计数
            # （图谱 round-N 下的序号 = 该轮第几次拆分；跨轮不累计。
            #   2026-08-24 修复：此前全局递增，第二轮第一个任务显示 2）
            self._task_seq = 0
            # 任务循环号 +1：task_id 以 round 为前缀（t{round}_{seq}），
            # 保证跨轮不重 key（2026-08-25：此前每轮复用 t1，前端 upsertNode
            # 不更新已存在节点 parent → 第二轮节点挂到第一轮序号下）
            self._round_no += 1
            print("[CEO] 新任务开始：上下文已重置", flush=True)
            return True
        return False

    def _maybe_reset_round(self) -> bool:
        """新一轮判定：上一任务已结束（_task_done=True）则序号归 1、轮号 +1

        talk() 每次进入时调用。ask_user 挂起恢复（_task_done=False）是同一轮，
        不重置序号；异常结束路径（web_app.py 任务 runner）恢复 _task_done=True，
        保证下一条用户消息作为新任务开新轮、序号从 1 开始
        （2026-09-03 修复：此前异常后 _task_done 残留 False，
        新一轮序号接续上轮，如轮 4 直接从 2 开始）。
        """
        if self._task_done:
            self._task_seq = 0
            self._round_no += 1
            self._task_done = False
            return True
        return False

    # ================================================================
    # 与用户对话（意图理解）
    # ================================================================

    def talk(self, user_input: str, *, recovery_execution=None) -> str:
        """用户消息入口：CEO 决定 ask_user 澄清 / 拆任务 / 汇报进度 / 完成

        talk 期间锁定会话上下文（_talk_session）：即使中途切换活跃会话
        （load 历史会话），本任务仍写自己的 history，任务间完全隔离。
        """
        self._talk_session = self._active
        self._recovery_execution = recovery_execution
        self._recovery_dispatched = False
        self.last_talk_session = self._talk_session  # 保存归属兜底（talk 结束后节点事件仍存对会话）
        try:
            return self._talk_body(user_input)
        finally:
            self._recovery_execution = None
            self._talk_session = None

    def talk_in_recovery(self, user_input: str) -> str:
        """Answer from saved context without entering the task loop."""
        self._talk_session = self._active
        try:
            with self._lock():
                self.history.append({"role": "user", "content": user_input})
            self.save_conversation(self._owner)
            bus.emit("chat.recovery_user", text=user_input)
            prompt = (
                "你是 ADGS 的 CEO。平台处于事故恢复保护状态。"
                "结合已保存的对话和任务事实回答用户，可以解释进展、核对已知状态和讨论下一步。"
                "目前没有可调用的工具。不要声称已检查实时文件、已派发或已恢复任务。"
                "历史工具记录仅供参考。用户要求执行时，用自然语言说明保护仍在生效。"
                "当前消息仅用于交流，不会加入执行队列，也不会改变原任务状态。"
                "用 Markdown 直接答复用户。"
            )
            messages = []
            for message in sanitize_tool_messages(self._model_messages(prompt)):
                role = message["role"]
                content = message.get("content") or ""
                if role == "tool":
                    role = "assistant"
                    content = "历史工具结果：\n" + content
                elif role == "assistant":
                    content = render_recovery_reply(content)
                    if message.get("tool_calls"):
                        calls = [call.get("function", {}) for call in message["tool_calls"]]
                        content += "\n历史工具操作记录：\n" + json.dumps(calls, ensure_ascii=False)
                messages.append({"role": role, "content": content})
            if should_compress(messages):
                messages = compress(messages)
            reply = self.chat.get_full_response(
                messages, timeout=config.worker_timeout, reject_tool_calls=True).strip()
            if reply == "__LLM_ERROR__: unexpected tool call":
                reply = _RECOVERY_TOOL_REPLY
            elif reply.startswith("__LLM_ERROR__:"):
                error = reply.removeprefix("__LLM_ERROR__:")
                if re.search(r"insufficient[_ -](?:quota|balance|credits)|quota.exceeded|余额不足|额度不足", error, re.I):
                    reply = "CEO 模型额度不足，暂时无法答复。你的消息已保存。"
                elif re.search(r"deadline|timed?\s*out|timeout", error, re.I):
                    reply = "CEO 模型请求超时。你的消息已保存，可以稍后重试。"
                else:
                    reply = "CEO 模型调用失败。你的消息已保存，可以稍后重试。"
            elif not reply:
                reply = "CEO 模型返回了空回复。你的消息已保存，可以稍后重试。"
            reply = render_recovery_reply(f"{_RECOVERY_HEADING}\n\n{reply}")
            with self._lock():
                self.history.append({"role": "assistant", "content": reply})
            self.save_conversation(self._owner)
            bus.emit("chat.recovery_reply", text=reply)
            return reply
        finally:
            self._talk_session = None

    def _has_pending_guidance(self):
        pending = self.__dict__.get("_guidance_pending")
        stopped = self.__dict__.get('_stop_requested')
        return bool((stopped and stopped()) or (pending and pending()))

    def _apply_pending_guidance(self):
        source = self.__dict__.get("_guidance_source")
        if not source:
            return False

        def receive(items):
            # Commit the exact user inputs before acknowledging removal from the queue.
            # No worker or Hermes context is replaced by this operation.
            with self._lock():
                before_history = copy.deepcopy(self.history)
                facts = self._facts_for()
                before_facts = copy.deepcopy(facts)
                try:
                    for item in items:
                        self.history.append({"role": "user", "content": item["text"]})
                        facts["requests"].append({"text": item["text"], "run_id": self._run_id,
                                                  "mode": "interrupt", "queue_id": item["id"]})
                        facts["revision"] += 1
                    self.save_conversation(self._owner)
                except Exception:
                    self.history = before_history
                    facts.clear()
                    facts.update(before_facts)
                    raise
            for item in items:
                try:
                    bus.emit("chat.user", text=item["text"], mode="interrupt", queue_id=item["id"],
                             session=self._owner)
                except Exception:
                    # The committed conversation is authoritative; notification failure
                    # must not cause the same instruction to execute twice.
                    logging.getLogger(__name__).exception("Guidance event delivery failed")
            self._explore_count = 0
            self._loop_names = []
            self._loop_fp = []
            self._split_sigs = []
            self._loop_active = False
            self._loop_hint_pending = False
        return bool(source(receive))

    def _talk_body(self, user_input: str) -> str:
        if self._task_done or not self._run_id:
            self._run_id = uuid.uuid4().hex
        self._awaiting_input = False
        with self._lock():
            self._facts_for()["requests"].append({"text": user_input, "run_id": self._run_id})
            self._facts_for()["revision"] += 1
        # 2026-09-29 修复：事件带全文。此前 [:500] 只影响前端显示（用户误以为
        # CEO 收到的任务书被截断）；history 始终是全文，不受影响。
        bus.emit("chat.user", text=user_input)
        self.history.append({"role": "user", "content": user_input})

        # 连续对话上下文管理（2026-08-24 修正）：任务完成不清空，历史累积；
        # 超过 token 阈值（ctx_window × trigger_ratio）才压缩——压缩为
        # 旧对话摘要 + 保留最近详情，不丢重点。此前按消息条数(>60)压缩，
        # 与"token 达到一定数量触发"的预期不符。
        if should_compress(self._model_messages(), ctx_window=config.ceo_ctx_window):
            before = len(self.history)
            self.history = self._compress_history()
            print(f"[CEO] 连续对话压缩：{before} 条 → {len(self.history)} 条"
                  f"（token 超阈值）", flush=True)

        # 每轮对话重置动态循环上限状态
        self._loop_names = []
        self._loop_fp = []
        self._split_sigs = []
        self._compress_count = 0
        self._loop_active = False
        self._loop_hint_pending = False
        self._explore_count = 0  # 探索纪律：每任务重置
        # 派发序号：新一轮对话（上一轮已 finish/end，_task_done=True）从 1 开始；
        # ask_user 挂起恢复（_task_done=False）是同一轮，不重置序号
        # （2026-08-24：连续对话模式下上下文保留，但新一轮序号归零）
        self._maybe_reset_round()

        step = 0
        protocol_failures = 0
        while step < self._dynamic_limit():
            if self._stop_if_requested():return '本轮已暂停，原任务保留。'
            if self._apply_pending_guidance():
                step = 0
            step += 1

            # 疑似重复：追加提示引导 CEO 换方向（不中断循环）
            if self._loop_hint_pending:
                self._loop_hint_pending = False
                self.history.append({
                    "role": "user",
                    "content": "（系统）检测到你最近在重复之前的动作"
                               "（反复调用同类工具、或拆出与之前相似的任务）。"
                               "请避免重复，换个角度推进；若任务已完成或无法继续，"
                               "请如实调用 finish(status=blocked) 说明阻塞；不能把无法继续写成完成。"})

            # 上下文压缩：历史超阈值则压缩并计数（压缩次数影响循环上限）
            if should_compress(self._model_messages(), ctx_window=config.ceo_ctx_window):
                self.history = self._compress_history()
                self._compress_count += 1
                print(f"[CEO] 上下文压缩 #{self._compress_count}"
                      f"（当前循环上限 {self._dynamic_limit()}）", flush=True)

            # 防御：发送给 LLM 的历史序列必须合法（assistant 的 tool_calls 后
            # 必须有对应 tool 响应）。任何异常中断 / 未知工具名留下的不平衡
            # 都会在此清理，避免 OpenAI/DeepSeek 400（insufficient tool
            # messages following tool_calls message）。
            clean = sanitize_tool_messages(self.history)
            if len(clean) != len(self.history):
                print(f"[CEO] 历史消息序列不完整，已清理 "
                      f"{len(self.history) - len(clean)} 条", flush=True)
                self.history = clean
            available_tools = [tool for tool in CEO_TOOLS
                if self._explore_count < 5 or tool["function"]["name"] not in ("read_file", "list_files")]
            if self.__dict__.get("_recovery_execution"):
                allowed = {"report", "ask_user", "finish"}
                if not self._recovery_dispatched:
                    allowed.add("continue_task")
                available_tools = [tool for tool in available_tools if tool["function"]["name"] in allowed]
            available_names = {tool["function"]["name"] for tool in available_tools}
            res = self.chat.call_tools(self._model_messages(), available_tools, tool_choice="required")
            if self._stop_if_requested():return '本轮已暂停，原任务保留。'
            if self._apply_pending_guidance():
                step = 0
                continue
            # 真正的 LLM 调用失败（网络/API 错误）才终止
            if res.get("error") and (res.get("error_kind") in ("network_error", "timeout", "provider_error")
                                      or "LLM call failed" in res["error"]
                                     or "Max retries" in res["error"]):
                # A provider outage ends this turn, not the task. Keeping the run
                # unfinished also prevents the web runner from draining queued
                # requests into the same unavailable provider.
                kind = res.get("error_kind")
                status_code = res.get("http_status")
                error = res["error"].removeprefix("LLM call failed: ")
                if re.search(r"insufficient[_ -](?:quota|balance|credits)|quota.exceeded|billing.hard.limit|余额不足|额度不足", error, re.I):
                    label = "CEO 模型额度不足"
                elif status_code == 429:
                    label = "CEO 模型服务限流"
                elif kind == "timeout":
                    label = "CEO 模型请求超时"
                elif kind == "network_error":
                    label = "CEO 无法连接模型服务"
                else:
                    label = "CEO 模型调用失败"
                if status_code:
                    label += f"（HTTP {status_code}）"
                return self._suspend_turn("error", f"{label}：{error}")
            if not res.get("tool_calls"):
                protocol_failures += 1
                if protocol_failures >= 3:
                    detail = res.get("error") or "模型未返回可用的工具调用"
                    return self._suspend_turn("blocked", f"CEO 连续三次工具调用协议错误：{detail}")
                # 模型没调工具（纯文本 / required 重试耗尽）：
                # 回填强提示，强制 CEO 用工具继续决策，不中断循环
                self.history.append({
                    "role": "assistant",
                    "content": res.get("content") or ""})
                self.history.append({
                    "role": "user",
                    "content": "（系统）你上一步没有调用任何工具。CEO 必须通过调用工具决策"
                               "（ask_user / split_tasks / single_task / read_file / list_files"
                               " / report / finish），禁止纯文本回答。请立即调用合适工具继续。"})
                continue

            tc = res["tool_calls"][0]
            name = tc["name"]
            args = tc.get("arguments") or {}
            tool_schema = CEO_TOOL_BY_NAME.get(name, {}).get("function", {}).get("parameters", {})
            argument_error = (validate_arguments(args, tool_schema) if name in available_names
                              else f"unknown tool {name}")
            if name in ("read_file", "list_files") and name not in available_names:
                argument_error = ("探索预算已耗尽。已有执行用 continue_task 恢复；新工作可拆分/委派。"
                                  "不得用新任务代替已授权的原会话续接。")
            if len(res["tool_calls"]) != 1 or argument_error:
                protocol_failures += 1
                if protocol_failures >= 3:
                    detail = argument_error or "一次返回了多个工具调用"
                    return self._suspend_turn("blocked", f"CEO 连续三次返回无效工具调用：{detail}")
                self.history.append({"role": "assistant", "content": res.get("content") or ""})
                self.history.append({"role": "user", "content": "[协议修复] 每次只调用一个工具，并补交合法参数：" +
                                     (argument_error or "returned multiple calls")})
                continue
            protocol_failures = 0

            # 疑似回复循环检测：动态进入/恢复
            was_loop = self._loop_active
            if self._detect_loop(name, args):
                if not was_loop:
                    print(f"[CEO] 检测到疑似回复循环（反复调用 {name}），"
                          f"循环上限收紧到 40", flush=True)
            elif was_loop:
                print(f"[CEO] 已跳出循环，恢复循环上限", flush=True)

            # 工具结果回填历史
            self.history.append({
                "role": "assistant",
                "content": res.get("content") or "",
                "tool_calls": [{
                    "id": tc["id"], "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
                }],
            })

            if name in ("read_file", "list_files"):
                self._set_activity("inspecting")
                self._explore_count += 1
                content = (self._read_file(args) if name == "read_file"
                           else self._list_files(args))
                if self._explore_count == 5:
                    content += ("\n[系统] 探索预算已用完。已有执行使用 continue_task 保留原会话；"
                                "新任务可拆分/委派；无法推进则如实报告阻塞。")
                self.history.append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "content": content})
                # 全量结果入事件流（前端折叠展示；2026-10-23 用户要求可点开看完整内容）
                bus.emit("chat.ceo_tool", name=name, args=args,
                         result=content)
                if self._stop_if_requested():return '本轮已暂停，原任务保留。'
                continue  # 让 CEO 看完文件内容再决定下一步

            if name == "ask_user":
                q = args.get("question", "请详细说明你的需求")
                self._task_done = False  # 提问挂起：用户回答时接着上下文，不重置
                self._awaiting_input = True
                self._set_activity("awaiting_user")
                self.history.append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "content": f"（已向用户提问：{q}）"})
                bus.emit("chat.ceo_ask", question=q)
                return f"CEO 提问：{q}"

            if name in ("split_tasks", "single_task", "continue_task"):
                self._set_activity("waiting_children", tool=name)
                try:
                    result = (self._continue_task(args) if name == "continue_task"
                              else self._dispatch_step(name, args))
                except Exception as e:
                    # 派发异常不中断：回填错误结果保持消息序列平衡，
                    # 并让 CEO 看到错误后自行决定下一步
                    result = {"error": f"派发异常: {type(e).__name__}: {e}",
                              "outcome": "interrupted", "infra_failure": True}
                summary = self._result_summary(result)
                infrastructure_error = self._dispatch_infrastructure_error(result)
                if infrastructure_error:
                    summary["ceo_decision_hint"] = (
                        f"本批有执行受阻：{infrastructure_error}。请根据结果决定下一步。"
                        "保留已完成成果。优先续接原执行或推进独立工作，避免重复派发同一受阻任务。"
                        "确实无法继续时，调用 finish(status=blocked)，说明原因、未完成项和恢复条件。"
                    )
                content = json.dumps(summary, ensure_ascii=False)
                self.history.append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "content": content})
                bus.emit("chat.ceo_tool", name=name, args=args,
                         result=content)
                self._set_activity("reviewing")
                if self._stop_if_requested():
                    return "本轮已暂停，原任务保留。"
                if self._apply_pending_guidance():
                    step = 0
                continue  # 让 CEO 看结果再决定下一步

            if name == "report":
                self._set_activity("reporting")
                # 进度汇报：打印到终端 + 回填结果，循环继续（不结束）
                print(f"\n[CEO 进度汇报] {args.get('summary', '')}\n"
                      f"  下一步：{args.get('next_plan', '')}\n", flush=True)
                self.history.append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "content": "（进度已汇报给用户，任务尚未结束，请继续决策下一步）"})
                bus.emit("chat.ceo_report",
                         summary=args.get("summary", ""),
                         next_plan=args.get("next_plan", ""))
                continue

            if name == "finish":
                finish_status = args.get("status", "completed")
                rejection = self._finish_rejection(finish_status)
                if finish_status != "completed" and not args.get("summary", "").strip():
                    rejection = "请在 summary 中说明具体原因和仍未完成的工作。"
                if rejection:
                    self.history.append({"role": "tool", "tool_call_id": tc["id"],
                                         "content": json.dumps({"outcome": "blocked", "error": rejection}, ensure_ascii=False)})
                    continue
                # CEO 作为直系上级的程序化结算：账本任务最终结论 → 节点分修正
                # （passed=+2 / failed=-2 / 其它 0；幅度与其它打分动作一致，
                # 单任务不会打穿总分——2026-10-24 分层验收设计）。
                if finish_status in {"completed", "failed"}:
                    self._settle_direct_reports(finish_status)
                self._task_done = finish_status != "blocked"
                self._set_activity(finish_status)
                self.history.append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "content": ("（受阻原因已向用户汇报，原任务待恢复）" if finish_status == "blocked"
                                else "（最终汇报已输出给用户，任务结束）")})
                bus.emit("chat.ceo_finish",
                         status=finish_status, run_id=self._run_id,
                         summary=args.get("summary", ""),
                         workflow=args.get("workflow", ""),
                         artifacts=args.get("artifacts", []))
                return self._format_report(args)

            # 未知工具兜底：模型幻觉出不在 CEO_TOOLS 里的工具名时，
            # 必须回填 tool 响应保持消息序列平衡，否则下次调用 400。
            self.history.append({
                "role": "tool", "tool_call_id": tc["id"],
                "content": (f"（系统未实现工具 {name}，已忽略；请使用 "
                            f"ask_user / split_tasks / single_task / "
                            f"read_file / list_files / report / finish）")})
            continue

        # while 正常结束 = 撞上动态上限
        reason = []
        if self._loop_active:
            reason.append("疑似回复循环")
        if self._compress_count:
            reason.append(f"压缩 {self._compress_count} 次")
        extra = "（" + "，".join(reason) + "）" if reason else ""
        return self._suspend_turn("limit", f"CEO 达到本轮决策上限 {step} 步{extra}", steps=step)

    def _suspend_turn(self, status, reason, **details):
        return self.mark_interrupted(self._owner, reason, status=status, **details)

    # ================================================================
    # 动态循环上限 + 回复循环检测 + 上下文压缩
    # ================================================================

    def _dynamic_limit(self) -> int:
        """动态循环上限：
        - 疑似回复循环 → 40（尽早收手）
        - 上下文压缩 > 5 次 → 60
        - 上下文压缩 >= 3 次 → 80
        - 都无 → 100
        """
        if self._loop_active:
            return 40
        if self._compress_count > 5:
            return 60
        if self._compress_count >= 3:
            return 80
        return 100

    def _detect_loop(self, name: str, args: dict) -> bool:
        """检测疑似回复循环，动态维护循环状态（确认后若不再重复则恢复）。

        返回当前是否处于循环状态（_loop_active）。
        - 归一化指纹：普通工具=精确指纹；拆分工具=拆分结果签名（相似则归并）。
        - 疑似重复（同指纹第 2 次 或 连续 3 次同工具）→ 置 _loop_hint_pending。
        - 循环状态由 _recheck_loop() 基于最近窗口重新评估：窗口内同指纹>=3 或
          连续 5 次同工具（report/finish/拆分工具排除）即循环，否则恢复。
        """
        if name in self._SPLIT_TOOLS:
            sig = self._split_signature(name, args)
            if sig:
                rep = sig
                for s in self._split_sigs:
                    if s and self._similar(sig, s):
                        rep = s
                        break
                fp = f"{name}::split::{rep}"
            else:
                fp = f"{name}::{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
            self._split_sigs.append(sig)
        else:
            fp = f"{name}::{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
            self._split_sigs.append("")

        self._loop_names.append(name)
        self._loop_fp.append(fp)
        if len(self._loop_names) > 10:
            self._loop_names = self._loop_names[-10:]
            self._loop_fp = self._loop_fp[-10:]
            self._split_sigs = self._split_sigs[-10:]

        count = self._loop_fp.count(fp)

        # 疑似重复 → 提示（仅「精确指纹第 2 次」触发；
        # 连续同工具阈值放宽到 10 次，中间不再单独提示）
        if count == 2:
            self._loop_hint_pending = True

        # 循环状态动态评估（确认后若不再重复则恢复）
        self._recheck_loop()
        return self._loop_active

    def _recheck_loop(self):
        """基于最近窗口重新评估循环状态：确认循环后若不再重复则恢复"""
        from collections import Counter
        cnt = Counter(self._loop_fp)
        if any(v >= 3 for v in cnt.values()):
            self._loop_active = True
            return
        # 连续同工具：最近 10 步（窗口满）全是同一工具才算循环。
        # 探索类工具（read_file/list_files）连续使用是正常行为，阈值放宽到 10。
        names = self._loop_names
        if (len(names) >= 10 and len(set(names)) == 1
                and names[0] not in self._CONSEC_EXCLUDE
                and names[0] not in self._SPLIT_TOOLS):
            self._loop_active = True
            return
        self._loop_active = False

    def _split_signature(self, name: str, args: dict) -> str:
        """提取拆分工具的结果签名（goal 拼接），非拆分工具返回空串"""
        if name == "split_tasks":
            subtasks = args.get("subtasks", [])
            if not isinstance(subtasks, list):
                subtasks = []
            goals = [str(s.get("goal", "")) for s in subtasks if isinstance(s, dict)]
            return " || ".join(goals)
        if name == "single_task":
            return str(args.get("goal", ""))
        return ""

    def _similar(self, a: str, b: str) -> bool:
        """两个拆分签名是否相似（精确相等 或 编辑相似度 >= 0.85）"""
        if not a or not b:
            return False
        if a == b:
            return True
        import difflib
        return difflib.SequenceMatcher(None, a, b).ratio() >= 0.85

    def _compress_history(self) -> list:
        """压缩 CEO 对话历史，并清理孤儿 tool 消息

        压缩截断点可能落在 assistant(tool_calls) 与对应 tool 结果之间，
        产生孤儿 tool / tool 结果不足的非法序列（OpenAI 400）——
        sanitize_tool_messages 完整清理（不只开头，含中段截断）。
        """
        compressed = compress(self._model_messages(), ctx_window=config.ceo_ctx_window)
        # Facts remain in their durable store; avoid duplicating them in history.
        return [{"role": "system", "content": CEO_SYSTEM_PROMPT}] + [
            m for m in sanitize_tool_messages(compressed) if m.get("role") != "system"]

    def _read_file(self, args: dict) -> str:
        """read_file 工具实现：读取本地小文件（探索纪律：≤3000 字符，>1MB 委派）"""
        from pathlib import Path
        path = str(args.get("path", "") or "").strip()
        if not path:
            return "[read_file 错误] 未提供 path 参数"
        # 探索纪律：CEO 只允许快速看小文件，字符数硬上限 3000（模型传大值也截断）
        max_chars = 3000

        p = Path(path)
        if not p.is_absolute():
            p = Path(config.workspace) / path
        try:
            p = p.resolve()
        except Exception:
            pass

        try:
            if not p.exists():
                return f"[read_file] 文件不存在: {p}"
            if p.is_dir():
                return f"[read_file] {p} 是目录，请改用 list_files 工具列出其内容"
            size = p.stat().st_size
            if size > 1_000_000:
                return (f"[read_file] 文件过大（{size} 字节），探索纪律："
                        f"大文件请用 single_task 委派节点读取并返回结构化摘要，"
                        f"CEO 不自行读取大文件")
            raw = p.read_bytes()
            text = None
            for enc in ("utf-8", "gbk"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            if text is None:
                return f"[read_file] 无法按文本解码（疑似二进制），大小 {size} 字节"
            total = len(text)
            if total > max_chars:
                text = text[:max_chars] + f"\n…（已截断，原文共 {total} 字符）"
            return f"[read_file] {p}（{size} 字节，{total} 字符）:\n{text}"
        except Exception as e:
            return f"[read_file] 读取失败: {type(e).__name__}: {e}"

    def _list_files(self, args: dict) -> str:
        """list_files 工具实现：列本层目录（探索纪律：禁递归，≤50 项）"""
        from pathlib import Path
        path = str(args.get("path", "") or "").strip()
        if not path:
            return "[list_files 错误] 未提供 path 参数"
        # 探索纪律：递归列目录消耗大，CEO 一律不自行递归，委派节点
        if bool(args.get("recursive", False)):
            return ("[list_files] 递归列出消耗大，探索纪律禁止 CEO 自行递归；"
                    "请用 single_task 委派节点查看目录结构并返回摘要")
        max_items = 50

        p = Path(path)
        if not p.is_absolute():
            p = Path(config.workspace) / path
        try:
            p = p.resolve()
        except Exception:
            pass

        try:
            if not p.exists():
                return f"[list_files] 路径不存在: {p}"
            if not p.is_dir():
                return f"[list_files] {p} 不是目录（可用 read_file 读取文件内容）"

            entries = sorted(p.iterdir(), key=lambda x: x.name.lower())

            label = "本层"
            lines = [f"[list_files] {p}（{label}，共 {len(entries)} 项）："]
            for e in entries[:max_items]:
                rel = e.name
                if e.is_dir():
                    lines.append(f"  [目录] {rel}/")
                else:
                    try:
                        size = e.stat().st_size
                    except Exception:
                        size = 0
                    lines.append(f"  [文件] {rel}  ({size}B)")
            if len(entries) > max_items:
                lines.append(f"  …（还有 {len(entries) - max_items} 项未列出）")
            return "\n".join(lines)
        except Exception as e:
            return f"[list_files] 列出失败: {type(e).__name__}: {e}"

    # ================================================================
    # 任务分发（动态拆解下一步）
    # ================================================================

    def _record_result(self, execution_id, result):
        with self._lock():
            item = self._facts_for()["tasks"].get(execution_id)
            if item is None:
                return
            evaluation = result.get("evaluation") or {}
            outcome = result.get("outcome") or result.get("status")
            if result.get("infra_failure"):
                outcome = outcome or "infra_failure"
            elif evaluation.get("unjudged") or result.get("unjudged"):
                outcome = outcome or "unjudged"
            elif outcome not in ("protocol_error", "unjudged", "infra_failure", "interrupted", "blocked",
                                 "waiting_user", "evaluation_unavailable", "context_overflow"):
                outcome = "passed" if result.get("success") else "failed"
            item.update({"outcome": outcome, "evaluation": copy.deepcopy(evaluation),
                         "hermes_session_id": result.get("hermes_session_id") or result.get("session_id"),
                         "profile": result.get("profile") or item.get("profile"),
                         "summary": str(result.get("summary") or result.get("error") or
                                        evaluation.get("detail") or "")[:2000]})
            self.__dict__.setdefault("_execution_results", {}).setdefault(self._owner, {})[execution_id] = copy.deepcopy(result)
            children = result.get("children") or []
            subtasks = result.get("subtasks") or []
            child_ids = []
            for index, child in enumerate(children):
                if not isinstance(child, dict) or not child.get("execution_id"):
                    continue
                child_id = child["execution_id"]
                contract = subtasks[index] if index < len(subtasks) and isinstance(subtasks[index], dict) else {}
                existing = self._facts_for()["tasks"].get(child_id, {})
                child_fact = {"execution_id": child_id,
                    "task_id": child.get("task_id") or existing.get("task_id") or child_id,
                    "node_id": child.get("node_id") or existing.get("node_id"),
                    "run_id": child.get("run_id") or item.get("run_id"),
                    "active_run_id": item.get("active_run_id", item.get("run_id")),
                    "parent_execution_id": execution_id,
                    "task_md": child.get("task_md") or existing.get("task_md"),
                    "name": child.get("name") or contract.get("name") or existing.get("name", ""),
                    "goal": contract.get("goal") or existing.get("goal", ""),
                    "permissions": contract.get("permissions") or existing.get("permissions", item.get("permissions")),
                    "requirements": contract.get("requirements", existing.get("requirements", [])),
                    "acceptance_criteria": contract.get("acceptance_criteria", existing.get("acceptance_criteria", [])),
                    "deliverables": contract.get("deliverables", existing.get("deliverables", [])),
                    "write_paths": contract.get("write_paths", existing.get("write_paths")),
                    "blocked_by": [children[dependency]["execution_id"]
                                   for dependency in contract.get("depends_on", [])
                                   if type(dependency) is int and 0 <= dependency < len(children)
                                   and isinstance(children[dependency], dict)
                                   and children[dependency].get("execution_id")]
                                  if "depends_on" in contract else existing.get("blocked_by", []),
                    "started": bool(child.get("hermes_session_id") or child.get("session_id") or existing.get("started")
                                    or child.get("node_id") or child.get("action") == "split_tasks"),
                    "outcome": child.get("outcome") or "unjudged"}
                self._facts_for()["tasks"][child_id] = child_fact
                self._record_result(child_id, child)
                child_ids.append(child_id)
            if children:
                item["children"] = child_ids
            self._facts_for()["revision"] += 1

    def _result_summary(self, result):
        if "results" in result:
            return {k: (self._result_summary(result[k]) if k == "results" else result[k])
                    for k in ("stages", "tasks", "results", "outcome", "error") if k in result}
        if isinstance(result, list):
            return [self._result_summary(r) for r in result]
        keys = ("idx", "subtask", "name", "action", "node_id", "task_id", "execution_id", "run_id", "profile",
                "hermes_session_id", "success", "outcome", "unjudged", "infra_failure", "error",
                "not_started", "task_definition",
                "termination_reason", "evaluation", "deliverables")
        summary = {key: result[key] for key in keys if key in result}
        if result.get("children"):
            summary["children"] = [self._result_summary(child) for child in result["children"]]
        # Preserve each result and evaluation; do not truncate serialized JSON.
        if result.get("summary"):
            summary["summary"] = str(result["summary"])[-2000:]
        elif result.get("work_log"):
            summary["report_excerpt"] = str(result["work_log"])[-2000:]
        return summary

    def _finish_rejection(self, status):
        if status not in {"completed", "answered"}:
            return None
        tasks = [t for t in self._facts_for()["tasks"].values()
                 if t.get("active_run_id", t.get("run_id")) == self._run_id]
        if status == "answered":
            return ("本轮已有执行任务，请继续验收并使用 completed、blocked 或 failed；不能用纯答复跳过任务义务。"
                    if tasks else None)
        if not tasks:
            return "本轮没有已验收的任务证据。纯答复已有信息请用 answered；业务工作需先执行或验收，无法推进时用 blocked。"
        pending = [t["task_id"] for t in tasks if t.get("outcome") != "passed"]
        if pending:
            return "以下必需任务尚未验收通过：" + ", ".join(pending)
        if any(t.get("evaluation", {}).get("unjudged") for t in tasks):
            return "存在无法判断的验收结果。"
        if any(t.get("evaluation", {}).get("passed") is not True for t in tasks):
            return "任务缺少明确的验收通过证据。"
        return None

    # 预算类超时：会话可续接（下一次 continue_task 拿全新预算），
    # 不属于"换任务 ID 也修不好"的基础设施故障。
    _BUDGET_EXHAUSTION_REASONS = {"wall_timeout", "inactivity_timeout"}
    _BUDGET_EXHAUSTION_PREFIXES = ("Wall timeout", "No observed task activity")

    def _settle_direct_reports(self, finish_status: str):
        """CEO 直系结算（程序化映射）：把账本里每个 depth-0 任务的最终结论换算为
        节点分修正——CEO 的逐项验收就是它的判断，映射即打分（2026-10-24 用户选定）。
        passed→+2 / failed→-2 / blocked・unjudged→0；每任务只结算一次。"""
        try:
            from core.node import NODE
            tasks = dict(self._facts_for().get("tasks") or {})
        except Exception:
            return
        for execution_id, task in tasks.items():
            if not isinstance(task, dict) or task.get("settled") or not task.get("node_id"):
                continue
            # 结论优先取账本；缺失时回读执行存档
            outcome = str(task.get("outcome") or "").lower()
            if not outcome:
                saved = execution_store.get(execution_id) or {}
                outcome = str(saved.get("state") or "").lower()
            raw = 2.0 if outcome == "passed" else (-2.0 if outcome == "failed" else 0.0)
            if raw == 0.0:
                task["settled"] = True
                continue
            try:
                node = NODE(task["node_id"])
                current = node.reg.get(task["node_id"]).score
                node._score_base = current
                node._execution_id = f"ceo-settle-{str(execution_id)[:12]}"
                node._update_node(outcome == "passed", current + raw)
                task["settled"] = True
                self._facts_for()["tasks"][execution_id]["settled"] = True
                bus.emit("ceo.settle", node=task["node_id"], execution_id=execution_id,
                         outcome=outcome, delta=raw, finish=finish_status)
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "CEO settle failed for %s: %s", task.get("node_id"), exc)

    @classmethod
    def _dispatch_infrastructure_error(cls, result):
        """提取需要提醒 CEO 的服务故障。普通执行超时可按原预算规则续接。"""
        if not isinstance(result, dict):
            return ""
        if result.get("infra_failure") or result.get("outcome") in {
                "infra_failure", "evaluation_unavailable", "context_overflow"}:
            reason = str(result.get("termination_reason") or "")
            error = str(result.get("error") or result.get("detail") or result.get("outcome") or "执行基础设施错误")
            if reason in cls._BUDGET_EXHAUSTION_REASONS or (
                    not reason and error.startswith(cls._BUDGET_EXHAUSTION_PREFIXES)):
                return ""
            return error
        for field in ("results", "children"):
            for child in result.get(field) or []:
                error = cls._dispatch_infrastructure_error(child)
                if error:
                    return error
        return ""

    def _stop_if_requested(self):
        callback=getattr(self,'_stop_requested',None)
        if callable(callback) and callback():
            self._task_done=False
            self._awaiting_input=False
            self._set_activity("paused")
            bus.emit('chat.ceo_end',status='paused',reason='控制端请求暂停；原任务、会话和排队消息已保留')
            return True
        return False

    def _continue_task(self, args):
        execution_id = args["execution_id"]
        recovery_execution = self.__dict__.get("_recovery_execution")
        if recovery_execution and execution_id != recovery_execution:
            return {"outcome": "protocol_error", "error": "事故保护期间只允许恢复本次核准的原执行"}
        if recovery_execution and self._recovery_dispatched:
            return {"outcome": "protocol_error", "error": "本轮已尝试恢复，先向用户报告结果"}
        with self._lock():
            item = self._facts_for()["tasks"].get(execution_id)
            if not item:
                # A restored chat can lose its ledger while the original execution
                # checkpoint still retains the exact chat and Hermes ownership.
                from core.execution_store import execution_store, preworker_attempts_safe, preworker_resume_evidence
                saved = execution_store.get(execution_id)
                resumable_owner = bool(saved and saved.get("session_id"))
                if saved and not resumable_owner and preworker_resume_evidence(saved):
                    from core.terminal_runtime import terminal_store
                    resumable_owner = preworker_attempts_safe(terminal_store.list_attempts(execution_id=execution_id))
                if (saved and saved.get("session_name") == self._owner
                        and resumable_owner
                        and all(saved.get(k) for k in
                                ("node_id", "task_id", "run_id", "task_md"))):
                    item = {"execution_id": execution_id,
                            **{k: copy.deepcopy(saved[k]) for k in
                               ("node_id", "task_id", "run_id", "task_md")},
                            "profile": saved.get("profile"),
                            "hermes_session_id": saved.get("session_id"),
                            "started": True, "outcome": saved.get("state", "unjudged"),
                            "blocked_by": copy.deepcopy(saved.get("blocked_by", []))}
                    self._facts_for()["tasks"][execution_id] = item
            if not item:
                return {"outcome": "protocol_error", "error": "该执行不属于当前会话，不能新起会话代替恢复"}
            blocked_by = [eid for eid in item.get("blocked_by", [])
                          if self._facts_for()["tasks"].get(eid, {}).get("outcome") != "passed"]
            if blocked_by:
                return {"outcome": "blocked", "success": False, "execution_id": execution_id,
                        "error": "前驱尚未验收通过：" + ", ".join(blocked_by)}
            # Resuming a run carries its remaining obligations into this conversation round.
            for related in self._facts_for()["tasks"].values():
                if related.get("run_id") == item.get("run_id"):
                    related["active_run_id"] = self._run_id
            item["active_run_id"] = self._run_id
            item["outcome"] = "queued"
            snapshot = copy.deepcopy(item)
        if recovery_execution:
            self._recovery_dispatched = True
        try:
            if not snapshot.get("started"):
                # A dependency-blocked task has never created a Hermes session.
                from core.registry import registry
                node = route(snapshot["task_md"], reg=registry)
                if node is None:
                    result = {"success": False, "outcome": "blocked", "error": "无可用节点"}
                else:
                    item["node_id"] = node.id
                    item["started"] = True
                    latest = [{"execution_id": eid,
                               **{k: self._facts_for()["tasks"][eid].get(k)
                                  for k in ("outcome", "evaluation", "deliverables", "summary")}}
                              for eid in snapshot.get("blocked_by", [])]
                    task_md = snapshot["task_md"] + "\n\n【前驱最新验收证据】\n" + json.dumps(latest, ensure_ascii=False)
                    result = self._dispatch_single(task_md + "\n\n" + args["instruction"],
                        snapshot.get("name", "continue"), node, snapshot["task_id"], snapshot.get("goal"),
                        execution_id=execution_id, run_id=snapshot["run_id"],
                        workspace=snapshot.get("workspace"))
            else:
                result = NODE(snapshot["node_id"]).continue_execution(
                    execution_id, args["instruction"], task_id=snapshot["task_id"], run_id=snapshot["run_id"])
        except Exception as exc:
            result = {"success": False, "outcome": "interrupted", "error": str(exc)}
        result.setdefault("execution_id", execution_id)
        self._record_result(execution_id, result)
        return result

    def _dispatch_step(self, tool_name: str, args: dict) -> dict:
        overall = args.get("overall_goal", "")
        subtasks = args.get("subtasks", []) if tool_name == "split_tasks" else [{
            "name": "single", "goal": args.get("goal", ""),
            "requirements": args.get("requirements", []),
            "acceptance_criteria": args.get("acceptance_criteria", []),
            "deliverables": args.get("deliverables", []),
            **({"write_paths": args["write_paths"]} if "write_paths" in args else {}),
            "permissions": args.get("permissions", ""), "depends_on": []}]
        schema = CEO_TOOL_BY_NAME["split_tasks"]["function"]["parameters"]["properties"]["subtasks"]
        error = validate_arguments(subtasks, schema)
        if error:
            return {"outcome": "protocol_error", "error": error}
        count = len(subtasks)
        try:
            resources = [write_resources(t, config.workspace) | external_resources(t)
                         for t in subtasks]
        except ValueError as exc:
            return {"outcome": "protocol_error", "error": str(exc)}
        deps = []
        for i, task in enumerate(subtasks):
            if not task.get("goal", "").strip() or not task.get("name", "").strip():
                return {"outcome": "protocol_error", "error": f"Task {i} requires a nonempty name and goal"}
            dependencies = task.get("depends_on", [])
            if (any(type(d) is not int or not 0 <= d < count or d == i for d in dependencies)
                    or len(set(dependencies)) != len(dependencies)):
                return {"outcome": "protocol_error", "error": f"Invalid dependencies for task {i}"}
            deps.append(set(dependencies))
        visited = set()
        while True:
            ready = {i for i in range(count) if i not in visited and deps[i] <= visited}
            if not ready:
                break
            visited.update(ready)
        if len(visited) != count:
            return {"outcome": "protocol_error", "error": "Dependency cycle; no tasks were dispatched"}
        results, pending, futures = {}, set(range(count)), {}
        batches = 0
        with ThreadPoolExecutor(max_workers=config.max_workers_effective) as executor:
            while pending or futures:
                guiding = self._has_pending_guidance()
                if guiding and not futures:
                    break
                ready = [] if guiding else sorted(i for i in pending if deps[i] <= results.keys())
                ready = [i for i in ready if any(not results[d].get("success") for d in deps[i])
                         or (len(futures) < config.max_workers_effective and not any(
                             resources_conflict(resources[i], resources[j]) for j in futures.values()))]
                if ready:
                    batches += 1
                    with self._lock():
                        self._task_seq += 1
                        seq = self._task_seq
                    stage_id = f"t{self._round_no}_{seq}_{uuid.uuid4().hex[:8]}"
                    for idx in ready:
                        if self._has_pending_guidance():
                            break
                        blocked = [d for d in deps[idx] if not results[d].get("success")]
                        if not blocked and (len(futures) >= config.max_workers_effective or any(
                                resources_conflict(resources[idx], resources[j]) for j in futures.values())):
                            continue
                        pending.remove(idx)
                        if blocked:
                            item = self._prepare_task(overall, subtasks[idx], idx, seq, stage_id,
                                                      self._depends_context(subtasks[idx], results), route_task=False)
                            result = {"idx": idx, "subtask": subtasks[idx]["name"], "success": False,
                                      "outcome": "blocked", "error": f"Required predecessors not accepted: {blocked}",
                                      **{k: item[k] for k in ("task_id", "execution_id", "run_id")}}
                            self._facts_for()["tasks"][item["execution_id"]]["blocked_by"] = [
                                results[d]["execution_id"] for d in deps[idx] if results[d].get("execution_id")]
                            self._record_result(item["execution_id"], result)
                            results[idx] = result
                            continue
                        item = self._prepare_task(overall, subtasks[idx], idx, seq, stage_id,
                                                  self._depends_context(subtasks[idx], results))
                        futures[submit_context(executor, self._execute_prepared, item)] = idx
                if futures:
                    if not self._has_pending_guidance() and len(futures) < config.max_workers_effective and any(
                            deps[i] <= results.keys() and not any(resources_conflict(resources[i], resources[j])
                                                                  for j in futures.values()) for i in pending):
                        continue
                    finished, _ = wait(futures, return_when=FIRST_COMPLETED)
                    for future in finished:
                        idx = futures.pop(future)
                        try:
                            result = future.result()
                        except Exception as exc:
                            result = {"success": False, "outcome": "interrupted", "error": str(exc)}
                        result.update({"idx": idx, "subtask": subtasks[idx]["name"]})
                        results[idx] = result
                elif pending and not ready:
                    if self._has_pending_guidance():
                        break
                    raise RuntimeError("Validated dependency graph made no progress")
        for idx in sorted(pending):
            results[idx] = {"idx": idx, "subtask": subtasks[idx]["name"], "success": False,
                            "outcome": "deferred", "not_started": True,
                            "task_definition": copy.deepcopy(subtasks[idx]),
                            "error": "收到用户引导，本任务尚未派发；请结合最新要求重新决定后续安排"}
        return {"stages": batches, "tasks": count,
                "results": [results[i] for i in range(count)]}

    def _prepare_task(self, overall, task, idx, seq, stage_id, background="", route_task=True):
        from core.registry import registry
        if not self._run_id:
            self._run_id = uuid.uuid4().hex
        task_id = f"{stage_id}_{idx}"
        execution_id = uuid.uuid4().hex
        permissions = task.get("permissions") or "只读；不得修改任何文件，权限不明确时先澄清"
        from core.scheduling import artifact_dir
        report_path = (f"{artifact_dir(config.workspace, self._run_id)}"
                       f"/{_safe_name(task.get('name', 'task'))}_{task_id}_report.md")
        md = render_task_md(overall, task.get("goal", ""), task.get("requirements", []),
                            task.get("acceptance_criteria", []), task.get("deliverables", []),
                            permissions, background + "\n\n" + (task.get("background") or ""),
                            report_path=report_path, write_paths=task.get("write_paths"))
        if task.get("write_paths") is not None:
            md += "\n\n## 写路径约束\n" + json.dumps(task["write_paths"], ensure_ascii=False)
        node = route(md, reg=registry) if route_task else None
        item = {"task_id": task_id, "execution_id": execution_id, "run_id": self._run_id,
                "stage_id": stage_id, "node_id": node.id if node else None,
                "workspace": str(config.workspace),
                "goal": task.get("goal", ""), "permissions": permissions,
                "overall_goal": overall, "requirements": task.get("requirements", []),
                "name": task.get("name", ""), "task_md": md, "started": False,
                "write_paths": task.get("write_paths"),
                "acceptance_criteria": task.get("acceptance_criteria", []),
                "deliverables": task.get("deliverables", []), "outcome": "queued"}
        with self._lock():
            self._facts_for()["tasks"][execution_id] = copy.deepcopy(item)
        bus.emit("graph.task", id=task_id, task_id=task_id, execution_id=execution_id,
                 run_id=self._run_id, stage_id=stage_id, name=task.get("name", ""),
                 goal=task.get("goal", ""), task_md=md, overall=overall, parent="ceo", seq=seq, count=1)
        return {**item, "task_md": md, "name": task.get("name", ""), "node": node}

    def _execute_prepared(self, item):
        if item["node"] is None:
            result = {"success": False, "outcome": "blocked", "error": "无可用节点"}
        else:
            with self._lock():
                self._facts_for()["tasks"][item["execution_id"]]["started"] = True
            result = self._dispatch_single(item["task_md"], item["name"], item["node"],
                                           item["task_id"], item["goal"],
                                           execution_id=item["execution_id"], run_id=item["run_id"],
                                           workspace=item.get("workspace") or config.workspace)
        for key in ("task_id", "execution_id", "run_id", "node_id"):
            result.setdefault(key, item[key])
        self._record_result(item["execution_id"], result)
        return result

    def _depends_context(self, st: dict, results_by_idx: dict) -> str:
        """生成依赖前驱子任务的结果上下文（注入依赖方，实现 depends_on 语义）"""
        deps = st.get("depends_on", []) or []
        if not deps:
            return ""
        parts = []
        for d in deps:
            if not isinstance(d, int) or d not in results_by_idx:
                continue
            r = results_by_idx[d]
            status = "完成" if r.get("success") else "失败"
            summary = (r.get("summary") or r.get("work_log") or "")[:800]
            parts.append(f"【前驱子任务 {d}（{r.get('subtask', '?')}）结果：{status}】\n{summary}")
        return "\n\n".join(parts)

    def _dispatch_parallel(self, overall: str, subtasks: list,
                           stage: List[int], dep_ctx: dict = None) -> List[dict]:
        """Compatibility entry: same profile, independent execution/session IDs."""
        dep_ctx = dep_ctx or {}
        with self._lock():
            self._task_seq += 1
            seq = self._task_seq
        stage_id = f"t{self._round_no}_{seq}_{uuid.uuid4().hex[:8]}"
        items = [self._prepare_task(overall, subtasks[i], i, seq, stage_id, dep_ctx.get(i, ""))
                 for i in stage]
        def submit(local_idx, _results):
            result = self._execute_prepared(items[local_idx])
            idx = stage[local_idx]
            result.update({"idx": idx, "subtask": subtasks[idx].get("name", "")})
            return result
        stage_tasks = [{**subtasks[i], "depends_on": []} for i in stage]
        out = dependency_results(stage_tasks, submit, config.max_workers_effective,
                                 workspace=config.workspace)
        return out

    def _dispatch_single(self, task_md: str, name: str,
                         node=None, task_id: str = None,
                          goal: str = None, execution_id: str = None,
                          run_id: str = None, workspace: str = None) -> dict:
        """派发单个子任务到指定节点；ask_upper 时 CEO 澄清后重派同一节点（最多3次）

        node: 预路由锁定的节点（_dispatch_parallel 传入）；None 时自行路由
        （独立调用场景）。澄清会追加任务书使任务书变长，若每轮重新路由，
        向量匹配结果会漂移，可能路由到没看过原始问题的新节点，导致澄清
        上下文丢失 —— 因此只在首次路由，后续澄清重派沿用同一节点。
        task_id: 图谱任务节点 id（graph.task 事件同源），派发事件随附。
        goal: 本子任务目标（并发时各自子任务目标，前端 ags 节点点开展示）。
        """
        from core.registry import registry
        execution_id = execution_id or uuid.uuid4().hex
        run_id = run_id or self._run_id or uuid.uuid4().hex
        # 独立调用（node=None）且无 task_id 时：补发 graph.task，让前端图谱
        # 有任务节点可点（显示任务详情；single_task 场景任务名固定为 "single"）
        if node is None and not task_id:
            self._task_seq = getattr(self, "_task_seq", 0) + 1
            task_id = (f"t{getattr(self, '_round_no', 0)}_{self._task_seq}_"
                       f"{uuid.uuid4().hex[:6]}")
            bus.emit("graph.task", id=task_id, name=name,
                     task_id=task_id, execution_id=execution_id, run_id=run_id,
                     goal=task_md, task_md=task_md, parent="ceo", count=1)
        for attempt in range(3):
            if node is None:
                node = route(task_md, reg=registry)
                if not node:
                    return {"success": False, "error": "无可用节点"}
            self.workflow_log.append({"node": node.id, "task": name,
                                      "status": "dispatched",
                                      "attempt": attempt + 1})
            bus.emit("graph.dispatch", parent="ceo", task=task_id or name,
                     task_id=task_id, execution_id=execution_id, run_id=run_id,
                     node=node.id, mode="session", goal=goal or "", task_md=task_md)
            try:
                result = NODE(node.id).work(task_md, upper="ceo",
                                            task_id=task_id, execution_id=execution_id,
                                            run_id=run_id, workspace=workspace or config.workspace)
            except Exception as e:
                self.workflow_log.append({"node": node.id, "task": name,
                                          "status": "error"})
                return {"success": False, "outcome": "interrupted", "error": str(e),
                        "node_id": node.id, "execution_id": execution_id}

            if result.get("need_clarify"):
                # 智能体间发消息工具：CEO 回答节点的问题，然后重派同一节点
                answer = self._answer_clarify(result)
                if answer is None:
                    return {**result, "success": False, "outcome": "unjudged",
                            "error": "澄清服务不可用，保留任务待恢复"}
                task_md = task_md + f"\n\n【上层节点补充说明】\n{answer}"
                self.workflow_log.append({"node": node.id, "task": name,
                                          "status": "clarified",
                                          "question": str(result.get("question"))[:80]})
                continue
            status = "done" if result.get("success") else "failed"
            self.workflow_log.append({"node": node.id, "task": name,
                                      "status": status})
            return result
        return {"success": False, "error": "澄清后仍失败",
                "node_id": node.id, "task_md": task_md}

    def _dispatch_clone(self, node_id: str, task_md: str, name: str,
                        task_id: str = None, goal: str = None) -> dict:
        """Legacy entry; use the same AGS profile with a fresh independent session."""
        from core.registry import registry
        return self._dispatch_single(task_md, name, registry.get(node_id), task_id, goal,
                                     workspace=config.workspace)

    def _answer_clarify(self, result: dict) -> str:
        """CEO 回答节点的澄清提问（用 brain 模型快速回答）"""
        q = result.get("question", "")
        need = result.get("need_info", [])
        ctx = (f"下层节点 {result.get('node_id')} 对任务提出疑问：{q}\n"
               f"需要信息：{', '.join(need) if need else '无'}\n"
               f"任务书：{result.get('task_md', '')}\n"
               f"用户请求与事实：{json.dumps(self._facts_for(), ensure_ascii=False)}\n"
               f"请以 CEO 身份给出明确、简洁的补充说明，消除节点的疑惑。")
        try:
            # 2026-08-30 P3：复用模块级 CHAT 客户端（此前每次澄清都 new 一个，
            # 高频澄清时重复建连接池浪费）
            if self._clarify_chat is None:
                from utils.chat import CHAT
                self._clarify_chat = CHAT(config.brain_url, config.brain_model,
                                          config.brain_key)
            answer = self._clarify_chat.get_full_response(
                [{"role": "system", "content": "你是 ADGS 系统的 CEO，正在回答下层节点的任务疑问。"},
                 {"role": "user", "content": ctx}], max_tokens=400)
            # get_full_response 失败时返回 "__LLM_ERROR__: ..."（非异常），
            # 直接拼进任务书会把错误文本当成"补充说明"发给节点，须拦截
            if not answer or answer.startswith("__LLM_ERROR__"):
                return None
            return answer
        except Exception:
            return None

    # ================================================================
    # 汇报
    # ================================================================

    def _format_report(self, args: dict) -> str:
        wf = "\n".join(
            f"  [{w['status']}] {w['node']} → {w['task']}"
            for w in self.workflow_log) or "  (无节点流转)"
        # 2026-08-30 修复（P1）：artifacts 模型可能返回字符串（答题卡模式
        # 不保证类型）——字符串逐字符 join 会变乱码，dict 会 TypeError。
        arts = args.get("artifacts", [])
        if isinstance(arts, str):
            arts = [arts]
        art_list = chr(10).join("- " + str(a) for a in arts) or "- 无"
        if args.get("status") == "answered":
            return args.get("summary", "")
        title = {"completed": "任务完成汇报", "blocked": "任务阻塞汇报", "failed": "任务未完成汇报"}.get(args.get("status", "completed"), "任务汇报")
        return f"""════ {title} ════

{args.get('summary', '')}

【工作流程】
{wf}

{args.get('workflow', '')}

【产物】
{art_list}
"""


if __name__ == "__main__":
    print("CEO 模块加载正常")
