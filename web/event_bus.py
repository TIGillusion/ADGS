"""web/event_bus.py — 全局事件总线（ADGS 运行时事件 → SSE 推送）

设计：
- 纯标准库（无 Flask 依赖）：core/ 埋点 import 本模块在 CLI 模式下也安全
- CLI 模式：无订阅者，emit() 仅写入历史缓冲（开销极小，不影响原流程）
- Web 模式：web_app 启动后前端通过 EventSource 订阅，事件实时推送
- 历史环形缓冲：页面刷新后由 /api/state 的 history(tail) 重放最近事件
  （subscribe 不再重放，避免与 /api/state 重放叠加导致前端重复渲染）

事件类型（type 字段）：
  chat.user / chat.ceo_ask / chat.ceo_tool / chat.ceo_report / chat.ceo_finish / chat.ceo_end
  graph.task / graph.dispatch / graph.node_status / graph.child
  node.brain / node.eval / node.delegate
  hermes.start / hermes.line / hermes.end
  evolve.round / system
"""
import json
import queue
import threading
import time
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar

_event_context = ContextVar("adgs_event_context", default={})


@contextmanager
def event_scope(**fields):
    token = _event_context.set({**_event_context.get(), **{k: v for k, v in fields.items() if v is not None}})
    try:
        yield
    finally:
        _event_context.reset(token)

_HISTORY_MAX = 20000   # 历史缓冲：覆盖一次完整任务的全部事件（结构+日志）
_QUEUE_MAX = 2000

# 当前任务会话名（2026-08-30 会话隔离修复）：web_app 任务 runner 启动时
# set_task_session(name)、结束清理；emit 时自动附加 session 字段。
# 前端据此过滤"非当前查看会话"的事件，防止历史会话被其它任务的
# 消息/图谱节点串台（用户实测：查看会话 A 时 B 任务的消息/节点混进来）。
_task_session = None
_task_session_lock = threading.Lock()


def set_task_session(name):
    """任务执行期间标记当前会话（None = 无任务）"""
    global _task_session
    with _task_session_lock:
        _task_session = name


def get_task_session():
    from core.session_context import runtime_session
    scoped = runtime_session.get()
    if scoped:
        return scoped
    with _task_session_lock:
        return _task_session


