"""Durable, independently observed Hermes attempts. Terminal bytes are presentation,
not a completion signal. Each attempt owns its PTY and process-tree lifetime.
"""
from __future__ import annotations

import codecs
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import psutil
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_ID = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_cancellations: dict[str, threading.Event] = {}
_cancel_lock = threading.Lock()


def _provider_failure_reason(text: str) -> str:
    """Return a terminal provider-failure reason for repeated explicit errors."""
    recent = _ANSI.sub("", str(text or "")).lower()
    compression = recent.count("compression summary failed")
    admission = recent.count("inference request expired while waiting for admission")
    connection = recent.count("internalservererror") + recent.count("connection error")
    # 2026-10-22 实测新增话术:opencode 网关长时间无响应时,hermes 逐次
    # 报 "No response from provider ... Reconnecting",最终宣告
    # "Provider has been unresponsive ... retrying won't help" 并 exit 1。
    unresponsive = recent.count("provider has been unresponsive")
    rejected = recent.count("retrying won't help")
    stale = recent.count("no response from provider")
    http_failure = re.search(r"\bhttp\s+(401|403|408|413|422|429|5\d\d)\b", recent)
    if http_failure and any(marker in recent for marker in (
            "api failed after", "final error:", "server error on all", "rate limit on all")):
        return f"模型服务请求失败（HTTP {http_failure[1]}），重试已耗尽；原会话已保留"
    if ((compression >= 2 and connection >= 1) or admission >= 2 or connection >= 3
            or unresponsive >= 2 or rejected >= 1 or stale >= 3):
        if unresponsive or rejected or stale:
            return "Worker 模型服务连续无响应（provider stale/unresponsive），Hermes 放弃本次请求"
        return "Provider repeatedly failed during Hermes request; stopped without retrying the task"
    return ""


def _refine_exit_failure(exit_code, receipt_path, observed_session, tail_text):
    """Classify a non-zero Hermes exit using the receipt, then the terminal tail.

    provider 无响应类退出不写 turn-receipt；直接归为 process_error 会让
    CEO/用户只看到 "Hermes exited with code 1"（2026-10-22 实测 blocked 的
    报告根因）。尾部文本能还原真实类别。
    """
    if exit_code == 1:
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if (receipt.get("session_id") == observed_session
                    and receipt.get("termination_reason") == "iteration_limit"):
                return "iteration_limit", "Hermes turn iteration budget exhausted; continue the existing session"
        except (OSError, ValueError, AttributeError):
            pass
        provider = _provider_failure_reason(tail_text)
        if provider:
            return "provider_error", f"{provider}（Hermes exited with code 1）"
    return "process_error", f"Hermes exited with code {exit_code}"


class AttemptInterrupted(RuntimeError):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def _identifier(value=None):
    value = str(value or uuid.uuid4().hex)
    if not _ID.fullmatch(value) or value in {".", ".."}:
        raise ValueError("Invalid execution/attempt identifier")
    return value


