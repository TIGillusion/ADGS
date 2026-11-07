"""Hermes tool bridges for fixed CEO decisions and delegated coordinators."""
import json
import os
import threading
from pathlib import Path


def install_role_tools():
    import model_tools
    from agent import tool_executor

    schemas_file = os.environ.get("ADGS_CONTROL_TOOLS")
    decision_file = os.environ.get("ADGS_CONTROL_DECISION")
    coordinator_file = os.environ.get("ADGS_COORDINATOR_FLAG")
    schemas = json.loads(Path(schemas_file).read_text(encoding="utf-8")) if schemas_file else None
    names = {tool["function"]["name"] for tool in schemas} if schemas else set()
    if schemas:
        from tools.registry import registry
        for tool in schemas:
            fn = tool["function"]

            def submit(arguments, name=fn["name"], **_kwargs):
                destination = Path(decision_file)
                try:
                    # The parent reads only complete receipts after replace.
                    temporary = destination.with_suffix(".tmp")
                    with _receipt_lock:
                        if not destination.exists():
                            temporary.write_text(json.dumps({"name": name, "arguments": arguments},
                                                            ensure_ascii=False), encoding="utf-8")
                            temporary.replace(destination)
                except OSError:
                    return json.dumps({"error": "CEO 决策未能保存，请重试"}, ensure_ascii=False)
                # Parent owns tool dispatch and terminates this decision process.
                threading.Event().wait()

            registry.register(fn["name"], "adgs-ceo", fn, submit, override=True)
        model_tools.get_tool_definitions = lambda *args, **kwargs: schemas

    original = tool_executor._dispatch_authorized_once

    def dispatch(agent, state, ref, **kwargs):
        denied = bool(schemas and ref.name not in names)
        if not schemas and coordinator_file and Path(coordinator_file).is_file():
            allowed = {"read_file", "search_files", "list_files", "tool_search", "tool_describe"}
            mesh = ("list_nodes", "probe_node", "delegate_to_node", "continue_delegation", "wait_delegation",
                    "report_delegation_verdict", "escalate_delegation_verdict")
            denied = ref.name not in allowed and not any(ref.name.endswith("__" + name) for name in mesh)
        if denied:
            block = kwargs.get("scope_block")
            if not block and not schemas and ref.name == "tool_call":
                # A malformed deferred bridge has not resolved to an actual
                # tool. Preserve its parameter error instead of misleading
                # coordinators into thinking delegation itself is forbidden.
                from tools.tool_search import resolve_underlying_call
                _, _, block = resolve_underlying_call(getattr(ref, "args", {}))
            kwargs["scope_block"] = block or "本实例负责协调下属。请等待汇报、读取产物、验收或继续委派，业务代码交给工作节点。"
        return original(agent, state, ref, **kwargs)

    tool_executor._dispatch_authorized_once = dispatch


_receipt_lock = threading.Lock()
