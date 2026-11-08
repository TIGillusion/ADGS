import threading
import time
from types import SimpleNamespace

from core.hermes_channel import HermesChannel
from core.hermes_resident import serve


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
