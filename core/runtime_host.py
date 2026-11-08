"""Detached local scheduler host. The frontend Flask process is only a proxy.

Only ensure_runtime starts the host; importing this module is side-effect free.
The private port is selected by bind(0), never guessed from a free-port probe.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = Path(os.environ.get("ADGS_STATE_DIR", str(ROOT / ".adgs_runtime")))
AUTH_HEADER = "X-ADGS-Runtime-Token"


@contextmanager
def _host_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "host.lock"
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b" ")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _read_state(directory):
    try:
        state = json.loads((directory / "host.json").read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            return None
        url = urllib.parse.urlparse(str(state.get("url", "")))
        if url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port or url.username or url.password or url.path or url.query or url.fragment:
            return None
        return state
    except (OSError, ValueError):
        return None


def _healthy(state):
    if not state:
        return False
    try:
        request = urllib.request.Request(state["url"] + "/_adgs_runtime/health", headers={AUTH_HEADER: state["token"]})
        with urllib.request.urlopen(request, timeout=1.5) as response:
            data = json.loads(response.read())
        return data.get("nonce") == state.get("nonce") and data.get("pid") == state.get("pid")
    except (KeyError, OSError, ValueError, urllib.error.URLError):
        return False


def _write_state(directory, state, filename="host.json"):
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / ("host." + secrets.token_hex(6) + ".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    temporary.replace(directory / filename)


def _pending_startup(directory):
    try:
        state = json.loads((directory / "startup.json").read_text(encoding="utf-8"))
        return state if isinstance(state, dict) and isinstance(state.get("pid"), int) else None
    except (OSError, ValueError):
        return None


def ensure_runtime(timeout=30, state_dir=None):
    """Start once or reconnect; do not attach runtime lifetime to the Web server."""
    directory = Path(state_dir or STATE_DIR)
    state = _read_state(directory)
    if _healthy(state):
        return state
    with _host_lock(directory):
        state = _read_state(directory)
        if _healthy(state):
            return state
        # Do not spawn a second scheduler merely because a live host is briefly
        # slow. A process whose token matches this startup owns the state file.
        if state and _pid_exists(state.get("pid")):
            raise RuntimeError("Runtime is alive but unavailable; retry without starting a second scheduler")
        pending = _pending_startup(directory)
        if pending and _pid_exists(pending["pid"]):
            raise RuntimeError("Runtime is still starting; retry without starting a second scheduler")
        token, nonce = secrets.token_urlsafe(32), secrets.token_hex(16)
        env = os.environ.copy()
        env.update({"ADGS_RUNTIME_WORKER": "1", "ADGS_RUNTIME_TOKEN": token, "ADGS_RUNTIME_NONCE": nonce, "ADGS_RUNTIME_STATE_DIR": str(directory), "PYTHONUNBUFFERED": "1",
                    "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with (directory / "runtime.log").open("ab") as log:
            proc = subprocess.Popen([sys.executable, "-m", "core.runtime_host", "--serve"], cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, creationflags=flags, close_fds=True, start_new_session=os.name != "nt")
        _write_state(directory, {"pid": proc.pid, "nonce": nonce, "started": time.time()}, "startup.json")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("Runtime startup failed; inspect .adgs_runtime/runtime.log")
            state = _read_state(directory)
            if state and state.get("nonce") == nonce and _healthy(state):
                return state
            time.sleep(0.15)
        # The detached host may still finish booting; do not kill active work.
        raise RuntimeError("Runtime startup timed out; host is retained for reconnect")


def _pid_exists(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        if pid > 0xFFFFFFFF:
            return False
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            # OpenProcess can still succeed for an exited process while another
            # owner retains its handle; only STILL_ACTIVE means it can own work.
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def proxy_request(incoming=None):
    """Proxy API responses, with continuous SSE streaming and disconnect cleanup."""
    from flask import Response, request, stream_with_context
    incoming = incoming or request
    try:
        state = ensure_runtime()
    except (RuntimeError, OSError):
        return Response(json.dumps({"error": "Runtime unavailable; retry to reconnect"}), status=503, mimetype="application/json")
    headers = {key: value for key, value in incoming.headers.items() if key.lower() not in {"host", "connection", "content-length", AUTH_HEADER.lower()}}
    headers[AUTH_HEADER] = state["token"]
    target = state["url"] + incoming.full_path.rstrip("?")
    outbound = urllib.request.Request(target, data=incoming.get_data() if incoming.method not in {"GET", "HEAD"} else None, headers=headers, method=incoming.method)
    try:
        upstream = urllib.request.urlopen(outbound, timeout=45)
    except urllib.error.HTTPError as exc:
        upstream = exc
    except urllib.error.URLError:
        return Response(json.dumps({"error": "Runtime unavailable; task state remains in the runtime"}), status=503, mimetype="application/json")
    response_headers = [(key, value) for key, value in upstream.headers.items() if key.lower() not in {"connection", "transfer-encoding", "content-length", "server", "date"}]
    is_stream = "text/event-stream" in upstream.headers.get("Content-Type", "")
    if not is_stream:
        try:
            return Response(upstream.read(), status=upstream.status, headers=response_headers)
        finally:
            upstream.close()
    def iterate():
        try:
            while True:
                chunk = upstream.read1(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            upstream.close()
    return Response(stream_with_context(iterate()), status=upstream.status, headers=response_headers)


def serve():
    token = os.environ.get("ADGS_RUNTIME_TOKEN")
    nonce = os.environ.get("ADGS_RUNTIME_NONCE")
    if not token or not nonce:
        raise RuntimeError("Runtime host requires a private startup token")
    os.environ["ADGS_RUNTIME_WORKER"] = "1"
    directory = Path(os.environ.get("ADGS_RUNTIME_STATE_DIR", STATE_DIR))
    from web import event_bus
    configure = getattr(event_bus, "configure_event_store", None)
    if configure:
        configure(directory / "events.sqlite")
    import web_app
    from flask import jsonify, request
    from werkzeug.serving import make_server
    app = web_app.app
    app.config["ADGS_PROXY_RUNTIME"] = False
    @app.before_request
    def authenticate_runtime():
        if not secrets.compare_digest(request.headers.get(AUTH_HEADER, ""), token):
            return jsonify({"error": "Runtime authentication required"}), 403
    @app.get("/_adgs_runtime/health")
    def health():
        return jsonify({"pid": os.getpid(), "nonce": nonce})
    server = make_server("127.0.0.1", 0, app, threaded=True)
    _write_state(directory, {"url": f"http://127.0.0.1:{server.server_port}", "token": token, "nonce": nonce, "pid": os.getpid(), "started": time.time()})
    # A2A 种群协作 MCP server（merged_brain；独立 uvicorn 线程，runtime 活着即服务）
    start_mesh = getattr(web_app, "_start_mesh_mcp", None)
    if start_mesh:
        try:
            start_mesh()
        except Exception:
            logging.getLogger(__name__).exception("mesh MCP bootstrap failed")
    # Evolution may provision worker profiles. Start it after publishing mesh readiness.
    bootstrap = getattr(web_app, "_bootstrap_runtime", None) or getattr(web_app, "_bootstrap_evolution", None)
    if bootstrap:
        import threading
        threading.Thread(target=bootstrap, daemon=True, name="adgs-bootstrap").start()
    try:
        server.serve_forever()
    finally:
        from core.hermes_process import resident_pool
        resident_pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    if parser.parse_args().serve:
        serve()
