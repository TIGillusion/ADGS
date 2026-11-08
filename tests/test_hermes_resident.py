import threading
import time
import sys
from types import SimpleNamespace

from core.hermes_channel import HermesChannel
from core.hermes_resident import serve, settle_background


def test_three_turns_keep_agent_history_and_consume_each_input_once(tmp_path):
    channel = HermesChannel(tmp_path)
    agent = SimpleNamespace(session_id='original')
    history = []
    cli = SimpleNamespace(agent=agent, session_id='original')
    def chat(text):
        history.append(text)
        cli._last_turn_result = {'completed': True}
        return '\n'.join(history)
    cli.chat = chat
    thread = threading.Thread(target=serve, args=(cli,tmp_path), kwargs={'exit_code':lambda r:0}, daemon=True)
    thread.start()
    try:
        for index, text in enumerate(['首轮\n中文', '第二轮\n```python\nprint(1)\n```', '最后一轮'], 1):
            tid=f'turn{index:08d}'
            channel.publish(tid, 'input', {'instruction':text})
            assert not channel.publish(tid, 'input', {'instruction':text})
            deadline=time.monotonic()+5
            while channel.receive(tid,'completed') is None and time.monotonic()<deadline:
                time.sleep(.01)
            result=channel.receive(tid,'completed')
            assert result['agent_id']==id(agent) and result['session_id']=='original'
            assert result['output']=='\n'.join(history)
        assert len(history)==3
    finally:
        channel.publish('process','shutdown',{})
        thread.join(3)
    assert not thread.is_alive()


def test_background_work_must_finish_before_turn_completion(monkeypatch, tmp_path):
    running = threading.Event()
    running.set()
    checked = threading.Event()
    def count_running():
        checked.set()
        return int(running.is_set())
    monkeypatch.setitem(sys.modules, 'tools.process_registry', SimpleNamespace(
        process_registry=SimpleNamespace(count_running=count_running)))
    cli = SimpleNamespace(session_id='owned', agent=SimpleNamespace(session_id='owned'))
    def chat(text):
        cli._last_turn_result = {'completed': True}
        return 'Model returned while its background work was still running'
    cli.chat = chat
    channel = HermesChannel(tmp_path)
    thread = threading.Thread(target=serve, args=(cli, tmp_path),
        kwargs={'exit_code': lambda r: 0, 'after_turn': settle_background}, daemon=True)
    thread.start()
    try:
        channel.publish('turn00000001', 'input', {'instruction': 'run background work'})
        assert checked.wait(3)
        assert channel.receive('turn00000001', 'received') is not None
        assert channel.receive('turn00000001', 'completed') is None
        running.clear()
        deadline = time.monotonic() + 3
        while channel.receive('turn00000001', 'completed') is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert channel.receive('turn00000001', 'completed')['session_id'] == 'owned'
    finally:
        running.clear()
        channel.publish('process', 'shutdown', {})
        thread.join(3)
    assert not thread.is_alive()
