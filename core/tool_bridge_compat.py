"""Normalize one unambiguous provider JSON layer, preserving Hermes validation."""
import functools
import json


def normalize_bridge_arguments(arguments):
    if not isinstance(arguments, dict) or not isinstance(arguments.get('calls'), str):
        return arguments
    try:
        calls = json.loads(arguments['calls'])
    except (ValueError, TypeError):
        return arguments
    return {**arguments, 'calls': calls} if isinstance(calls, list) else arguments


def install_bridge_compat(bridge=None):
    if bridge is None:
        try:
            from tools import tool_search as bridge
        except ImportError:
            return
    original = getattr(bridge, 'normalize_tool_call_entries', None)
    if not callable(original) or getattr(original, '_adgs_json_calls_compat', False):
        return

    @functools.wraps(original)
    def normalize(arguments):
        # Native parsing still validates names, argument schemas and batching.
        if isinstance(arguments, dict) and isinstance(arguments.get('calls'), str):
            try:
                json.loads(arguments['calls'])
            except json.JSONDecodeError as exc:
                # Do not conceal malformed JSON behind the native array-type
                # error or guess missing task content. Real Qwen long task
                # dispatches otherwise retry by truncating the contract.
                return None, (
                    f"tool_call 'calls' contains malformed JSON at line {exc.lineno}, "
                    f"column {exc.colno}: {exc.msg}. No tool was executed. "
                    "Pass calls as a native JSON array, not a JSON-encoded string, "
                    'for example {"calls":[{"name":"tool_name","arguments":{...}}]}. '
                    "Preserve the complete task text; do not truncate it or delegate a probe task to bypass this syntax error.")
        return original(normalize_bridge_arguments(arguments))

    normalize._adgs_json_calls_compat = True
    bridge.normalize_tool_call_entries = normalize
