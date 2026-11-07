"""Native Hermes decisions bridged into the existing durable CEO dispatcher."""
import hashlib
import json
import re
import uuid

from config.config import config
from utils.chat import CHAT


class HermesCEOChat(CHAT):
    def __init__(self, owner, **kwargs):
        super().__init__(**kwargs)
        self.owner = owner

    def call_tools(self, messages, tools, tool_choice="required", **_kwargs):
        from core.hermes import HermesInstance, _configure_profile
        from core.terminal_runtime import run_hermes, terminal_store
        from core.ceo_policy import sync_ceo_role, coordinator_guidance

        ceo = self.owner
        client, model, _ = self._cfg
        worker_cfg = {"exec_model": model, "base_url": str(client.base_url),
                      "api_key": client.api_key, "context_length": config.ceo_ctx_window}
        try:
            if not HermesInstance("ceo").profile_dir.is_dir() and not HermesInstance.create("ceo"):
                raise RuntimeError("CEO Hermes profile 创建失败")
            if not _configure_profile("ceo", worker_cfg, overwrite_model=True, mesh_required=False):
                raise RuntimeError("CEO Hermes profile 配置失败")
            sync_ceo_role(HermesInstance("ceo").profile_dir)
            encoded = [json.dumps(message, ensure_ascii=False, sort_keys=True) for message in messages
                       if message.get("role") != "system"]
            policy = "\n".join(str(message.get("content") or "") for message in messages
                               if message.get("role") == "system")
            count = ceo._hermes_input_count
            prefix = hashlib.sha256("\n".join(encoded[:count]).encode()).hexdigest()
            if ceo._hermes_session_id and count and prefix == ceo._hermes_input_hash:
                context = encoded[count:]
            else:
                context = encoded
            # The first item is the actual CEO policy. Serialized prior exchanges
            # include authoritative tool outcomes and repaired/compressed history.
            query = (
                "你处于固定 CEO 职位，使用 Hermes 决策。所有派发、汇报和结束操作都通过本轮提供的 CEO 工具。"
                "工具提交后由 ADGS 调度器执行，下一轮会交还真实结果。不要自行执行任务代码。"
                "\n当前固定职责与协调技能（包括恢复原会话时）\n" + coordinator_guidance() +
                "\n当前 CEO 规则与任务事实\n" + policy +
                "\n以下为当前 CEO 上下文及最新执行结果，历史工具记录仅供参考。\n" +
                "\n".join(context))
            eid = ceo._hermes_execution_id
            if not eid:
                eid = "ceo-" + uuid.uuid4().hex
                ceo._hermes_execution_id = eid
            aid = uuid.uuid4().hex
            ceo._set_activity("deciding", attempt_id=aid)

            class Cancellation:
                def is_set(self):
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
            ceo._set_activity("reviewing", attempt_id=aid)
            if not receipt.is_file():
                # A normal prose reply is a missing tool decision, not a provider
                # outage. The durable CEO loop already repairs protocol failures
                # with a bounded retry and retains this same Hermes conversation.
                return {"error": "CEO Hermes 仅返回说明文字，尚未提交工具决策",
                        "error_kind": "protocol_error", "tool_calls": [],
                        "content": str(result.get("output") or "")[-12000:]}
            action = json.loads(receipt.read_text(encoding="utf-8"))
            return {"tool_calls": [{"id": "ceo-" + aid, **action}]}
        except Exception as exc:
            detail = str(exc).replace(client.api_key, "[已隐藏]")
            detail = re.sub(r"(?i)bearer\s+\S+|\bsk-[A-Za-z0-9_-]+", "[已隐藏]", detail)[:400]
            return {"error": f"CEO Hermes 决策失败（{type(exc).__name__}）：{detail}",
                    "error_kind": "provider_error", "tool_calls": []}
