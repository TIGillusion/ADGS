from core.node import NODE
import pytest


def test_model_context_rejection_does_not_resume_unpersisted_session(monkeypatch):
    node = NODE.__new__(NODE)
    node._deadline = float('inf')
    log = {
        'exit_code': 1,
        'session_id': 'early-session-id',
        'output': "Hermes couldn't start the model connection: Model worker has a context window of 32,768 tokens, below the minimum 64,000 required by Hermes Agent.",
    }
    monkeypatch.setattr(node, '_resume_hermes', lambda *args: (_ for _ in ()).throw(AssertionError('Must not resume an unpersisted startup session')))
    monkeypatch.setattr(node, '_unjudged', lambda task, output, outcome, reason: {'outcome': outcome, 'reason': reason})
    result = node._complete_execution('task', log, 'ceo', False)
    assert result['outcome'] == 'interrupted'
    assert result['reason'] == "hermes couldn't start the model connection"


@pytest.mark.parametrize('message', [
    'HTTP 400: litellm.ContextWindowExceededError: No models have context window large enough',
    'Context Window exceeded for given call. Got 386833; maximum 262144',
])
def test_provider_context_overflow_is_not_sent_to_worker_correction(monkeypatch, message):
    node = NODE.__new__(NODE)
    node._deadline = float('inf')
    log = {'exit_code': 1, 'session_id': 'existing-session', 'output': message}
    monkeypatch.setattr(node, '_resume_hermes', lambda *args:
        (_ for _ in ()).throw(AssertionError('Do not enlarge context with correction')))
    monkeypatch.setattr(node, '_unjudged', lambda task, output, outcome, reason:
        {'outcome': outcome, 'reason': reason})
    result = node._complete_execution('contract', log, 'ceo', True)
    assert result['outcome'] == 'interrupted'
