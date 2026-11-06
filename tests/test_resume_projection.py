"""Legacy resume recovery preserves durable messages and completed tool pairs."""
import copy
import json
from types import SimpleNamespace

import pytest

from core.resume_projection import project_unanswered_retries


def histories():
    prefix = [{"role": "assistant", "content": "", "tool_calls": [{"id": "a"}]},
              {"role": "tool", "tool_call_id": "a", "content": "existing artifact"}]
    tail = [{"role": "user", "content": "【kill+resume 修正指令】" + "合同" * 20000, "_row_id": n}
            for n in (10, 11)]
    merged = {"role": "user", "content": "\n".join(m["content"] for m in tail),
              "api_content": "oversized wire override", "_row_id": 10, "_db_persisted": True}
    return prefix + tail, prefix + [merged]


def test_projection_archives_exact_tail_without_mutating_db_history_or_tool_pairs(tmp_path):
    raw, history = histories()
    original = copy.deepcopy((raw, history))
    db = SimpleNamespace(get_messages_as_conversation=lambda *a, **kw: raw)
    projected = project_unanswered_retries(db, "s", history, tmp_path)
    assert (raw, history) == original
    assert projected[:-1] == history[:-1]
    assert projected[-1]["_row_id"] == 10 and projected[-1]["_db_persisted"]
    assert "api_content" not in projected[-1] and len(projected[-1]["content"]) < 1000
    archive = next(tmp_path.glob("*.json"))
    assert json.loads(archive.read_text(encoding="utf-8")) == {"session_id": "s", "messages": raw[-2:]}
    project_unanswered_retries(db, "s", history, tmp_path)
    assert len(list(tmp_path.glob("*.json"))) == 2


@pytest.mark.parametrize("case", ["ordinary", "single", "small", "answered"])
def test_ordinary_or_answered_messages_are_not_replaced(tmp_path, case):
    raw, history = histories()
    if case == "ordinary":
        raw = [{"role": "user", "content": "x" * 40000}] * 2
    elif case == "single":
        raw = raw[-1:]
    elif case == "small":
        raw = [{"role": "user", "content": "【kill+resume 修正指令】"}] * 2
    else:
        history.append({"role": "assistant", "content": "done"})
    db = SimpleNamespace(get_messages_as_conversation=lambda *a, **kw: raw)
    assert project_unanswered_retries(db, "s", history, tmp_path) is history
    assert not list(tmp_path.iterdir())


def test_archive_failure_is_fail_closed(tmp_path):
    raw, history = histories()
    invalid = tmp_path / "file"
    invalid.write_text("keep")
    db = SimpleNamespace(get_messages_as_conversation=lambda *a, **kw: raw)
    original = copy.deepcopy(history)
    with pytest.raises(OSError):
        project_unanswered_retries(db, "s", history, invalid)
    assert history == original


def test_installer_covers_cli_top_level_and_lease_reload_but_not_raw_display(tmp_path, monkeypatch):
    import sys
    from core.resume_projection import install_resume_projection
    raw, history = histories()
    class SessionDB:
        def get_messages_as_conversation(self, sid, include_ancestors=False,
                                         include_inactive=False, repair_alternation=False, **kw):
            return copy.deepcopy(history if repair_alternation else raw)
        def get_resume_conversations(self, sid):
            return copy.deepcopy(history), copy.deepcopy(raw)
    monkeypatch.setitem(sys.modules, "hermes_state", SimpleNamespace(SessionDB=SessionDB))
    install_resume_projection(tmp_path)
    install_resume_projection(tmp_path)
    db = SessionDB()
    assert db.get_messages_as_conversation("s") == raw
    assert db.get_messages_as_conversation("s", True, True, True)[-1]["content"].startswith("【ADGS")
    assert db.get_messages_as_conversation("s", repair_alternation=True)[-1]["content"].startswith("【ADGS")
    projected, display = db.get_resume_conversations("s")
    assert display == raw and projected[:-1] == history[:-1]
    assert len(projected[-1]["content"]) < 1000
