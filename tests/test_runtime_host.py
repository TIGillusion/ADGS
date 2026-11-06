import json
import threading
import time
from pathlib import Path
from unittest.mock import Mock

from flask import Flask

from core import runtime_host as host


def test_reconnect_does_not_launch_second_host(tmp_path, monkeypatch):
    state = {"url": "http://127.0.0.1:1234", "token": "test", "nonce": "nonce", "pid": 123}
    host._write_state(tmp_path, state)
    monkeypatch.setattr(host, "_healthy", lambda value: value == state)
    popen = Mock(side_effect=AssertionError("Should reconnect"))
    monkeypatch.setattr(host.subprocess, "Popen", popen)
    assert host.ensure_runtime(state_dir=tmp_path) == state
    assert host.ensure_runtime(state_dir=tmp_path) == state
    popen.assert_not_called()


def test_windows_exited_process_with_retained_handle_is_not_alive():
    import os
    import subprocess
    import sys
    import pytest
    if os.name != "nt":
        pytest.skip("Windows process object lifetime")
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        assert host._pid_exists(process.pid)
        retained_handle = process._handle
        process.terminate()
        process.wait(timeout=5)
        assert process._handle is retained_handle
        assert not host._pid_exists(process.pid)
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_live_unresponsive_runtime_does_not_spawn_duplicate(tmp_path, monkeypatch):
    import pytest
    host._write_state(tmp_path, {"url": "http://127.0.0.1:1234", "token": "t", "pid": 123})
    monkeypatch.setattr(host, "_healthy", lambda state: False)
    monkeypatch.setattr(host, "_pid_exists", lambda pid: True)
    with pytest.raises(RuntimeError, match="second scheduler"):
        host.ensure_runtime(state_dir=tmp_path)


def test_retained_startup_does_not_spawn_duplicate(tmp_path, monkeypatch):
    import pytest
    process = Mock(pid=123)
    process.poll.return_value = None
    popen = Mock(return_value=process)
    monkeypatch.setattr(host.subprocess, "Popen", popen)
    monkeypatch.setattr(host, "_pid_exists", lambda pid: True)
    with pytest.raises(RuntimeError, match="retained"):
        host.ensure_runtime(timeout=0, state_dir=tmp_path)
    with pytest.raises(RuntimeError, match="still starting"):
        host.ensure_runtime(timeout=0, state_dir=tmp_path)
    assert popen.call_count == 1


def test_proxy_runtime_unavailable_is_retryable(monkeypatch):
    def unavailable():
        raise RuntimeError("Still starting")
    monkeypatch.setattr(host, "ensure_runtime", unavailable)
    app = Flask(__name__)
    with app.test_request_context("/api/status"):
        assert host.proxy_request().status_code == 503


def test_proxy_preserves_sse_chunks_and_cleans_up(monkeypatch):
    from email.message import Message
    response = Mock()
    response.status = 200
    response.headers = Message()
    response.headers["Content-Type"] = "text/event-stream"
    response.read1.side_effect = [b"id: 1\ndata: first\n\n", b"id: 2\ndata: second\n\n", b""]
    monkeypatch.setattr(host, "ensure_runtime", lambda: {"url": "http://127.0.0.1:1234", "token": "private"})
    captured = []
    monkeypatch.setattr(host.urllib.request, "urlopen", lambda request, timeout: captured.append(request) or response)
    app = Flask(__name__)
    with app.test_request_context("/api/events?after=2", headers={host.AUTH_HEADER: "forged"}):
        proxied = host.proxy_request()
        assert b"".join(proxied.response) == b"id: 1\ndata: first\n\nid: 2\ndata: second\n\n"
    assert captured[0].get_header("X-adgs-runtime-token") == "private"
    assert captured[0].full_url.endswith("/api/events?after=2")
    response.close.assert_called_once()


def test_private_host_authentication_bootstrap_and_state(tmp_path, monkeypatch):
    import sys
    import types
    import werkzeug.serving
    from web import event_bus
    app = Flask("test_runtime_host")
    bootstrapped = threading.Event()
    monkeypatch.setitem(sys.modules, "web_app", types.SimpleNamespace(app=app, _bootstrap_runtime=bootstrapped.set))
    monkeypatch.setenv("ADGS_RUNTIME_TOKEN", "private-test-token")
    monkeypatch.setenv("ADGS_RUNTIME_NONCE", "test-nonce")
    monkeypatch.setenv("ADGS_RUNTIME_STATE_DIR", str(tmp_path))
    configured = []
    monkeypatch.setattr(event_bus, "configure_event_store", lambda path: configured.append(path), raising=False)
    server = Mock(server_port=43210)
    monkeypatch.setattr(werkzeug.serving, "make_server", lambda *args, **kwargs: server)
    host.serve()
    assert bootstrapped.wait(2)
    assert configured == [tmp_path / "events.sqlite"]
    assert app.config["ADGS_PROXY_RUNTIME"] is False
    client = app.test_client()
    assert client.get("/_adgs_runtime/health").status_code == 403
    response = client.get("/_adgs_runtime/health", headers={host.AUTH_HEADER: "private-test-token"})
    assert response.json["nonce"] == "test-nonce"
    assert host._read_state(tmp_path)["url"] == "http://127.0.0.1:43210"
    server.serve_forever.assert_called_once()


def test_runtime_state_rejects_external_url(tmp_path):
    host._write_state(tmp_path, {"url": "http://127.0.0.1:1234@external.example", "token": "private"})
    assert host._read_state(tmp_path) is None


def test_detached_host_survives_frontend_exit_and_reconnects(tmp_path):
    """Use a fake Flask app in another checkout; never import business bootstrap."""
    import os
    import shutil
    import signal
    import subprocess
    import sys
    package = tmp_path / "core"
    package.mkdir()
    (package / "__init__.py").write_text("")
    shutil.copy2(host.__file__, package / "runtime_host.py")
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "__init__.py").write_text("")
    (tmp_path / "web" / "event_bus.py").write_text("def configure_event_store(path): pass\n")
    (tmp_path / "web_app.py").write_text("from flask import Flask\napp = Flask(__name__)\ndef _bootstrap_runtime(): pass\n")
    state_dir = tmp_path / "state"
    env = os.environ.copy()
    env["ADGS_STATE_DIR"] = str(state_dir)
    first = subprocess.Popen([sys.executable, "-c", "from core.runtime_host import ensure_runtime; import time; ensure_runtime(); time.sleep(30)"], cwd=tmp_path, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    state = None
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = host._read_state(state_dir)
            if state and host._healthy(state):
                break
            assert first.poll() is None, "Isolated frontend failed during startup"
            time.sleep(0.1)
        assert state and host._healthy(state)
        first.terminate()
        first.wait(timeout=5)
        assert host._healthy(state), "Runtime died with its frontend"
        second = subprocess.run([sys.executable, "-c", "from core.runtime_host import ensure_runtime; print(ensure_runtime()['pid'])"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10)
        assert second.returncode == 0, second.stderr
        assert int(second.stdout.strip()) == state["pid"]
    finally:
        if first.poll() is None:
            first.terminate()
            first.wait(timeout=5)
        state = state or host._read_state(state_dir)
        if state and host._pid_exists(state["pid"]):
            if os.name == "nt":
                import win32api
                import win32con
                process = win32api.OpenProcess(win32con.PROCESS_TERMINATE, False, state["pid"])
                try:
                    win32api.TerminateProcess(process, 0)
                finally:
                    process.Close()
            else:
                os.kill(state["pid"], signal.SIGTERM)