class TerminalStore:
    def __init__(self, root=None):
        default = Path(os.environ.get("ADGS_STATE_DIR", str(ROOT / ".adgs_runtime")))
        self.root = Path(root or default)
        self._lock = threading.RLock()

    @contextmanager
    def _connect(self):
        self.root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.root / "terminals.sqlite", timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS attempts (attempt_id TEXT PRIMARY KEY, execution_id TEXT NOT NULL, run_id TEXT, task_id TEXT, profile TEXT NOT NULL, session_id TEXT, status TEXT NOT NULL, started REAL NOT NULL, ended REAL, last_activity REAL, cols INTEGER, rows INTEGER, exit_code INTEGER, error TEXT, termination_reason TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS records (attempt_id TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, ts REAL NOT NULL, PRIMARY KEY(attempt_id,seq))")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def paths(self, execution_id, attempt_id):
        directory = self.root / "executions" / _identifier(execution_id) / _identifier(attempt_id)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def create(self, **meta):
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute("INSERT INTO attempts (attempt_id,execution_id,run_id,task_id,profile,session_id,status,started,last_activity,cols,rows) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (meta["attempt_id"], meta["execution_id"], meta.get("run_id"), meta.get("task_id"), meta["profile"], meta.get("session_id"), "starting", now, now, 120, 36))
        self.append(meta["attempt_id"], "resize", {"cols": 120, "rows": 36})

    def update(self, attempt_id, **fields):
        allowed = {"session_id", "status", "ended", "last_activity", "cols", "rows", "exit_code", "error", "termination_reason"}
        if not fields or not set(fields) <= allowed:
            raise ValueError("Invalid terminal metadata update")
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE attempts SET " + ",".join(f"{key}=?" for key in fields) + " WHERE attempt_id=?", (*fields.values(), attempt_id))

    def append(self, attempt_id, kind, data):
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            seq = conn.execute("SELECT COALESCE(MAX(seq),0)+1 FROM records WHERE attempt_id=?", (attempt_id,)).fetchone()[0]
            from core.contracts import utf8_safe
            conn.execute("INSERT INTO records VALUES (?,?,?,?,?)", (attempt_id, seq, kind, utf8_safe(json.dumps(data, ensure_ascii=False)), time.time()))
        return seq

    def get_attempt(self, attempt_id):
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT a.*, COALESCE((SELECT MAX(seq) FROM records WHERE attempt_id=a.attempt_id),0) AS last_seq FROM attempts a WHERE attempt_id=?", (attempt_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["node"] = result["profile"]
        return result

    def list_attempts(self, execution_id=None, run_id=None, task_id=None):
        filters = {k: v for k, v in {"execution_id": execution_id, "run_id": run_id, "task_id": task_id}.items() if v is not None}
        where = " WHERE " + " AND ".join(f"{key}=?" for key in filters) if filters else ""
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT attempt_id FROM attempts" + where + " ORDER BY started", tuple(filters.values())).fetchall()
        return [self.get_attempt(row[0]) for row in rows]

    def get_execution(self, execution_id):
        attempts = self.list_attempts(execution_id=execution_id)
        if not attempts:
            return None
        return {**attempts[-1], "attempts": attempts}

    def read_records(self, attempt_id, after=0, limit=256):
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT seq,kind,data,ts FROM records WHERE attempt_id=? AND seq>? ORDER BY seq LIMIT ?", (attempt_id, max(0, int(after)), min(1000, max(1, int(limit))))).fetchall()
        return [{"seq": row[0], "kind": row[1], "data": json.loads(row[2]), "ts": row[3], "attempt_id": attempt_id} for row in rows]

    def latest_seq(self, attempt_id):
        return (self.get_attempt(attempt_id) or {}).get("last_seq", 0)

    def read_output(self, attempt_id, offset=0, limit=65536):
        meta = self.get_attempt(attempt_id)
        if not meta:
            raise KeyError(attempt_id)
        path = self.paths(meta["execution_id"], attempt_id) / "output.vt"
        offset = max(0, int(offset))
        data = b""
        if path.exists():
            with path.open("rb") as stream:
                stream.seek(offset)
                data = stream.read(min(262144, max(4, int(limit))))
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            if exc.end == len(data):
                data = data[:exc.start]
            text = data.decode("utf-8", errors="replace")
        return {"data": text, "next_offset": offset + len(data), "done": meta["status"] not in {"starting", "running", "stopping"}}


terminal_store = TerminalStore()


def cancel_attempt(attempt_id):
    with _cancel_lock:
        event = _cancellations.get(attempt_id)
    if event is None:
        return False
    event.set()
    return True


class SessionLease:
    """Only the same stored session is exclusive; different sessions share profiles."""
    def __init__(self, profile_home, session_id):
        import hashlib
        directory = Path(profile_home) / "runtime" / "adgs-session-locks"
        directory.mkdir(parents=True, exist_ok=True)
        self.file = (directory / (hashlib.sha256(session_id.encode()).hexdigest() + ".lock")).open("a+b")
        if self.file.tell() == 0:
            self.file.write(b" ")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("SESSION_NOT_OWNED: session already executing")

    def close(self):
        if self.file.closed:
            return
        self.file.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        self.file.close()


class WindowsJob:
    """A gated wrapper waits until it is assigned; no Hermes grandchildren escape."""
    def __init__(self):
        import win32job
        self.api = win32job
        self.handle = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(self.handle, win32job.JobObjectExtendedLimitInformation)
        info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(self.handle, win32job.JobObjectExtendedLimitInformation, info)

    def assign(self, pid):
        import win32api
        import win32con
        handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, pid)
        try:
            self.api.AssignProcessToJobObject(self.handle, handle)
        finally:
            handle.Close()

    def terminate(self):
        self.api.TerminateJobObject(self.handle, 130)

    def close(self):
        if self.handle is not None:
            self.handle.Close()
            self.handle = None


class ConPtyProcess:
    """Public pywinpty PTY API without PtyProcess's blocking socket relay.

    pywinpty 2.0.15 PtyProcess.isalive() marks its socket wrapper closed before
    close() can free it. A nonblocking native read gives teardown a bounded path.
    """
    def __init__(self, argv, cwd, env):
        from winpty import PTY
        self._pty = PTY(120, 36, backend=0)
        executable = shutil.which(argv[0]) or argv[0]
        block = "\0".join(f"{key}={value}" for key, value in env.items()) + "\0"
        self._pty.spawn(executable, cwd=str(cwd or os.getcwd()), env=block,
                        cmdline=" " + subprocess.list2cmdline(argv[1:]))
        self.pid = self._pty.pid
        self._empty_after_exit = None

    def isalive(self):
        return self._pty is not None and self._pty.isalive()

    @property
    def exitstatus(self):
        return self._pty.get_exitstatus()

    def read(self, size=65536):
        data = self._pty.read(blocking=False)
        if data:
            self._empty_after_exit = None
            return data
        # pywinpty's iseof probes can wait for its internal pipe reader after
        # child exit. Process liveness + an empty nonblocking read is sufficient.
        if not self.isalive():
            # ConPTY's reader can deliver a final frame just after owner exit.
            # A short bounded quiet period drains it without blocking EOF probes.
            now = time.monotonic()
            if self._empty_after_exit is None:
                self._empty_after_exit = now
            elif now - self._empty_after_exit >= 0.15:
                raise EOFError()
        time.sleep(0.02)
        return ""

    def write(self, text):
        return self._pty.write(text)

    def setwinsize(self, rows, cols):
        self._pty.set_size(cols, rows)

    def close(self, force=True):
        if self.isalive() and force:
            os.kill(self.pid, signal.SIGTERM)
        self._pty = None


def hermes_installation():
    explicit = os.environ.get("ADGS_HERMES_PYTHON")
    executable = Path(shutil.which("hermes") or "")
    candidates = [Path(explicit)] if explicit else []
    if executable.name:
        candidates += [executable.parent / ("python.exe" if os.name == "nt" else "python"), executable.parent.parent / "bin" / "python"]
    for python in candidates:
        if python.is_file():
            source = python.parent.parent.parent
            if (source / "hermes_cli" / "main.py").is_file():
                return python, source
    raise RuntimeError("Installed Hermes source/venv not found; set ADGS_HERMES_PYTHON")


FROZEN_HERMES = ROOT / "runtime" / "hermes" / "hermes-agent-cn-runtime-win32-x64.exe"


def hermes_frozen_exe():
    """项目内打包版 hermes（PyInstaller 冻结 CLI，相对路径解析）。

    兼容未启用源码包装器的运行环境。协调者角色限制和 CEO 原生工具桥接
    使用源码包装器。注意：冻结 exe 直接执行 CLI，不经过 hermes_worker
    包装层（provider_compat / resume_projection / TurnReceipt 补丁不生效）
    ——profile 已自洽（显式 providers 段），2026-10-25 实测真实调用通过。
    """
    return FROZEN_HERMES if FROZEN_HERMES.is_file() else None


def hermes_source_available():
    try:
        hermes_installation()
        return True
    except RuntimeError:
        return False


def _emit(name, **data):
    from web.event_bus import bus
    bus.emit(name, **data)


def _activity_is_progress(description):
    # Hermes refreshes these labels while no provider data has arrived. They
    # prove liveness, but must not indefinitely reset our inactivity watchdog.
    text = str(description or "").strip().lower()
    text = re.sub(r"^[^\w]+", "", text)
    return bool(text) and not re.match(
        r"^(?:waiting|still waiting|retrying|reconnecting|local model loading|executing|sequential tool running)\b", text)


def _session_snapshot(home, session_id, cursor, activity_details=None):
    path = Path(home) / "state.db"
    if not path.exists() or not session_id:
        return session_id, cursor, None, []
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.1) as conn:
        conn.row_factory = sqlite3.Row
        # Compression can rotate during a running turn before the breadcrumb sync.
        seen = set()
        while session_id not in seen:
            seen.add(session_id)
            parent = conn.execute("SELECT end_reason FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not parent or "compress" not in str(parent[0] or "").lower():
                break
            children = conn.execute("SELECT id FROM sessions WHERE parent_session_id=?", (session_id,)).fetchall()
            if len(children) != 1:
                break
            session_id = children[0][0]
            cursor = 0
        columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
        activity = None
        if "last_activity_at" in columns:
            row = conn.execute("SELECT last_activity_at FROM sessions WHERE id=?", (session_id,)).fetchone()
            activity = row[0] if row else None
            fields = [name for name in ("last_activity_description", "last_activity_provenance") if name in columns]
            if activity_details is not None and fields:
                row = conn.execute("SELECT " + ",".join(fields) + " FROM sessions WHERE id=?", (session_id,)).fetchone()
                if row:
                    activity_details.update(dict(row))
        rows = conn.execute("SELECT id,role,content,tool_call_id,tool_calls,tool_name,timestamp,finish_reason FROM messages WHERE session_id=? AND id>? ORDER BY id LIMIT 128", (session_id, cursor)).fetchall()
    messages = [dict(row) for row in rows]
    return session_id, messages[-1]["id"] if messages else cursor, activity, messages


def _validate_resume(home, session_id):
    path = Path(home) / "state.db"
    if not path.is_file():
        raise AttemptInterrupted("session_missing", "No saved Hermes session in this profile")
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.5) as conn:
        if not conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
            raise AttemptInterrupted("session_missing", "Requested Hermes session does not belong to this profile")


