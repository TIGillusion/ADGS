"""Read-only execution inspection and durable terminal replay for Web3."""
import json
import re
import time
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, send_file

from core.execution_store import execution_store
from core.terminal_runtime import terminal_store
from web.event_bus import bus

execution_api = Blueprint("executions", __name__)
_ID = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-_])")


def _valid(value):
    return bool(_ID.fullmatch(value or "")) and value not in (".", "..")


def _attempt(meta):
    return {**meta, "state": meta["status"], "started_at": meta["started"],
            "ended_at": meta.get("ended"), "last_activity_at": meta.get("last_activity")}


def runtime_fields(execution_id, node_id=None, checkpoint=None):
    from core.node import active_cancel
    from core.delegation_runtime import execution_wait_state
    waiting = execution_wait_state(execution_id)
    fields = {"waiting_for_children": waiting == 'children', "waiting_for_slot": waiting == 'slot'}
    from core.hermes_process import resident_pool
    fields.update(resident_pool.describe(execution_id))
    # CEO lifetime is owned by the session coordinator, not NODE's worker registry.
    if node_id != 'ceo' and not execution_id.startswith('ceo-'):
        fields['runtime_active'] = active_cancel(execution_id) is not None
        # Initial admission waits before an ExecutionSlot exists. Slot tracking
        # only sees ancestors reacquiring admission after waiting for children.
        # Require a live owner so a queued checkpoint left by a stopped runtime
        # does not falsely claim it is still waiting in the current queue.
        saved = checkpoint or {}
        if (waiting is None and fields['runtime_active']
                and saved.get('phase') in {'queued', 'waiting_slot'}
                and saved.get('state') in {'running', 'queued', 'starting', 'waiting'}):
            fields['waiting_for_slot'] = True
    return fields


@execution_api.get("/api/executions/<execution_id>")
def execution_detail(execution_id):
    if not _valid(execution_id):
        return jsonify(ok=False, error="非法执行 ID"), 400
    checkpoint = execution_store.get(execution_id)
    attempts = terminal_store.list_attempts(execution_id=execution_id)
    if not checkpoint and not attempts:
        return jsonify(ok=False, error="执行记录不存在"), 404
    latest = attempts[-1] if attempts else {}
    meta = checkpoint or {"execution_id": execution_id, "node_id": latest.get("profile"),
                          "profile": latest.get("profile"), "task_id": latest.get("task_id"),
                          "run_id": latest.get("run_id"), "state": latest.get("status"),
                          "started_at": latest.get("started"), "ended_at": latest.get("ended")}
    detail = {**meta, "session_id": latest.get("session_id") or meta.get("session_id"),
              "last_activity_at": latest.get("last_activity") or meta.get("updated_at"),
              "attempts": [_attempt(a) for a in attempts],
              "events": bus.execution_events(execution_id)}
    detail.update(runtime_fields(execution_id, detail.get('node_id'), detail))
    return jsonify(ok=True, execution=detail)


@execution_api.get("/api/attempts/<attempt_id>/terminal")
def terminal_events(attempt_id):
    if not _valid(attempt_id):
        return jsonify(ok=False, error="非法终端 ID"), 400
    if not terminal_store.get_attempt(attempt_id):
        return jsonify(ok=False, error="终端记录不存在"), 404
    try:
        after = max(0, int(request.headers.get("Last-Event-ID") or request.args.get("after", 0)))
    except (ValueError, TypeError):
        return jsonify(ok=False, error="非法终端游标"), 400

    def generate():
        cursor = after
        heartbeat = time.monotonic()
        while True:
            records = terminal_store.read_records(attempt_id, after=cursor)
            for record in records:
                fields = record["data"]
                payload = {"type": record["kind"], "seq": record["seq"],
                           "attempt_id": attempt_id, "ts": record["ts"]}
                if record["kind"] == "data":
                    payload["data"] = fields.get("text", "")
                else:
                    payload.update(fields)
                cursor = record["seq"]
                yield f"id: {cursor}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                if record["kind"] == "end":
                    return
            if records:
                continue
            meta = terminal_store.get_attempt(attempt_id)
            if not meta or (meta["status"] not in {"starting", "running", "stopping"}
                            and cursor >= meta["last_seq"]):
                return
            if time.monotonic() - heartbeat >= 10:
                heartbeat = time.monotonic()
                yield ": keepalive\n\n"
            time.sleep(0.2)
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@execution_api.get("/api/attempts/<attempt_id>/download")
def terminal_download(attempt_id):
    if not _valid(attempt_id):
        return jsonify(ok=False, error="非法终端 ID"), 400
    meta = terminal_store.get_attempt(attempt_id)
    if not meta:
        return jsonify(ok=False, error="终端记录不存在"), 404
    fmt = request.args.get("format", "text")
    if fmt not in {"vt", "text"}:
        return jsonify(ok=False, error="只支持 text/vt 格式"), 400
    path = terminal_store.paths(meta["execution_id"], attempt_id) / "output.vt"
    if fmt == "vt" and path.exists():
        return send_file(path, mimetype="application/octet-stream", as_attachment=True,
                         download_name=f"{attempt_id}.vt")
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    if fmt == "text":
        text = _ANSI.sub("", text)
    return Response(text, mimetype="text/plain", headers={
        "Content-Disposition": f'attachment; filename="{attempt_id}.{fmt if fmt == "vt" else "txt"}"'})


@execution_api.post("/api/executions/<execution_id>/cancel")
def execution_cancel(execution_id):
    if not _valid(execution_id):
        return jsonify(ok=False, error="非法执行 ID"), 400
    from core.node import cancel_execution
    cancelled = cancel_execution(execution_id)
    return jsonify(ok=cancelled, error=None if cancelled else "执行已结束或不存在")
