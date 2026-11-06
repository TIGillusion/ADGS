"""Session graph replay must outlive the bus tail and include full A2A exchanges."""
from web.event_bus import EventBus


def test_session_graph_events_reads_durable_history(tmp_path):
    bus = EventBus()
    bus.configure_store(tmp_path / 'events.sqlite')
    task = bus.emit('graph.task', session='old', task='t1')
    delegated = bus.emit('a2a.delegate', session='old', delegation_id='d1', summary='x' * 3000)
    verdict = bus.emit('a2a.verdict', session='old', delegation_id='d1', passed=True)
    bus.emit('a2a.delegate', session='other', delegation_id='other')
    bus.emit('terminal.output', session='old', text='noise')
    bus.clear()
    result = bus.session_graph_events('old')
    assert result == [task, delegated, verdict]
    assert len(result[1]['data']['summary']) == 3000
    bus._db.close()


def test_session_graph_events_without_database():
    bus = EventBus()
    event = bus.emit('a2a.delegate', session='one', delegation_id='d')
    user = bus.emit('chat.user', session='one', text='full input')
    bus.emit('a2a.delegate', session='two', delegation_id='d2')
    assert bus.session_graph_events('one') == [event, user]
