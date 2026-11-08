"""Hermes tool bridges for fixed CEO decisions and delegated coordinators."""
import json
import os
import threading
from pathlib import Path


def install_role_tools(task_file=None):
    import model_tools
    from agent import tool_executor
    try:
        from .tool_bridge_compat import install_bridge_compat
    except ImportError:  # Hermes loads ADGS hooks from the core PYTHONPATH.
        from tool_bridge_compat import install_bridge_compat
    install_bridge_compat()

    schemas_file = os.environ.get("ADGS_CONTROL_TOOLS")
    decision_file = os.environ.get("ADGS_CONTROL_DECISION")
    schemas = json.loads(Path(schemas_file).read_text(encoding="utf-8")) if schemas_file else None
    names = {tool["function"]["name"] for tool in schemas} if schemas else set()
    try:
        from .contracts import read_file_only
    except ImportError:
        from contracts import read_file_only
    def only_read():
        current = os.environ.get("ADGS_CURRENT_TASK_FILE") or task_file
        return bool(not schemas and current and read_file_only(Path(current).read_text(encoding="utf-8")))

    if not schemas and hasattr(model_tools, "get_tool_definitions"):
        original_definitions = model_tools.get_tool_definitions
        def definitions(*args, **kwargs):
            tools = original_definitions(*args, **kwargs)
            return [tool for tool in tools if tool.get("function", {}).get("name") == "read_file"] if only_read() else tools
        model_tools.get_tool_definitions = definitions
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
        if only_read() and ref.name != "read_file":
            kwargs["scope_block"] = kwargs.get("scope_block") or "本任务明确只允许 read_file；请读取指定文件并在对话中汇报，不调用其他工具。"
            return original(agent, state, ref, **kwargs)
        coordinator_file = os.environ.get("ADGS_COORDINATOR_FLAG")
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
