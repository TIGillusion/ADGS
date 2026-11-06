"""Durable node execution checkpoints, separate from each AGS's Hermes home."""
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

PROFILE_NOT_READY = "AGS profile 未完成配置，保留任务供恢复"
WORKER_HOLD_REASON = "平台处于事故恢复保护状态，Hermes 节点启动已暂停"


def worker_launch_on_hold(state_dir=None):
    return (_state_directory(state_dir) / "worker-hold").exists()


def _state_directory(state_dir=None):
    return Path(state_dir or os.environ.get("ADGS_STATE_DIR", str(Path(__file__).resolve().parents[1] / ".adgs_runtime")))


def recovery_grant(execution_id, *, state_dir=None):
    """An operator may enable one low-integrity execution while holding all other work."""
    if os.name != "nt" or not execution_id or not worker_launch_on_hold(state_dir):
        return None
    try:
        grant = json.loads((_state_directory(state_dir) / "worker-recovery.json").read_text(encoding="utf-8"))
        if (grant["execution_id"] == execution_id and isinstance(grant["profile"], str)
                and isinstance(grant["workspace"], str) and isinstance(grant["output_dir"], str)):
            return grant
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None
from contextlib import contextmanager


class ExecutionStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.lock = threading.RLock()

    @contextmanager
    def _connect(self):
        path = self.path or Path(os.environ.get("ADGS_STATE_DIR",
                      str(Path(__file__).resolve().parents[1] / ".adgs_runtime"))) / "executions.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=15)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS executions (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def get(self, execution_id):
        with self.lock, self._connect() as conn:
            row = conn.execute("SELECT payload FROM executions WHERE id=?", (execution_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def put(self, execution_id, **fields):
        with self.lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT payload FROM executions WHERE id=?", (execution_id,)).fetchone()
            payload = json.loads(row[0]) if row else {"execution_id": execution_id, "started_at": time.time()}
            payload.update(fields)
            payload["updated_at"] = time.time()
            # sqlite TEXT params are strict UTF-8; a lone/split surrogate in a
            # work_log must never abort the whole dispatch (2026-10-21 实测).
            from core.contracts import utf8_safe
            conn.execute("INSERT OR REPLACE INTO executions VALUES (?, ?)",
                         (execution_id, utf8_safe(json.dumps(payload, ensure_ascii=False, allow_nan=False))))
            return payload


def preworker_resume_evidence(saved):
    """Positive checkpoint evidence that no worker session has been established.

    Callers must additionally confirm absence of durable terminal attempts.
    Missing metadata alone is not permission to guess/restart a worker.
    """
    result = saved.get("result") or {}
    if (saved.get("session_id") or saved.get("hermes_session_id") or
            result.get("session_id") or result.get("hermes_session_id") or
            saved.get("state") not in {"infra_failure", "protocol_error", "waiting_user", "interrupted"}):
        return False
    if result.get("termination_reason") == "worker_preflight":
        return True
    # Older dispatchers allocated an attempt ID before this exact profile check.
    # An ID alone does not imply a launch. Both callers must still check the terminal store.
    if saved.get("phase") in {"execute", "preflight"} and saved.get("error") == PROFILE_NOT_READY:
        return True
    if saved.get("attempt_id"):
        return False
    pending = saved.get("decision_pending")
    return pending is True or (pending is None and
        (saved.get("phase") == "decision" or result.get("outcome") in
         {"infra_failure", "protocol_error", "waiting_user"}))


def preworker_attempts_safe(attempts):
    """Permit a retry only when every recorded attempt stopped before spawning."""
    return all(attempt.get("status") == "interrupted"
               and attempt.get("termination_reason") == "worker_preflight"
               and not attempt.get("session_id") for attempt in attempts)


execution_store = ExecutionStore()
