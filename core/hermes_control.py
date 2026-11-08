"""A native CEO tool call waits for its actual durable dispatcher result."""
import functools
import json
import os
import threading
import time

try:
    from .hermes_channel import HermesChannel
except ImportError:
    from hermes_channel import HermesChannel

active = None


class DecisionTurn:
    def __init__(self, cli, directory, turn_id, message):
        self.cli = cli
        self.channel = HermesChannel(directory)
        self.turn_id = turn_id
        self.token = message.get('output_token')
        self.lock = threading.Lock()
        self.submitted = False
        self.resolved = False

    def submit(self, name, arguments):
        with self.lock:
            if self.submitted:
                return json.dumps({'error': '本次只接受一个 CEO 决策；此额外调用未执行。'}, ensure_ascii=False)
            self.submitted = True
            agent = self.cli.agent
            if self.token:
                print('[ADGS_OUTPUT:' + self.token + ':end]', end='', flush=True)
            self.channel.publish(self.turn_id, 'decision', {
                'name': name, 'arguments': arguments, 'pid': os.getpid(),
                'session_id': agent.session_id, 'agent_id': id(agent)})
        while True:
            result = self.channel.receive(self.turn_id, 'tool-result')
            if result is not None:
                content = result.get('content')
                if not isinstance(content, str):
                    raise ValueError('CEO result must contain the complete tool content')
                self.resolved = True
                return content
            if self.channel.receive('process', 'shutdown') is not None:
                raise RuntimeError('ADGS process shutting down; decision retained')
            time.sleep(.05)


def begin(cli, directory, turn_id, message):
    global active
    active = DecisionTurn(cli, directory, turn_id, message)


def install_decision_boundary():
    # Let native Hermes persist the real tool result, then settle its chat turn
    # without spending another model call before ADGS supplies the next input.
    # This keeps the same CLI/Agent and its canonical transcript intact.
    from agent import conversation_loop
    original = conversation_loop.run_tool_round

    @functools.wraps(original)
    def run(agent, **kwargs):
        verdict = original(agent, **kwargs)
        if active and active.resolved and verdict.action == 'continue' and not verdict.failed:
            verdict.action = 'break'
            verdict.final_response = ''
            verdict._turn_exit_reason = 'adgs_decision_settled'
        return verdict

    conversation_loop.run_tool_round = run
