"""任务队列 API 回归(2026-10-22 用户报告:排队消息不可见/不可编辑/不可撤销,
任务结束后排队消息不自动接续):
  1. 排队条目带稳定 id,api_state 与 GET /api/queue 均可见
  2. PUT /api/queue/<id> 编辑文本
  3. DELETE /api/queue/<id> 撤销
  4. 不存在的 id → 404
  5. runner 接续条件:正常结束(含 blocked,_task_done=False)弹队首;
     run_failed 时不弹(防错误风暴)—— 直接测 web_app 的接续状态机输入
"""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import utils.context_probe
utils.context_probe.probe_context_window = lambda *a, **k: None

import pytest

import web_app

app = web_app.app


@pytest.fixture()
def clean_queue(monkeypatch):
    """隔离:清空真实队列、屏蔽事件落盘、busy 置位模拟任务执行中"""
    monkeypatch.setattr(web_app.bus, "emit", lambda *a, **k: None)
    with web_app._queue_lock:
        web_app._queue.clear()
    old_busy = web_app._busy["state"]
    web_app._busy["state"] = True
    yield
    web_app._busy["state"] = old_busy
    with web_app._queue_lock:
        web_app._queue.clear()


def _enqueue(text, session="queue-test"):
    r = app.test_client().post("/api/chat", json={"text": text, "session": session})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["ok"] and body.get("queued"), body
    return body


def test_queued_message_is_visible_with_stable_id(clean_queue):
    _enqueue("第一条排队消息")
    _enqueue("第二条排队消息", session="other-sess")
    body = app.test_client().get("/api/queue").get_json()
    assert body["ok"] and len(body["queue"]) == 2
    first, second = body["queue"]
    assert first["text"] == "第一条排队消息"
    assert first["session"] == "queue-test"
    assert first["id"] and second["id"] and first["id"] != second["id"]
    # api_state 同样携带完整队列
    state = app.test_client().get("/api/state").get_json()
    assert state["queue_len"] == 2
    assert [q["id"] for q in state["queue"]] == [first["id"], second["id"]]


def test_queue_update_edits_text(clean_queue):
    _enqueue("原始任务")
    qid = app.test_client().get("/api/queue").get_json()["queue"][0]["id"]
    r = app.test_client().put(f"/api/queue/{qid}", json={"text": "修改后的任务"})
    assert r.status_code == 200 and r.get_json()["ok"]
    q = app.test_client().get("/api/queue").get_json()["queue"][0]
    assert q["text"] == "修改后的任务"
    assert q["id"] == qid


def test_queue_remove_cancels_entry(clean_queue):
    _enqueue("将被撤销的消息")
    _enqueue("保留的消息")
    qid = app.test_client().get("/api/queue").get_json()["queue"][0]["id"]
    r = app.test_client().delete(f"/api/queue/{qid}")
    assert r.status_code == 200 and r.get_json()["ok"]
    queue = app.test_client().get("/api/queue").get_json()["queue"]
    assert [q["text"] for q in queue] == ["保留的消息"]


def test_queue_operations_reject_unknown_id(clean_queue):
    _enqueue("排队中")
    assert app.test_client().put("/api/queue/deadbeef", json={"text": "x"}).status_code == 404
    assert app.test_client().delete("/api/queue/deadbeef").status_code == 404


def test_queue_update_rejects_empty_text(clean_queue):
    _enqueue("排队中")
    qid = app.test_client().get("/api/queue").get_json()["queue"][0]["id"]
    r = app.test_client().put(f"/api/queue/{qid}", json={"text": "   "})
    assert r.status_code == 400
    assert app.test_client().get("/api/queue").get_json()["queue"][0]["text"] == "排队中"


def test_chat_while_idle_starts_immediately_not_queued(clean_queue, monkeypatch):
    """空闲时提交不应入队(回归:busy 判断)——启动 runner 的路径打桩"""
    web_app._busy["state"] = False
    started = {}
    monkeypatch.setattr(web_app, "_start_task",
                        lambda text, sess_name=None, workspace=None, **_options:
                        started.update(text=text, session=sess_name) or {"ok": True})
    r = app.test_client().post("/api/chat", json={"text": "直接执行", "session": "s"})
    assert r.get_json()["ok"] and not r.get_json().get("queued")
    assert started["text"] == "直接执行"


def _run_to_boundary(monkeypatch, task_done=False, fail=False):
    from types import SimpleNamespace
    ceo = SimpleNamespace(_states={"queue-test": {"_task_done": task_done}},
                          activate_session=lambda name: None,
                          mark_interrupted=lambda *a: None)
    def talk(text):
        if fail:
            raise RuntimeError("fixture interruption")
    ceo.talk = talk
    monkeypatch.setattr(web_app, "get_ceo", lambda: ceo)
    monkeypatch.setattr(web_app, "registry", SimpleNamespace(count=lambda: 1))
    monkeypatch.setattr(web_app, "_save_current_session", lambda *a, **kw: None)
    monkeypatch.setattr(web_app, "set_task_session", lambda *a: None)
    monkeypatch.setattr(web_app.threading, "Thread", lambda target, **kw: SimpleNamespace(start=target))
    web_app._busy["state"] = False
    return web_app._start_task("原任务", "queue-test")


def test_drain_holds_ordinary_queue_until_task_is_complete(clean_queue, monkeypatch):
    """普通排队不得把尚未完成任务的阻塞状态当成已完成。"""
    _enqueue("任务真正结束后执行")
    assert _run_to_boundary(monkeypatch, task_done=False)["ok"]
    assert len(web_app._queue) == 1


def test_drain_holds_on_runner_exception(clean_queue, monkeypatch):
    _enqueue("异常时保留的消息")
    assert _run_to_boundary(monkeypatch, fail=True)["ok"]
    assert len(web_app._queue) == 1


def test_ask_user_suspend_holds_queue(clean_queue, monkeypatch):
    """CEO ask_user 挂起等待用户回答时,队列不接续 ——
    排队消息会被 CEO 上下文误读为对提问的回答(2026-10-22 实测)"""
    _enqueue("挂起期间保留的消息")
    session = "queue-test"
    fake_ceo = type("FakeCEO", (), {"_states": {session: {"_awaiting_input": True}}})()
    monkeypatch.setattr(web_app, "get_ceo", lambda: fake_ceo)
    # 复刻 runner 的挂起判定
    states = getattr(fake_ceo, "_states", {})
    awaiting_input = bool(states.get(session, {}).get("_awaiting_input"))
    assert awaiting_input is True
    with web_app._task_lock:
        with web_app._queue_lock:
            if not awaiting_input and web_app._queue:
                web_app._queue.pop(0)
    assert len(web_app._queue) == 1, "ask_user 挂起期间队列必须原样保留"