def _publish_recovery_artifacts(staging, destination):
    """Publish new files only, after the scoped worker and its children exit."""
    if staging.resolve() != staging or destination.resolve() != destination:
        raise RuntimeError("恢复产物根目录包含重解析点")
    published = []
    for source in sorted(staging.rglob("*"), key=lambda p: (p.name.casefold() == "deliverables.txt", p)):
        if source.is_symlink() or source.is_junction():
            raise RuntimeError("隔离产物不能包含符号链接或重解析目录")
        if not source.is_file():
            continue
        relative = source.relative_to(staging)
        if source.stat().st_size > 16 * 1024 * 1024 or len(published) >= 128:
            raise RuntimeError("隔离产物超过安全发布上限")
        target = destination / relative
        if target.resolve() != target:
            raise RuntimeError("目标产物路径包含重解析目录")
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = source.read_bytes()
        if source.name.casefold() == "deliverables.txt":
            text = payload.decode("utf-8")
            for old, new in ((str(staging), str(destination)), (staging.as_posix(), destination.as_posix())):
                text = text.replace(old, new)
            payload = text.encode("utf-8")
        if target.is_file() and target.read_bytes() == payload:
            published.append(str(target))
            continue
        with target.open("xb") as handle:
            handle.write(payload)
        published.append(str(target))
    return published


