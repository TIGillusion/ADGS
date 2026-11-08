import sys
from types import ModuleType, SimpleNamespace

from core.hermes_roles import install_role_tools


def test_mesh_has_native_schemas_without_expanding_other_deferred_tools(monkeypatch):
    monkeypatch.delenv('ADGS_CONTROL_TOOLS', raising=False)
    monkeypatch.delenv('ADGS_CURRENT_TASK_FILE', raising=False)
    mesh = {'function': {'name': 'mcp__mesh__delegate_to_node', 'parameters': {
        'type': 'object', 'properties': {'task_md': {'type': 'string'}}, 'required': ['task_md']}}}
    read = {'function': {'name': 'read_file'}}
    bridge = {'function': {'name': 'tool_call'}}
    other = {'function': {'name': 'mcp__other__send'}}
    visible = [read, bridge]
    calls = []
    def native(enabled_toolsets=None, disabled_toolsets=None, quiet_mode=False, skip_tool_search_assembly=False):
        calls.append((enabled_toolsets, disabled_toolsets, quiet_mode, skip_tool_search_assembly))
        if 'mesh' in (disabled_toolsets or []):
            return [read, other] if skip_tool_search_assembly else visible
        return [read, mesh, other] if skip_tool_search_assembly else visible
    model = ModuleType('model_tools'); model.get_tool_definitions = native
    agent = ModuleType('agent')
    agent.tool_executor = SimpleNamespace(_dispatch_authorized_once=lambda *a, **kw: None)
    monkeypatch.setitem(sys.modules, 'model_tools', model)
    monkeypatch.setitem(sys.modules, 'agent', agent)
    install_role_tools()
    assert model.get_tool_definitions(['mesh'], ['other'], True) == [read, bridge, mesh]
    assert calls[-2:] == [(['mesh'], ['other'], True, False), (['mesh'], ['other'], True, True)]
    assert visible == [read, bridge]
    assert model.get_tool_definitions(disabled_toolsets=['mesh']) == visible
    # Raw lookup from native bridge validation must not recursively reassemble.
    before = len(calls)
    assert model.get_tool_definitions(skip_tool_search_assembly=True) == [read, mesh, other]
    assert len(calls) == before + 1
    assert model.get_tool_definitions(None, None, True, False) == [read, bridge, mesh]
