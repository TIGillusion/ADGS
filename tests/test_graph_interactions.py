"""Graph input events retain exact contracts and distinguish continuation from new work."""
from types import SimpleNamespace
from web.event_bus import EventBus
import core.node as module


def fixture_node(monkeypatch):
    node = module.NODE.__new__(module.NODE)
    node.node_id = node.profile = 'worker'
    node.agent = SimpleNamespace(history=[])
    node._session_name = 'isolated'
    node._execution_id = 'execution'
    node._work_task_id = 'task'
    node._run_id = 'run'
    node._prepare_execution = lambda *a, **kw: None
    node._save_result = lambda result: result
    bus = EventBus()
    monkeypatch.setattr(module, 'bus', bus)
    writes = []
    monkeypatch.setattr(module.execution_store, 'put', lambda *a, **kw: writes.append((a, kw)))
    return node, bus, writes


def test_new_work_exposes_full_input_and_exact_parent(monkeypatch):
    node, bus, writes = fixture_node(monkeypatch)
    node._work_inner = lambda *a: {'success': True}
    content = '完整任务书' * 2000
    node.work(content, upper='parent', parent_execution_id='parent-execution')
    event = bus.history()[-1]
    assert event['type'] == 'graph.instruction'
    assert event['data']['task_md'] == content
    assert event['data']['context_mode'] == 'new'
    assert event['data']['parent_execution_id'] == 'parent-execution'
    assert event['data']['execution_id'] == 'execution'
    assert writes[0][1]['upper'] == 'parent'


def test_continuation_keeps_identity_and_records_only_new_instruction(monkeypatch):
    node, bus, writes = fixture_node(monkeypatch)
    saved = dict(node_id='worker', profile='worker', session_id='hermes-context',
                 task_md='原任务', task_id='task', run_id='run', session_name='isolated',
                 upper='parent', parent_execution_id='parent-execution')
    monkeypatch.setattr(module.execution_store, 'get', lambda execution: saved)
    received = []
    node._resume_hermes = lambda instruction, session: received.append((instruction, session)) or {}
    node._complete_execution = lambda *a: {'success': True}
    instruction = '请继续修复，保留已有成果' * 1000
    node.continue_execution('execution', instruction)
    event = bus.history()[-1]
    assert event['data']['context_mode'] == 'continue'
    assert event['data']['instruction'] == instruction
    assert event['data']['execution_id'] == 'execution'
    assert event['data']['parent_execution_id'] == 'parent-execution'
    assert received[0][1] == 'hermes-context'