class EventBus:
    def __init__(self):
        self._subs: list = []
        self._lock = threading.RLock()
        self._history: list = []
        self._seq = 0
        self._db = None

    def configure_store(self, path):
        """Enable durable event cursors only in the owning runtime, never on import."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if self._db:
                self._db.close()
            self._db = sqlite3.connect(path, timeout=15, check_same_thread=False)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
            # Sparse structural index: historical graph loads must not scan terminal output.
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS events_session_interaction ON events "
                "(json_extract(payload,'$.session'), id) WHERE "
                "json_extract(payload,'$.type') GLOB 'graph.*' OR "
                "json_extract(payload,'$.type') GLOB 'node.*' OR "
                "json_extract(payload,'$.type') GLOB 'a2a.*' OR "
                "json_extract(payload,'$.type') GLOB 'chat.*'")
            self._seq = self._db.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]
            rows = self._db.execute("SELECT payload FROM events ORDER BY id DESC LIMIT ?", (_HISTORY_MAX,)).fetchall()
            self._history = [json.loads(row[0]) for row in reversed(rows)]

    @property
    def last_id(self):
        with self._lock:
            return self._seq

    def history_after(self, after=0, limit=1000):
        with self._lock:
            if self._db:
                rows = self._db.execute("SELECT payload FROM events WHERE id>? ORDER BY id LIMIT ?",
                                        (int(after), int(limit))).fetchall()
                return [json.loads(row[0]) for row in rows]
            return [e for e in self._history if e["id"] > int(after)][:limit]

    def execution_events(self, execution_id, limit=2000):
        with self._lock:
            if self._db:
                rows = self._db.execute(
                    "SELECT payload FROM events WHERE json_extract(payload,'$.data.execution_id')=? "
                    "AND json_extract(payload,'$.type') NOT IN ('terminal.output','hermes.line') "
                    "ORDER BY id DESC LIMIT ?", (execution_id, limit)).fetchall()
                return [json.loads(row[0]) for row in reversed(rows)]
            return [e for e in self._history if e["data"].get("execution_id") == execution_id
                    and e["type"] not in ("terminal.output", "hermes.line")][-limit:]

    def session_graph_events(self, session):
        """Replay structural and delegation events even after the in-memory tail expires."""
        prefixes = ("graph.", "node.", "a2a.", "chat.")
        with self._lock:
            if self._db:
                rows = self._db.execute(
                    "SELECT payload FROM events WHERE "
                    "json_extract(payload,'$.session')=? AND ("
                    "json_extract(payload,'$.type') GLOB 'graph.*' OR "
                    "json_extract(payload,'$.type') GLOB 'node.*' OR "
                    "json_extract(payload,'$.type') GLOB 'a2a.*' OR "
                    "json_extract(payload,'$.type') GLOB 'chat.*') ORDER BY id",
                    (session,)).fetchall()
                return [json.loads(row[0]) for row in rows]
            return [e for e in self.history(_HISTORY_MAX) if e.get("session") == session
                    and e["type"].startswith(prefixes)]

    def control_events(self, after=0, session=None, limit=100):
        """Bounded structural events for controllers; terminal bytes use attempt replay."""
        with self._lock:
            if self._db:
                predicate="(json_extract(payload,'$.type') GLOB 'graph.*' OR json_extract(payload,'$.type') GLOB 'node.*' OR json_extract(payload,'$.type') GLOB 'a2a.*' OR json_extract(payload,'$.type') GLOB 'chat.*')"
                clause=" AND json_extract(payload,'$.session')=?" if session else ''
                params=(int(after),session,int(limit)) if session else (int(after),int(limit))
                rows=self._db.execute('SELECT payload FROM events WHERE id>? AND '+predicate+clause+' ORDER BY id LIMIT ?',params).fetchall()
                return [json.loads(row[0]) for row in rows]
            return [event for event in self._history if event['id']>after and
                    (not session or event.get('session')==session) and event['type'].startswith(('graph.','node.','a2a.','chat.'))][:limit]

    # ---------------- 订阅 / 退订 ----------------

    def subscribe(self) -> queue.Queue:
        """返回一个事件队列（SSE 消费者用）

        不再重放历史：页面刷新后的历史重建由前端从 /api/state 的
        bus.history(300) 单路径重放；SSE 队列只承载订阅之后的实时事件。
        若 subscribe 也重放，则与 /api/state 重放叠加，页面加载时
        所有气泡/系统消息会渲染两遍（实测双聊天气泡根因之一）；
        且 SSE 断线重连会再次重放同一批历史，产生重复。
        """
        q = queue.Queue(maxsize=_QUEUE_MAX)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._lock:
            try:
                self._subs.remove(q)
            except ValueError:
                pass

    # ---------------- 生产 ----------------

    def emit(self, type: str, **data):
        """发布事件：历史缓冲 + 广播到所有订阅者（慢消费者丢弃不阻塞）"""
        data = {**_event_context.get(), **data}
        ev = {"type": type, "ts": time.time(), "data": data}
        sess = data.pop("session", None) or get_task_session()
        if sess:
            ev["session"] = sess  # 前端会话隔离用（2026-08-30）
        with self._lock:
            self._seq += 1
            ev["id"] = self._seq
            if self._db:
                # sqlite TEXT is strict UTF-8; a dirty event line must not
                # crash the emitting worker thread mid-dispatch.
                from core.contracts import utf8_safe
                self._db.execute("INSERT INTO events VALUES (?,?)", (self._seq, utf8_safe(json.dumps(ev, ensure_ascii=False))))
                self._db.commit()
            self._history.append(ev)
            if len(self._history) > _HISTORY_MAX:
                self._history = self._history[-_HISTORY_MAX:]
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(ev)
            except queue.Full:
                # Consumers recover missing IDs from history_after; execution never waits for a browser.
                pass
        return ev

    def history(self, tail: int = 300) -> list:
        with self._lock:
            return list(self._history[-tail:])
    def clear(self):
        with self._lock:
            self._history.clear()


bus = EventBus()


def configure_event_store(path):
    bus.configure_store(path)
