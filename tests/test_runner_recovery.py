"""Failure injection at run acceptance and FIFO handoff, without live services."""
from types import SimpleNamespace

import pytest


@pytest.fixture
def runner(monkeypatch):
    import web_app as web
    from config.config import config
    from web.event_bus import get_task_session
    workspace = config._workspace
    session = get_task_session()
    calls = []
    ceo = SimpleNamespace(_states={}, activate_session=lambda name: ceo._states.update(
        {name: {"_task_done": True}}), talk=lambda text: calls.append(text),
        mark_interrupted=lambda *args: None)
    monkeypatch.setattr(web, "get_ceo", lambda: ceo)
    monkeypatch.setattr(web, "registry", SimpleNamespace(count=lambda: 1))
    monkeypatch.setattr(web, "_save_current_session", lambda *a, **kw: None)
    monkeypatch.setattr(web.bus, "emit", lambda *a, **kw: None)
    monkeypatch.setattr(web, "_queue", [])
    monkeypatch.setitem(web._busy, "state", False)
    yield web, ceo, calls
    config._workspace = workspace
    web.set_task_session(session)


def immediate_thread(target, **kwargs):
    return SimpleNamespace(start=target)


@pytest.mark.parametrize("stage", ["construct", "start", "notification"])
def test_failed_acceptance_releases_busy_and_allows_retry(runner, monkeypatch, stage):
    web, _, calls = runner

    def fail():
        raise RuntimeError("injected launch failure")

    monkeypatch.setattr(web.threading, "Thread", lambda **kw:
        fail() if stage == "construct" else SimpleNamespace(start=fail))
    if stage == "notification":
        monkeypatch.setattr(web.bus, "emit", lambda *a, **kw: fail())
    result = web._start_task("first", "launch-fixture")
    assert result["ok"] is False and not web._busy["state"]
    monkeypatch.setattr(web.bus, "emit", lambda *a, **kw: None)
    monkeypatch.setattr(web.threading, "Thread", immediate_thread)
    assert web._start_task("retry", "launch-fixture")["ok"]
    assert calls == ["retry"]


def test_fifo_handoff_launch_failure_keeps_original_head_and_context(runner, monkeypatch):
    web, _, calls = runner
    pending = [{"text": "second", "session": "second-fixture", "workspace": "second-ws"},
               {"text": "third", "session": "third-fixture", "workspace": "third-ws"}]
    web._queue.extend(pending)
    launches = []

    def thread(target, **kwargs):
        launches.append(kwargs["name"])
        if len(launches) == 1:
            return SimpleNamespace(start=target)
        def fail():
            raise RuntimeError("injected handoff failure")
        return SimpleNamespace(start=fail)

    monkeypatch.setattr(web.threading, "Thread", thread)
    assert web._start_task("first", "first-fixture")["ok"]
    assert calls == ["first"]
    assert web._queue == pending
    assert not web._busy["state"]


def test_end_notification_failure_cannot_wedge_scheduler_or_drain_fifo(runner, monkeypatch):
    web, _, calls = runner
    pending = {"text": "second", "session": "second-fixture", "workspace": "second-ws"}
    web._queue.append(pending)

    def emit(kind, **data):
        if data.get("msg") == "当前运行已结束":
            raise OSError("event storage unavailable")

    monkeypatch.setattr(web.bus, "emit", emit)
    monkeypatch.setattr(web.threading, "Thread", immediate_thread)
    assert web._start_task("first", "first-fixture")["ok"]
    assert calls == ["first"] and web._queue == [pending]
    assert not web._busy["state"]


def test_interruption_record_failure_still_preserves_fifo(runner, monkeypatch):
    web, ceo, calls = runner
    pending = {"text": "second", "session": "second-fixture", "workspace": "second-ws"}
    web._queue.append(pending)
    def fail(*args):
        raise OSError("injected persistence failure")
    ceo.talk = ceo.mark_interrupted = fail
    monkeypatch.setattr(web.threading, "Thread", immediate_thread)
    assert web._start_task("first", "first-fixture")["ok"]
    assert web._queue == [pending] and not web._busy["state"]


