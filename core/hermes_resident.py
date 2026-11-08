"""Resident child entry, using the installed Hermes CLI's native chat turns."""
import os
import time

try:
    from .hermes_channel import HermesChannel, ChannelError
except ImportError:
    from hermes_channel import HermesChannel, ChannelError


def serve(cli, directory, *, exit_code, poll_interval=.05):
    channel = HermesChannel(directory)
    sequence = 1
    channel.publish('process', 'ready', {'pid': os.getpid(), 'session_id': cli.session_id})
    while True:
        turn_id = f'turn{sequence:08d}'
        if channel.receive('process', 'shutdown') is not None:
            return
        message = channel.receive(turn_id, 'input')
        if message is None:
            time.sleep(poll_interval)
            continue
        instruction = message.get('instruction')
        if not isinstance(instruction, str) or not instruction.strip():
            raise ChannelError('A turn requires a complete nonempty instruction')
        # An acknowledgement means this process accepted the entire verified
        # envelope, not that the model has finished its response.
        channel.publish(turn_id, 'received', {'pid': os.getpid(), 'session_id': cli.session_id})
        cli._last_turn_result = None
        response = cli.chat(instruction)
        result = cli._last_turn_result
        agent = getattr(cli, 'agent', None)
        if getattr(agent, 'session_id', None):
            cli.session_id = agent.session_id
        channel.publish(turn_id, 'completed', {
            'pid': os.getpid(), 'agent_id': id(agent), 'session_id': cli.session_id,
            'exit_code': exit_code(result), 'output': response or '',
            'error': str((result or {}).get('error') or '') if isinstance(result, dict) else 'No turn result',
        })
        sequence += 1


def install_resident_entry(directory):
    import cli as native

    def run(cli, query, image, quiet, oneshot, stream_json=False):
        # Preserve native MCP initialization and the actual CLI chat pipeline,
        # including staging/persistence/compression/history settlement.
        cli._single_query_mode = True
        try:
            serve(cli, directory, exit_code=native._single_query_exit_code)
        finally:
            native._finalize_single_query(cli)
        raise SystemExit(0)

    native._run_single_query_mode = run
