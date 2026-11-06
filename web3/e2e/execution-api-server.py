"""Isolated real Flask/SQLite execution API fixture. No CEO/Hermes/model calls."""
import json
import os
import queue
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ISOLATED = tempfile.TemporaryDirectory(prefix="adgs-execution-browser-")
os.environ["ADGS_STATE_DIR"] = ISOLATED.name
sys.path.insert(0, str(ROOT))

from flask import Flask, Response, jsonify, request, send_from_directory
from core.execution_store import ExecutionStore
from core.terminal_runtime import TerminalStore
from web.event_bus import EventBus, set_task_session
import web.execution_api as execution_api_module

app = Flask(__name__)
terminals = TerminalStore(Path(ISOLATED.name) / "terminals")
executions = ExecutionStore(Path(ISOLATED.name) / "executions.sqlite")
events = EventBus()
events.configure_store(Path(ISOLATED.name) / "events.sqlite")
execution_api_module.terminal_store = terminals
execution_api_module.execution_store = executions
execution_api_module.bus = events
app.register_blueprint(execution_api_module.execution_api)

EXE, ATTEMPT = "blueprint-execution", "blueprint-attempt"
set_task_session("blueprint-browser")
events.emit("chat.user", text="真实 Flask / SQLite 终端接口验收")
events.emit("graph.task", id="blueprint-task", name="终端接口验收", goal="验证真实持久化终端流", parent="ceo", seq=1)
identity = dict(node="ags_1", profile="ags_1", execution_id=EXE, task_id="blueprint-task", run_id="blueprint-run")
events.emit("graph.dispatch", **identity, task="blueprint-task")
events.emit("node.status", **identity, status="running", phase="execute")
events.emit("node.brain", **identity, action="plan_hermes", detail="读取持久化 VT 并核对中英文输出")
terminals.create(attempt_id=ATTEMPT, execution_id=EXE, profile="ags_1", run_id="blueprint-run", task_id="blueprint-task")
terminals.update(ATTEMPT, status="running", session_id="fixture-session")
initial = "\x1b[32mREAL FLASK BLUEPRINT\x1b[0m\r\n中文输出与 ANSI 原样保留\r\n"
terminals.append(ATTEMPT, "data", {"text": initial})
output_path = terminals.paths(EXE, ATTEMPT) / "output.vt"
output_path.write_text(initial, encoding="utf-8", newline="")
executions.put(EXE, node_id="ags_1", profile="ags_1", task_id="blueprint-task", run_id="blueprint-run", state="running", phase="execute")
NODE = {"id": "ags_1", "describe": "独立运行记录与终端观测", "score": 60, "success_rate": .8, "tasks_done": 1, "gen": 1, "birth_type": "initial", "status": "idle", "skills": []}
BOOT = time.time()

@app.get("/api/state")
def state():
    return jsonify(ok=True, workspace=ISOLATED.name, population={"active": 1, "capacity": 8}, models={"ceo": "fixture", "brain": "fixture", "worker": "fixture"}, nodes=[NODE], events=events.history(1500), busy=False, queue_len=0, boot_ts=BOOT)

@app.get("/api/events")
def event_feed():
    after = int(request.headers.get("Last-Event-ID") or request.args.get("after", 0))
    subscriber = events.subscribe()
    def feed():
        cursor = after
        try:
            for item in events.history_after(cursor):
                cursor = item["id"]
                yield f"id: {cursor}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
            while True:
                try:
                    item = subscriber.get(timeout=10)
                except queue.Empty:
                    yield "event: heartbeat\ndata: {}\n\n"
                    continue
                if item["id"] <= cursor:
                    continue
                cursor = item["id"]
                yield f"id: {cursor}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
        finally:
            events.unsubscribe(subscriber)
    return Response(feed(), mimetype="text/event-stream")

@app.post("/integration/append")
def append_live():
    text = "\r\n\x1b[36mPERSISTED LIVE CHUNK\x1b[0m\r\n实时追加已写入 SQLite\r\n"
    terminals.append(ATTEMPT, "data", {"text": text})
    with output_path.open("a", encoding="utf-8", newline="") as output:
        output.write(text)
    events.emit("node.eval", **identity, passed=True, score=61, passed_items=["中文 VT 输出", "持久化游标回放"], failed_items=[], evidence=["output.vt"], detail="真实 Blueprint 接口与浏览器接入通过")
    terminals.append(ATTEMPT, "end", {"exit_code": 0, "status": "completed"})
    terminals.update(ATTEMPT, status="completed", ended=time.time(), exit_code=0)
    executions.put(EXE, state="passed", phase="finished", ended_at=time.time())
    return jsonify(ok=True)

@app.post("/api/session/graph")
def graph_save():
    return jsonify(ok=True)

@app.get("/api/node/ags_1")
def node():
    return jsonify(ok=True, node=NODE, skills=[])

@app.get("/")
@app.get("/<path:path>")
def assets(path="index.html"):
    return send_from_directory(ROOT / "web3" / "dist", path)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8098, threaded=True, debug=False, use_reloader=False)