def _sync_recovery_database(source, destination, allowed_sessions):
    """Never replace a real profile database that also owns other sessions."""
    if source.resolve() != source or destination.resolve() != destination:
        raise RuntimeError("恢复会话库包含重解析点")
    if destination.is_file():
        with sqlite3.connect(destination.as_uri() + "?mode=ro", uri=True, timeout=5) as existing:
            if any(row[0] not in allowed_sessions for row in existing.execute("SELECT id FROM sessions")):
                return False
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=5) as saved, sqlite3.connect(
            destination, timeout=5) as target:
        saved.backup(target)
    return True


def run_hermes(profile, instruction, model=None, cwd=None, timeout=3600,
               inactivity_timeout=1800, session_id=None, execution_id=None,
               attempt_id=None, run_id=None, task_id=None, live_log=None,
               backend=None, store=None, cancel=None, control_tools=None):
    store = store or terminal_store
    from core.execution_store import WORKER_HOLD_REASON, recovery_grant, worker_launch_on_hold
    held = worker_launch_on_hold(store.root)
    grant = recovery_grant(execution_id, state_dir=store.root) if held else None
    if held and not grant:
        raise AttemptInterrupted("worker_safety_hold", WORKER_HOLD_REASON)
    execution_id, attempt_id = _identifier(execution_id), _identifier(attempt_id)
    original_profile = ROOT / ".hermes" / "profiles" / profile
    hermes_home = ROOT / ".hermes"
    profile_home = original_profile
    published = []
    isolated_session = False
    if grant:
        workspace = Path(grant["workspace"]).resolve(strict=True)
        artifact_root = (workspace / ".adgs_artifacts").resolve(strict=True)
        output_dir = Path(grant["output_dir"]).resolve(strict=True)
        if (profile != grant["profile"] or not cwd or Path(cwd).resolve() != workspace
                or not artifact_root.is_relative_to(workspace)
                or not output_dir.is_dir() or output_dir == artifact_root
                or not output_dir.is_relative_to(artifact_root)
                or output_dir != Path(grant["output_dir"]).absolute()):
            raise AttemptInterrupted("worker_safety_hold", "事故恢复许可与执行环境不一致")
        hermes_home = Path(grant.get("hermes_home") or "").resolve(strict=True)
        if hermes_home.parent != store.root.resolve() or not hermes_home.name.startswith("recovery-worker-"):
            raise AttemptInterrupted("worker_safety_hold", "事故恢复副本必须位于专用临时目录")
        profile_home = hermes_home / "profiles" / profile
        staging = hermes_home / "artifacts"
        if not (profile_home / "config.yaml").is_file() or not staging.is_dir():
            raise AttemptInterrupted("worker_safety_hold", "事故恢复副本尚未就绪")
        if isinstance(grant.get("worker_model"), str):
            model = grant["worker_model"]
        instruction += (
            "\n\n【本轮事故恢复写入限制】此前成果和工作区为只读。"
            f"所有新产物（含完整执行报告和清单）写入隔离目录 {staging}。"
            f"平台结束后将新文件发布到原任务产物目录 {output_dir}。"
            "不要直接写原任务书中的任何位置。清单逐行写隔离目录内的文件绝对路径。"
            "禁止任何删除、修改已有文件、改动 profile 或派发其他任务。")
    ids = {"execution_id": execution_id, "attempt_id": attempt_id, "run_id": run_id, "task_id": task_id, "profile": profile, "node": profile}
    store.create(**{k: v for k, v in ids.items() if k != "node"}, session_id=session_id)
    directory = store.paths(execution_id, attempt_id)
    task_file = directory / "task.md"
    task_workspace = str(Path(cwd).resolve()) if cwd else None
    if task_workspace:
        instruction = (f"【ADGS 本执行工作区】\n{task_workspace}\n"
                       "本任务中的相对路径均以此目录为基准。其他会话、长期记忆或平台安装目录不能替代此工作区。"
                       "不要为了寻找任务目录切换到 ADGS 平台根目录。\n\n" + instruction)
    task_file.write_text(instruction, encoding="utf-8")
    output_file = directory / "output.vt"
    output_file.touch()
    cancellation = cancel if cancel is not None else threading.Event()
    with _cancel_lock:
        _cancellations[attempt_id] = cancellation
    backend = backend or os.environ.get("ADGS_HERMES_BACKEND") or ("conpty" if os.name == "nt" else "pipe")
    if grant:
        # The low-integrity child inherits byte pipes. A console handle belongs
        # to the medium-integrity helper's ConPTY and is not usable by that child.
        backend = "pipe"
    proc = job = lease = None
    session_leases = {}
    threads = []
    chunks = []
    output_lock = threading.Lock()
    last_progress = [time.monotonic()]
    last_database_activity = 0
    activity_details = {}
    state_cursor = 0
    process_samples = {}
    observed_tools = {}
    final_response = ""
    reason, error, exit_code = "completed", "", -1
    started = time.monotonic()
    attempt_started_at = time.time()
    observed_session = session_id
    terminal_identity = f"adgs-{attempt_id}"
    breadcrumb = profile_home / "terminal-sessions" / f"term_session_id-{terminal_identity}"
    gate = directory / "launch.gate"
    receipt = directory / "turn-receipt.json"
    decision = directory / "ceo-decision.json"
    coordinator_flag = directory / "coordinator.flag"

    def feed(text):
        # pywinpty can hand out split UTF-16 surrogate units at read
        # boundaries; sanitize before any strict UTF-8 encode below, or the
        # drain thread dies and silently stops capturing terminal output.
        from core.contracts import utf8_safe
        text = utf8_safe(text)
        # Python codepoints use at most four UTF-8 bytes; bounded records make
        # replay/backpressure predictable while xterm retains ANSI state.
        for offset in range(0, len(text), 16384):
            feed_chunk(text[offset:offset+16384])

    def feed_chunk(text):
        if not text:
            return
        with output_lock:
            chunks.append(text)
            with output_file.open("ab") as stream:
                stream.write(text.encode("utf-8"))
            seq = store.append(attempt_id, "data", {"text": text})
            # A spinner redraw is display activity, not proof of task progress.
            visible = _ANSI.sub("", text).strip("\r\n \b")
            if visible and "\n" in text and _activity_is_progress(visible):
                last_progress[0] = time.monotonic()
        _emit("terminal.output", **ids, seq=seq, data=text)
        _emit("hermes.line", **ids, line=text, err=False)

    try:
        if cancellation.is_set():
            raise AttemptInterrupted("cancelled", "Cancelled before launch")
        if backend not in {"conpty", "pipe"}:
            raise RuntimeError(f"Unsupported Hermes backend: {backend}")
        if session_id:
            _validate_resume(profile_home, session_id)
            lease = SessionLease(profile_home, session_id)
            session_leases[session_id] = lease
        # Recovery needs the source wrapper's launch gate and turn receipts.
        frozen = None if grant or control_tools or (os.environ.get("ADGS_HERMES_ROLE_GUARD", "1") == "1"
            and hermes_source_available()) else hermes_frozen_exe()
        if frozen:
            # 打包版：直接执行冻结 CLI（worker 包装层不适用，见 hermes_frozen_exe）
            cmd = [str(frozen), "--yolo", "--profile", profile]
            if model:
                cmd += ["-m", model]
        else:
            python, source = hermes_installation()
            cmd = [str(python), "-u", str(ROOT / "core" / "hermes_worker.py"), "--source", str(source), "--gate", str(gate), "--", "--yolo", "--profile", profile]
            if model:
                cmd += ["-m", model]
        cmd += ["chat", "--oneshot", "--query-file", str(task_file)]
        if control_tools and profile == "ceo":
            cmd += ["--skills", "adgs-ceo"]
        if session_id:
            cmd += ["--resume", session_id]
        if task_workspace and not frozen:
            cmd += ["--in", task_workspace, "--no-restore-cwd"]
        env = os.environ.copy()
        env.pop("PYTHONSTARTUP", None)
        for key in ("ZELLIJ_PANE_ID", "TMUX_PANE", "KITTY_WINDOW_ID", "WEZTERM_PANE"):
            env.pop(key, None)
        env.update({"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "TERM": "xterm-256color", "TERM_SESSION_ID": terminal_identity, "ADGS_HERMES_RUN": "1", "ADGS_HERMES_RECEIPT": str(directory / "turn-receipt.json"), "HERMES_HOME": str(hermes_home)})
        if task_workspace:
            env.update({"HERMES_CWD": task_workspace, "TERMINAL_CWD": task_workspace,
                        "HERMES_DISABLE_CWD_FOLLOW": "1"})
        env["ADGS_COORDINATOR_FLAG"] = str(coordinator_flag)
        if control_tools:
            tools_file = directory / "ceo-tools.json"
            tools_file.write_text(json.dumps(control_tools, ensure_ascii=False), encoding="utf-8")
            env["ADGS_CONTROL_TOOLS"] = str(tools_file)
            env["ADGS_CONTROL_DECISION"] = str(decision)
        else:
            from core.execution_store import execution_store
            if (execution_store.get(execution_id) or {}).get("coordinator"):
                coordinator_flag.touch()
        if grant:
            scratch = hermes_home / "tmp" / attempt_id
            scratch.mkdir(parents=True, exist_ok=False)
            if scratch.resolve() != scratch:
                raise RuntimeError("恢复临时目录包含重解析点")
            receipt = scratch / receipt.name
            env.update({"TEMP": str(scratch), "TMP": str(scratch), "TMPDIR": str(scratch),
                        "ADGS_HERMES_RECEIPT": str(receipt), "PYTHONDONTWRITEBYTECODE": "1"})
            for key in ("ADGS_CONTROL_TOKEN", "ADGS_RUNTIME_TOKEN", "ADGS_RUNTIME_NONCE"):
                env.pop(key, None)
            cmd = [sys.executable, "-u", str(ROOT / "core" / "windows_worker.py"), str(gate), *cmd]
        if os.name == "nt":
            job = WindowsJob()
        if backend == "conpty":
            if os.name != "nt":
                raise RuntimeError("ConPTY requires Windows")
            env["PYWINPTY_BACKEND"] = "0"
            proc = ConPtyProcess(cmd, cwd, env)
            def drain_pty():
                try:
                    while True:
                        feed(proc.read(65536))
                except EOFError:
                    pass
                except Exception as exc:
                    _emit("terminal.diagnostic", **ids, message=f"Terminal read: {type(exc).__name__}")
            threads = [threading.Thread(target=drain_pty, daemon=True, name=f"adgs-terminal-{attempt_id}")]
            alive = proc.isalive
        else:
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL if grant else None,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=8192,
                                    creationflags=flags, start_new_session=os.name != "nt")
            def drain_pipe():
                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                while True:
                    data = proc.stdout.read1(65536)
                    if not data:
                        feed(decoder.decode(b"", final=True))
                        break
                    feed(decoder.decode(data))
            threads = [threading.Thread(target=drain_pipe, daemon=True, name=f"adgs-terminal-{attempt_id}")]
            alive = lambda: proc.poll() is None
        for thread in threads:
            thread.start()
        if job:
            job.assign(proc.pid)
        if cancellation.is_set():
            raise AttemptInterrupted("cancelled", "Cancelled before Hermes initialization")
        gate.touch()  # Wrapper cannot import Hermes/create children before assignment.
        store.update(attempt_id, status="running")
        _emit("terminal.started", **ids, backend=backend, cols=120, rows=36, pid=proc.pid)
        last_poll = 0
        while alive():
            now = time.monotonic()
            if control_tools and decision.is_file():
                reason, error = "decision_ready", ""
                break
            if now - last_poll >= 1:
                last_poll = now
                # Long-running tool processes may make progress without printing.
                # Ignore the Hermes owner and console/spinner processes.
                try:
                    current_samples = {}
                    for child in psutil.Process(proc.pid).children(recursive=True):
                        if child.name().lower() in {"conhost.exe", "openconsole.exe"}:
                            continue
                        cpu = child.cpu_times()
                        io = child.io_counters()
                        sample = (cpu.user + cpu.system, io.read_bytes, io.write_bytes)
                        identity = (child.pid, child.create_time())
                        previous = process_samples.get(identity)
                        if previous and any(value > old for value, old in zip(sample, previous)):
                            last_progress[0] = now
                        current_samples[identity] = sample
                    process_samples = current_samples
                except (OSError, psutil.Error):
                    pass
                try:
                    # The CLI breadcrumb can retain the pre-compression ID.
                    # Once known, follow SessionDB's lineage rather than resetting
                    # to that ancestor and replaying all child messages each poll.
                    if breadcrumb.exists() and not observed_session:
                        crumb = json.loads(breadcrumb.read_text(encoding="utf-8"))
                        observed_session = str(crumb.get("session_id") or observed_session or "") or None
                    sid, state_cursor, activity, messages = _session_snapshot(profile_home, observed_session, state_cursor, activity_details)
                    if sid != observed_session:
                        observed_session = sid
                    if observed_session and observed_session not in session_leases:
                        # New and compression-created sessions need the same
                        # guard as resumes once their early ID is known.
                        session_leases[observed_session] = SessionLease(profile_home, observed_session)
                    if activity and activity >= attempt_started_at and activity > last_database_activity:
                        last_database_activity = activity
                        if _activity_is_progress(activity_details.get("last_activity_description")):
                            last_progress[0] = now
                    for message in messages:
                        # Resumes and compressed sessions contain prior turns.
                        # Their old final answer is not this attempt's result.
                        if (message.get("timestamp") or 0) < attempt_started_at:
                            continue
                        last_progress[0] = now
                        _emit("hermes.message", **ids, session_id=observed_session, message=message)
                        calls = message.get("tool_calls") or []
                        if isinstance(calls, str):
                            try:
                                calls = json.loads(calls)
                            except ValueError:
                                calls = []
                        for call in calls if isinstance(calls, list) else []:
                            function = call.get("function") or {}
                            name = function.get("name") or call.get("name") or "tool"
                            observed_tools[call.get("id")] = name
                            _emit("tool.start", **ids, tool=name, tool_call_id=call.get("id"))
                        if message.get("role") == "tool":
                            name = message.get("tool_name") or observed_tools.get(message.get("tool_call_id"), "tool")
                            _emit("tool.end", **ids, tool=name, tool_call_id=message.get("tool_call_id"),
                                  result_preview=str(message.get("content") or "")[:500])
                        if message["role"] == "assistant" and message["content"] and not message["tool_calls"]:
                            final_response = message["content"]
                    if observed_session:
                        store.update(attempt_id, session_id=observed_session, last_activity=time.time() - (now - last_progress[0]))
                except (OSError, ValueError, sqlite3.Error):
                    pass  # Observation failure is not a task failure or activity heartbeat.
                _emit("terminal.activity", **ids, session_id=observed_session, idle_seconds=int(now-last_progress[0]), elapsed_seconds=int(now-started), source="session_db" if last_database_activity else "terminal", process_alive=True,
                      activity_description=activity_details.get("last_activity_description"),
                      activity_provenance=activity_details.get("last_activity_provenance"))
            if cancellation.is_set():
                reason, error = "cancelled", "Cancelled by operator"
                break
            if timeout is not None and now - started > timeout:
                reason, error = "wall_timeout", f"Wall timeout ({timeout}s)"
                break
            from core.delegation_runtime import execution_is_waiting
            waiting_for_children = execution_is_waiting(execution_id)
            if waiting_for_children:
                last_progress[0] = now
            if not waiting_for_children and now - last_progress[0] > inactivity_timeout:
                reason, error = "inactivity_timeout", f"No observed task activity ({inactivity_timeout}s)"
                break
            # Detect actual unattended startup prompts rather than spending a task timeout.
            with output_lock:
                tail = _ANSI.sub("", "".join(chunks[-8:]))
            # Hermes can remain alive while its context-compression retry loop
            # repeatedly receives a provider failure.  Treat two explicit,
            # recent provider failures as terminal infrastructure evidence;
            # otherwise a single transient error could strand an execution
            # until the full wall/inactivity timeout.
            provider_error = _provider_failure_reason(tail)
            if provider_error:
                reason = "provider_error"
                error = provider_error
                break
            if any(prompt in tail for prompt in ("Allow this hook to run?", "Use this model for this invocation?", "Set up a provider now?")):
                reason, error = "input_required", "Hermes startup requires operator input"
                break
            time.sleep(0.2)
        if reason == "decision_ready":
            if job:
                job.terminate()
            elif proc is not None:
                proc.terminate()
        elif reason != "completed":
            store.update(attempt_id, status="stopping")
            try:
                if backend == "conpty":
                    proc.write("\x03")
                elif os.name == "nt":
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    os.killpg(proc.pid, signal.SIGINT)
            except (OSError, EOFError):
                pass
            grace = time.monotonic() + 3
            while alive() and time.monotonic() < grace:
                time.sleep(0.1)
            if job:
                job.terminate()
            elif alive():
                os.killpg(proc.pid, signal.SIGKILL)
        _emit("terminal.stage", **ids, phase="process_exited", elapsed_seconds=round(time.monotonic()-started, 3))
        exit_code = int(proc.exitstatus if backend == "conpty" else proc.wait(timeout=5))
        if reason == "decision_ready":
            reason, exit_code = "completed", 0
        if job:
            job.terminate()
        _emit("terminal.stage", **ids, phase="tree_reaped", elapsed_seconds=round(time.monotonic()-started, 3))
        for thread in threads:
            thread.join(2)
        _emit("terminal.stage", **ids, phase="output_drained", elapsed_seconds=round(time.monotonic()-started, 3))
        # A final DB append may occur between the last monitor tick and process exit.
        try:
            # Short-lived processes can write their ID and exit before a poll.
            if not observed_session and breadcrumb.exists():
                crumb = json.loads(breadcrumb.read_text(encoding="utf-8"))
                observed_session = str(crumb.get("session_id") or "") or None
            observed_session, state_cursor, _, messages = _session_snapshot(profile_home, observed_session, state_cursor)
            for message in messages:
                if (message.get("timestamp") or 0) < attempt_started_at:
                    continue
                if message["role"] == "assistant" and message["content"] and not message["tool_calls"]:
                    final_response = message["content"]
        except (OSError, ValueError, sqlite3.Error):
            pass
        if reason == "completed" and exit_code != 0:
            tail_text = ""
            try:
                tail_text = output_file.read_bytes()[-16384:].decode("utf-8", "replace")
            except OSError:
                pass
            reason, error = _refine_exit_failure(exit_code, receipt, observed_session, tail_text)
    except Exception as exc:
        reason, error = getattr(exc, "reason", "runtime_error"), str(exc)
        if grant and proc is None and reason == "runtime_error":
            reason = "worker_preflight"
        if "SESSION_NOT_OWNED" in error:
            reason = "session_busy"
    finally:
        # Reap ALL descendants before releasing an execution slot, including after
        # a normal CLI exit that left a background tool process running.
        if job:
            try:
                job.terminate()
            except Exception:
                pass
            finally:
                job.close()
            # Assignment can fail before the gated wrapper enters the job.
            # The gate prevents descendants; explicitly reap that owner too.
            if proc is not None and backend == "pipe" and proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        elif proc is not None and backend == "pipe":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        if proc is not None and backend == "conpty":
            try:
                proc.close(force=True)
            except Exception:
                pass
        _emit("terminal.stage", **ids, phase="terminal_closed", elapsed_seconds=round(time.monotonic()-started, 3))
        for thread in threads:
            thread.join(2)
        for lease in session_leases.values():
            lease.close()
        if grant and observed_session and (profile_home / "state.db").is_file():
            # Only the session database returns to the original profile. The
            # restricted process cannot change the real profile configuration.
            try:
                source_db = profile_home / "state.db"
                _validate_resume(profile_home, observed_session)
                owned = {attempt["session_id"] for attempt in store.list_attempts(execution_id=execution_id)
                         if attempt.get("session_id")}
                isolated_session = not _sync_recovery_database(
                    source_db, original_profile / "state.db", owned | {observed_session})
            except (OSError, RuntimeError, sqlite3.Error):
                reason, error = "runtime_error", "会话已保存在恢复副本，合并原 profile 会话库失败"
        if grant and reason == "completed" and exit_code == 0:
            try:
                published = _publish_recovery_artifacts(staging, output_dir)
            except (OSError, RuntimeError, UnicodeError) as exc:
                reason, error = "runtime_error", f"隔离产物已保留，发布未完成：{exc}"
        with _cancel_lock:
            _cancellations.pop(attempt_id, None)
        raw = "".join(chunks)
        if not observed_session:
            match = re.search(r"[Ss]ession(?:_id)?:\s*([A-Za-z0-9_-]+)", _ANSI.sub("", raw))
            observed_session = match[1] if match else None
        infrastructure = reason in {"runtime_error", "worker_preflight", "session_busy", "session_missing", "input_required", "iteration_limit", "wall_timeout", "inactivity_timeout", "cancelled", "provider_error"}
        status = "completed" if reason == "completed" else "interrupted" if infrastructure else "failed"
        store.update(attempt_id, status=status, ended=time.time(), session_id=observed_session, exit_code=exit_code, error=error, termination_reason=reason)
        seq = store.append(attempt_id, "end", {"status": status, "exit_code": exit_code, "termination_reason": reason})
        _emit("terminal.ended", **ids, seq=seq, status=status, exit_code=exit_code, termination_reason=reason, error=error, session_id=observed_session)
    from core.contracts import utf8_safe
    answer = final_response or _ANSI.sub("", raw)
    if isolated_session:
        answer += "\n\n[恢复会话已保存在隔离副本。原 profile 中其他会话已保留。]"
    if published:
        answer += "\n\n[平台已核对并发布的新产物]\n" + "\n".join(published)
    return {"output": utf8_safe(answer), "exit_code": exit_code if reason == "completed" else exit_code if reason == "process_error" else -1, "error": error, "session_id": observed_session, "termination_reason": reason, "infra_failure": infrastructure, "terminal_path": str(output_file), **ids}
