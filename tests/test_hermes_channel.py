from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.hermes_channel import ChannelError, HermesChannel


def test_partial_input_is_never_visible(tmp_path):
    channel = HermesChannel(tmp_path)
    (tmp_path/'.writer.pending').write_bytes(b'{"payload":')
    assert channel.receive('turn1', 'input') is None
    text = ('中文消息\n```python\nprint("尚未输入完")\n```\n😀' * 10000)
    assert channel.publish('turn1', 'input', {'text': text})
    assert channel.receive('turn1', 'input') == {'text': text}


def test_retry_and_conflict_preserve_original_input(tmp_path):
    channel = HermesChannel(tmp_path)
    assert channel.publish('a', 'input', {'text': 'first'})
    assert not channel.publish('a', 'input', {'text': 'first'})
    with pytest.raises(ChannelError, match='different input'):
        channel.publish('a', 'input', {'text': 'second'})
    assert channel.receive('a', 'input') == {'text': 'first'}
    assert not list(tmp_path.glob('*.pending'))


def test_concurrent_publish_has_one_winner(tmp_path):
    channel = HermesChannel(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: channel.publish('a', 'input', {'text': '完整'}), range(24)))
    assert sum(outcomes) == 1
    assert channel.receive('a', 'input') == {'text': '完整'}


def test_received_does_not_imply_completed(tmp_path):
    channel = HermesChannel(tmp_path)
    channel.publish('a', 'input', {'text': 'work'})
    channel.publish('a', 'received', {'pid': 123})
    assert channel.receive('a', 'completed') is None
    channel.publish('a', 'completed', {'result': 'done'})
    assert channel.receive('a', 'received') == {'pid': 123}
    assert channel.receive('a', 'completed') == {'result': 'done'}


def test_corruption_is_not_consumed(tmp_path):
    channel = HermesChannel(tmp_path)
    channel.publish('a', 'input', {'text': 'original'})
    path = tmp_path/'a.input.json'
    envelope = json.loads(path.read_text(encoding='utf-8'))
    envelope['payload']['text'] = 'truncated'
    path.write_text(json.dumps(envelope), encoding='utf-8')
    with pytest.raises(ChannelError, match='integrity'):
        channel.receive('a', 'input')


@pytest.mark.parametrize('identifier', ['../escape', '', 'a/b'])
def test_identity_cannot_escape_channel(tmp_path, identifier):
    with pytest.raises(ChannelError):
        HermesChannel(tmp_path).publish(identifier, 'input', {'text': 'x'})
