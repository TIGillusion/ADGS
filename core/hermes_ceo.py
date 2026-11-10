"""Native Hermes decisions bridged into the existing durable CEO dispatcher."""
import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import closing

from config.config import config
from core.compress import TASK_FACTS_MARKER, _count_tokens, compress
from utils.chat import CHAT


def _native_context(home, session_id):
    """Measure retained history even for sessions created before budget tracking."""
    db = home / "state.db"
    if not session_id or not db.is_file():
        return None
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as conn:
        session = conn.execute("SELECT model FROM sessions WHERE id=?", (session_id,)).fetchone()
        if session is None:
            return None
        chars, count = conn.execute(
            "SELECT COALESCE(SUM(COALESCE(length(content), 0) + COALESCE(length(tool_calls), 0)), 0), COUNT(*) "
            "FROM messages WHERE session_id=?", (session_id,)).fetchone()
    return session[0], (chars + 1) // 2 + count * 8


class HermesCEOChat(CHAT):
    def __init__(self, owner, **kwargs):
        super().__init__(**kwargs)
        self.owner = owner

    def settle_pending(self, messages):
        """Return the real external outcome to a waiting native tool exactly once."""
        from core.hermes_process import resident_pool
        ceo = self.owner
        pending = getattr(ceo, '_hermes_pending_tool_id', None)
        if not pending:
            return None
        with resident_pool.lock:
            saved = resident_pool.entries.get(ceo._hermes_execution_id)
        if not saved or not saved[0].alive():
            # Recovery will load the durable external outcome in the next
            # query; never re-execute a submitted action to repair a transcript.
            return None
        process = saved[0]
        outcome = next((message for message in reversed(messages)
                        if message.get('role') == 'tool' and message.get('tool_call_id') == pending), None)
        content = (outcome.get('content') or '') if outcome else (
            'ADGS 未执行此决策：用户引导、停止请求或参数校验使它被放弃。请按后续输入重新决策。')
        result = process.resolve_decision(str(content))
        if result.get('exit_code') != 0:
            raise RuntimeError(result.get('error') or 'CEO 工具结果未能完成原生会话交接')
        ceo._hermes_session_id = result['session_id']
        ceo._hermes_pending_tool_id = None
        ceo._hermes_settled_tool_id = pending
        return pending

    def call_tools(self, messages, tools, tool_choice="required", **_kwargs):
        from core.hermes import HermesInstance, _configure_profile
        from core.hermes_process import resident_pool
        from core.terminal_runtime import run_hermes, terminal_store
        from core.ceo_policy import sync_ceo_role, coordinator_guidance

        ceo = self.owner
        persistent = os.environ.get('ADGS_HERMES_RESIDENT', '1') == '1'
        client, model, _ = self._cfg
        worker_cfg = {"exec_model": model, "base_url": str(client.base_url),
                      "api_key": client.api_key, "context_length": config.ceo_ctx_window}
        try:
            settled = self.settle_pending(messages) if persistent else None
            if not HermesInstance("ceo").profile_dir.is_dir() and not HermesInstance.create("ceo"):
                raise RuntimeError("CEO Hermes profile 创建失败")
            if not _configure_profile("ceo", worker_cfg, overwrite_model=True, mesh_required=False):
                raise RuntimeError("CEO Hermes profile 配置失败")
            home = HermesInstance("ceo").profile_dir
            sync_ceo_role(home)
            external = [message for message in messages if message.get('role') != 'system']
            encoded = [json.dumps(message, ensure_ascii=False, sort_keys=True) for message in external]
            policy = "\n".join(str(message.get("content") or "") for message in messages
                               if message.get("role") == "system")
            fixed_policy, facts_marker, facts = policy.partition(TASK_FACTS_MARKER)
            policy_hash = hashlib.sha256(fixed_policy.encode()).hexdigest()
            count = ceo._hermes_input_count
            prefix = hashlib.sha256("\n".join(encoded[:count]).encode()).hexdigest()
            valid_prefix = 0 < count <= len(encoded) and prefix == ceo._hermes_input_hash
            if ceo._hermes_session_id and valid_prefix:
                context_messages = external[count:]
            else:
                context_messages = external
            if persistent:
                settled = settled or getattr(ceo, '_hermes_settled_tool_id', None)
                if settled:
                    context_messages = [message for message in context_messages
                        if message.get('tool_call_id') != settled and not any(
                            call.get('id') == settled for call in message.get('tool_calls', []) or [])]
            def prompt(context, continuation):
                rendered = "\n".join(json.dumps(message, ensure_ascii=False, sort_keys=True) for message in context)
                if continuation:
                    changed_policy = policy_hash != getattr(ceo, '_hermes_policy_hash', '')
                    update = "当前规则更新\n" + fixed_policy + "\n" if changed_policy else ""
                    return (update + (facts_marker + facts + '\n' if facts_marker else '') +
                            "以下为新增输入，请结合原会话中真实工具结果继续决策。每次调用一个 CEO 工具。\n" + rendered)
                return ("你处于固定 CEO 职位，使用 Hermes 决策。所有派发、汇报和结束操作都通过本轮提供的 CEO 工具。"
                        "工具提交后由 ADGS 调度器执行，下一轮会交还真实结果。不要自行执行任务代码。"
                        "\n当前固定职责与协调技能（包括恢复原会话时）\n" + coordinator_guidance() +
                        "\n当前 CEO 规则与任务事实\n" + policy +
                        "\n以下为当前 CEO 上下文及最新执行结果，历史工具记录仅供参考。\n" + rendered)

            def prompt_cost(query):
                return _count_tokens([{"role": "user", "content": query}])

            # Leave room for the provider's completion reservation and Hermes' own prompt.
            input_budget = config.ceo_ctx_window // 2
            query = prompt(context_messages, persistent and ceo._hermes_session_id)
            previous = _native_context(home, ceo._hermes_session_id)
            if previous:
                old_model, used = previous
                model_changed = old_model and old_model != model
                prefix_changed = count and not valid_prefix
                if model_changed or prefix_changed or used + prompt_cost(query) > input_budget:
                    # Any pending tool was settled above. Keep the ADGS execution
                    # ID and build a new native prompt from its authoritative history.
                    with resident_pool.lock:
                        resident = resident_pool.entries.get(ceo._hermes_execution_id)
                    if resident:
                        resident_pool.discard(ceo._hermes_execution_id, resident[0])
                    ceo._hermes_session_id = None
                    ceo._hermes_pending_tool_id = None
                    ceo._hermes_settled_tool_id = None
                    ceo._hermes_input_count = 0
                    ceo._hermes_input_hash = ""
                    context_messages = external
                    query = prompt(context_messages, False)
            if prompt_cost(query) > input_budget:
                compact = compress([{"role": "system", "content": policy}, *context_messages],
                                   ctx_window=config.ceo_ctx_window,
                                   budget_ratio=min(config.compress_budget, 0.25))
                query = prompt([message for message in compact if message.get('role') != 'system'],
                               persistent and ceo._hermes_session_id)
                ceo._compress_count = getattr(ceo, '_compress_count', 0) + 1
            if prompt_cost(query) > input_budget:
                raise ValueError("CEO 规则和任务摘要超出模型输入预算")
            eid = ceo._hermes_execution_id
            if not eid:
                eid = "ceo-" + uuid.uuid4().hex
                ceo._hermes_execution_id = eid
            aid = uuid.uuid4().hex
            ceo._set_activity("deciding", attempt_id=aid)

            class Cancellation:
                def is_set(self):
                    if persistent:
                        stopped = ceo.__dict__.get('_stop_requested')
                        return bool(stopped and stopped())
                    return ceo._has_pending_guidance()

            result = run_hermes("ceo", query, model=model, cwd=config.workspace,
                               session_id=ceo._hermes_session_id, execution_id=eid,
                               attempt_id=aid, run_id=ceo._run_id, task_id="ceo",
                               timeout=None, inactivity_timeout=config.inactivity_timeout,
                               cancel=Cancellation(), control_tools=tools)
            if result.get("session_id"):
                ceo._hermes_session_id = result["session_id"]
            receipt = terminal_store.paths(eid, aid) / "ceo-decision.json"
            if result.get("exit_code") != 0:
                ceo._set_activity("interrupted")
                return {"error": result.get("error") or "CEO Hermes 未提交决策",
                        "error_kind": "provider_error", "tool_calls": []}
            ceo._hermes_input_count = len(encoded)
            ceo._hermes_input_hash = hashlib.sha256("\n".join(encoded).encode()).hexdigest()
            if persistent:
                ceo._hermes_policy_hash = hashlib.sha256(fixed_policy.encode()).hexdigest()
            ceo._set_activity("reviewing", attempt_id=aid)
            if not receipt.is_file():
                # A normal prose reply is a missing tool decision, not a provider
                # outage. The durable CEO loop already repairs protocol failures
                # with a bounded retry and retains this same Hermes conversation.
                return {"error": "CEO Hermes 仅返回说明文字，尚未提交工具决策",
                        "error_kind": "protocol_error", "tool_calls": [],
                        "content": str(result.get("output") or "")[-12000:]}
            action = json.loads(receipt.read_text(encoding="utf-8"))
            if persistent and result.get('control_decision'):
                ceo._hermes_pending_tool_id = 'ceo-' + aid
            return {"tool_calls": [{"id": "ceo-" + aid, **action}]}
        except Exception as exc:
            detail = str(exc).replace(client.api_key, "[已隐藏]")
            detail = re.sub(r"(?i)bearer\s+\S+|\bsk-[A-Za-z0-9_-]+", "[已隐藏]", detail)[:400]
            return {"error": f"CEO Hermes 决策失败（{type(exc).__name__}）：{detail}",
                    "error_kind": "provider_error", "tool_calls": []}
