"""State snapshots are scoped, small, and distinguish runtime facts from checkpoints."""
import threading

from core.execution_store import ExecutionStore


def test_execution_summaries_are_session_scoped_and_do_not_expose_contracts(tmp_path):
    store = ExecutionStore(tmp_path / 'executions.sqlite')
    store.put('a', session_name='chat-a', node_id='ags_1', task_id='t', state='running', phase='queued', task_md='private contract', work_log='large private log')
    store.put('b', session_name='chat-b', node_id='ags_1', task_id='t', state='passed', phase='finished')
    result = store.session_states('chat-a')
    assert len(result) == 1 and result[0]['execution_id'] == 'a'
    assert result[0]['state'] == 'running' and result[0]['phase'] == 'queued'
    assert 'task_md' not in result[0] and 'work_log' not in result[0]
    assert store.session_states('missing') == []


def test_snapshot_and_execution_detail_report_live_waiting_and_restart_residue(tmp_path, monkeypatch):
    import utils.context_probe
    monkeypatch.setattr(utils.context_probe, 'probe_context_window', lambda *a, **kw: None)
    import web_app as web
    import web.execution_api as endpoints
    from core import node
    from core.terminal_runtime import TerminalStore
    import core.delegation_runtime as delegation

    store = ExecutionStore(tmp_path / 'executions.sqlite')
    for eid in ['waiting', 'stale', 'done']:
        store.put(eid, session_name='chat-a', node_id='ags_1', task_id=eid,
                  state='passed' if eid == 'done' else 'running', phase='execute')
    monkeypatch.setattr(web, 'execution_store', store)
    monkeypatch.setattr(endpoints, 'execution_store', store)
    monkeypatch.setattr(endpoints, 'terminal_store', TerminalStore(tmp_path / 'terminals'))
    monkeypatch.setattr(node, '_ACTIVE_EXECUTIONS', {'waiting': threading.Event()})
    monkeypatch.setattr(delegation, 'execution_wait_state', lambda eid: 'children' if eid == 'waiting' else None)
    monkeypatch.setattr(web, '_session_ceos', {})
    monkeypatch.setattr(web, '_running_sessions', {})
    monkeypatch.setitem(web.app.config, 'ADGS_PROXY_RUNTIME', False)
    client = web.app.test_client()
    snapshot = client.get('/api/state?session=chat-a').json
    states = {row['execution_id']: row for row in snapshot['executions']}
    assert snapshot['snapshot_at'] > 0
    assert states['waiting']['runtime_active'] is True and states['waiting']['waiting_for_children'] is True
    assert states['stale']['runtime_active'] is False
    assert states['done']['state'] == 'passed'
    detail = client.get('/api/executions/waiting').json['execution']
    assert detail['runtime_active'] is True and detail['waiting_for_children'] is True
    assert client.get('/api/executions/stale').json['execution']['runtime_active'] is False
    assert client.get('/api/state?session=chat-b').json['executions'] == []


def test_wait_events_describe_child_wait_then_slot_wait_and_resume(monkeypatch):
    from core.delegation_runtime import owned_execution_slot, lend_execution_slot, execution_wait_state
    from core.node import DynamicSemaphore
    from web.event_bus import bus
    events = []
    monkeypatch.setattr(bus, 'emit', lambda event, **fields: events.append((event, fields)))
    semaphore = DynamicSemaphore(1)
    assert semaphore.acquire()
    with owned_execution_slot('parent', semaphore, float('inf'), activity={'node': 'ags_1', 'session': 'chat-a'}):
        with lend_execution_slot('parent'):
            assert execution_wait_state('parent') == 'children'
            assert semaphore.acquire(timeout=.1)
            semaphore.release()
        assert execution_wait_state('parent') is None
    assert [fields['status'] for _, fields in events] == ['delegating', 'waiting', 'running']
    assert all(fields['execution_id'] == 'parent' and fields['session'] == 'chat-a' for _, fields in events)
    assert semaphore.acquire(timeout=.1)
    semaphore.release()


def test_closed_waiter_never_publishes_a_false_running_status(monkeypatch):
    from core.delegation_runtime import owned_execution_slot, lend_execution_slot
    from core.node import DynamicSemaphore
    from web.event_bus import bus
    states = []
    monkeypatch.setattr(bus, 'emit', lambda event, **fields: states.append(fields['status']))
    semaphore = DynamicSemaphore(1)
    assert semaphore.acquire()
    with owned_execution_slot('parent', semaphore, float('inf'), activity={'node': 'ags_1'}) as slot:
        with lend_execution_slot('parent'):
            slot.close()
    assert states == ['delegating']
