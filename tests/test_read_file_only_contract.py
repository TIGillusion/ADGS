import sys
from types import ModuleType, SimpleNamespace

from core.contracts import read_file_only
from core.hermes_roles import install_role_tools


def test_explicit_limit_applies_to_current_task_not_quoted_child():
    assert read_file_only('你只允许使用 read_file 读取本工作区内的文件。')
    assert read_file_only('## 当前任务\n独立复核。\n## 需求明细\n- 你只能 read_file。')
    assert not read_file_only('## 总体任务\n你只能 read_file。\n## 当前任务\n协调下属。')
    assert not read_file_only('> 你只能 read_file。\n为下属编写只读任务书。')
    assert not read_file_only('子节点只能 read_file。')
    assert not read_file_only('你只能使用 read_file 和 search_files。')


def test_tool_schema_and_dispatch_enforce_explicit_read_file_only(tmp_path, monkeypatch):
    monkeypatch.delenv('ADGS_CONTROL_TOOLS', raising=False)
    monkeypatch.delenv('ADGS_COORDINATOR_FLAG', raising=False)
    model = ModuleType('model_tools')
    model.get_tool_definitions = lambda **kw: [{'function': {'name': n}} for n in ('read_file', 'search_files', 'terminal')]
    agent = ModuleType('agent')
    calls = []
    executor = SimpleNamespace(_dispatch_authorized_once=lambda *a, **kw: calls.append(kw))
    agent.tool_executor = executor
    monkeypatch.setitem(sys.modules, 'model_tools', model)
    monkeypatch.setitem(sys.modules, 'agent', agent)
    contract = tmp_path/'task.md'
    contract.write_text('## 当前任务\n你只允许使用 read_file 读取五个文件。', encoding='utf-8')
    install_role_tools(task_file=contract)
    assert [t['function']['name'] for t in model.get_tool_definitions()] == ['read_file']
    for name in ('search_files', 'terminal', 'write_file', 'skill_manage', 'mcp__mesh__delegate_to_node'):
        executor._dispatch_authorized_once(None, None, SimpleNamespace(name=name), scope_block=None)
        assert 'read_file' in calls[-1]['scope_block']
    executor._dispatch_authorized_once(None, None, SimpleNamespace(name='read_file'), scope_block=None)
    assert calls[-1]['scope_block'] is None
    # A subsequent turn updates the task and restores the original tool catalog;
    # the resident Agent must not keep the first turn's read-only closure.
    next_contract = tmp_path/'next.md'
    next_contract.write_text('## 当前任务\n编写核查脚本。', encoding='utf-8')
    monkeypatch.setenv('ADGS_CURRENT_TASK_FILE', str(next_contract))
    assert [t['function']['name'] for t in model.get_tool_definitions()] == ['read_file', 'search_files', 'terminal']
    executor._dispatch_authorized_once(None, None, SimpleNamespace(name='terminal'), scope_block=None)
    assert calls[-1]['scope_block'] is None