def test_chat_api_reports_launch_failure_as_retryable_503(runner, monkeypatch):
    web, _, calls = runner
    def fail():
        raise RuntimeError("cannot start thread")
    monkeypatch.setattr(web.threading, "Thread", lambda **kw: SimpleNamespace(start=fail))
    response = web.app.test_client().post("/api/chat", json={
        "text": "fixture", "session": "api-launch-fixture"})
    assert response.status_code == 503 and response.json["ok"] is False
    assert not web._busy["state"] and not calls


def test_queue_notification_failure_does_not_invite_duplicate_submission(runner, monkeypatch):
    web, _, calls = runner
    web._busy["state"] = True
    def fail(*args, **kwargs):
        raise OSError("cannot save notification")
    monkeypatch.setattr(web.bus, "emit", fail)
    response = web.app.test_client().post("/api/chat", json={
        "text": "queued", "session": "queue-api-fixture"})
    assert response.status_code == 200 and response.json["queued"]
    assert len(web._queue) == 1 and web._queue[0]["text"] == "queued"
    assert not calls


@pytest.mark.parametrize("payload", [["text"], "text", 42, {"text": ["text"]},
                                      {"text": "text", "session": 12},
                                      {"text": "text", "session": []}])
def test_bad_chat_payload_returns_400_without_launch_or_queue_mutation(runner, payload):
    web, _, calls = runner
    response = web.app.test_client().post("/api/chat", json=payload)
    assert response.status_code == 400 and response.json["ok"] is False
    assert not calls and not web._queue and not web._busy["state"]


@pytest.mark.parametrize("failure", ["adgs-evolution", "adgs-autosave"])
def test_bootstrap_retries_failed_thread_without_duplicating_started_service(runner, monkeypatch, failure):
    web, _, _ = runner
    monkeypatch.setattr(web, "_bootstrapped", False)
    monkeypatch.setattr(web, "_bootstrap_started", set())
    launched = []
    attempts = []
    def thread(**kw):
        def start():
            attempts.append(kw["name"])
            if kw["name"] == failure and attempts.count(failure) == 1:
                raise RuntimeError("startup failure")
            launched.append(kw["name"])
        return SimpleNamespace(start=start)
    monkeypatch.setattr(web.threading, "Thread", thread)
    web._bootstrap_runtime()
    web._bootstrap_runtime()
    assert web._bootstrapped and launched == ["adgs-evolution", "adgs-autosave"]
    assert attempts.count(failure) == 2


def test_exhausted_bootstrap_can_retry_only_missing_service(runner, monkeypatch):
    web, _, _ = runner
    monkeypatch.setattr(web, "_bootstrapped", False)
    monkeypatch.setattr(web, "_bootstrap_started", set())
    attempts = []
    fail = [True]
    def thread(**kw):
        def start():
            attempts.append(kw["name"])
            if fail[0] and kw["name"] == "adgs-autosave":
                raise RuntimeError("resource unavailable")
        return SimpleNamespace(start=start)
    monkeypatch.setattr(web.threading, "Thread", thread)
    with pytest.raises(RuntimeError):
        web._bootstrap_runtime()
    assert not web._bootstrapped and web._bootstrap_started == {"adgs-evolution"}
    fail[0] = False
    web._bootstrap_runtime()
    assert web._bootstrapped and attempts.count("adgs-evolution") == 1


def test_failed_saved_session_restore_does_not_mark_or_overwrite_original(runner, monkeypatch):
    web, ceo, calls = runner
    from unittest.mock import Mock
    ceo.activate_session = Mock(side_effect=ValueError("corrupt saved session"))
    ceo.mark_interrupted = Mock(side_effect=AssertionError("Would create blank replacement history"))
    save = Mock(side_effect=AssertionError("Would overwrite original saved file"))
    monkeypatch.setattr(web, "_save_current_session", save)
    monkeypatch.setattr(web.threading, "Thread", immediate_thread)
    pending = {"text": "next", "session": "next"}
    web._queue.append(pending)
    assert web._start_task("resume", "corrupt-session")["ok"]
    assert not calls and not save.called and not ceo.mark_interrupted.called
    assert not web._busy["state"] and web._queue == [pending]
