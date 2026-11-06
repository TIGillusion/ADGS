"""Bound legacy unanswered ADGS retries in a child process, never edit Hermes DB."""
import hashlib
import json
import logging
import os
import uuid
from pathlib import Path

_MARKERS = ("【kill+resume 修正指令】", "【返工指令 - 验收未通过】",
            "## 上层补充要求（保留原权限）", "【原会话续接：保留已完成成果，仅补缺项】")


def project_unanswered_retries(db, session_id, history, directory):
    # Do not touch completed tool/assistant turns or an ordinary large user input.
    if not history or history[-1].get("role") != "user":
        return history
    raw = db.get_messages_as_conversation(session_id, repair_alternation=False,
                                           include_row_ids=True)
    tail = []
    for message in reversed(raw):
        if message.get("role") != "user":
            break
        tail.append(message)
    tail.reverse()
    generated = sum(any(marker in str(m.get("content", "")) for marker in _MARKERS)
                    for m in tail)
    if generated < 2 or sum(len(str(m.get("content", ""))) for m in tail) < 32768:
        return history
    target_dir = Path(directory).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    archive = target_dir / f"unanswered-retries-{uuid.uuid4().hex}.json"
    payload = json.dumps({"session_id": session_id, "messages": tail}, ensure_ascii=False,
                         indent=2).encode("utf-8")
    # Archive must succeed before changing the in-memory model projection.
    with archive.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    reference = (f"【ADGS 失败续接历史恢复】原会话末尾有 {len(tail)} 条未得到助手响应的连续要求。\n"
                 "原始数据库与已执行的工具记录保留；这些要求完整归档于只读文件：\n"
                 f"{archive}\nSHA256: {hashlib.sha256(payload).hexdigest()}\n"
                 "累计要求没有取消。本次指令引用的完整合同仍为验收依据，"
                 "如需历史细节请分段读取归档。不要一次读回整份失败重试历史。")
    last = dict(history[-1])
    last["content"] = reference
    last.pop("api_content", None)  # Native wire projection prefers this over content.
    logging.getLogger(__name__).warning("ADGS resume projection archived %d unanswered turns (%d bytes) to %s",
                                        len(tail), len(payload), archive)
    return [*history[:-1], last]


def install_resume_projection(directory):
    # chat --oneshot uses HermesCLI's late loader; top-level -z uses a
    # different loader. Intercept their shared model-only DB projections,
    # including a reload after waiting for a cross-process turn lease.
    from hermes_state import SessionDB
    original = SessionDB.get_messages_as_conversation
    if getattr(original, "_adgs_projection", False):
        return
    original_resume = SessionDB.get_resume_conversations

    def messages(db, session_id, *args, **kwargs):
        history = original(db, session_id, *args, **kwargs)
        # Display/export/raw reads must remain verbatim. Positional flag use
        # is supported too: include_ancestors, include_inactive, repair.
        repair = kwargs.get("repair_alternation", args[2] if len(args) > 2 else False)
        if repair:
            return project_unanswered_retries(db, session_id, history, directory)
        return history

    def resume(db, session_id):
        history, display = original_resume(db, session_id)
        return project_unanswered_retries(db, session_id, history, directory), display

    messages._adgs_projection = True
    SessionDB.get_messages_as_conversation = messages
    SessionDB.get_resume_conversations = resume
