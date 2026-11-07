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
        return original(normalize_bridge_arguments(arguments))

    normalize._adgs_json_calls_compat = True
    bridge.normalize_tool_call_entries = normalize
