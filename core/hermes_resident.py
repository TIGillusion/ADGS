"""Resident child entry, using the installed Hermes CLI's native chat turns."""
import os
import time

try:
    from .hermes_channel import HermesChannel, ChannelError
except ImportError:
    from hermes_channel import HermesChannel, ChannelError


def prepare_turn(cli, message):
    # A resident outlives the attempt that launched it. Receipt paths and role
    # constraints must follow the new contract, without recreating the Agent.
    for key in ('ADGS_CURRENT_TASK_FILE', 'ADGS_COORDINATOR_FLAG', 'ADGS_HERMES_RECEIPT'):
        value = message.get('environment', {}).get(key)
        if value is not None:
            os.environ[key] = value
    agent = getattr(cli, 'agent', None)
    if 'control_tools' in message:
        try:
            from .hermes_roles import update_control_tools
        except ImportError:
            from hermes_roles import update_control_tools
        update_control_tools(message['control_tools'])
    if agent is not None:
        import model_tools
        tools = model_tools.get_tool_definitions(enabled_toolsets=cli.enabled_toolsets,
            disabled_toolsets=cli.disabled_toolsets, quiet_mode=True)
        agent.tools = tools
        agent.valid_tool_names = {tool['function']['name'] for tool in tools}


def settle_background(cli):
    # Keep this turn and its scheduling slot until its business subprocesses
    # finish. MCP helpers are not in this native terminal-process registry.
    from tools.process_registry import process_registry
    while process_registry.count_running():
        time.sleep(.2)


def serve(cli, directory, *, exit_code, poll_interval=.05, before_turn=None, after_turn=None):
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
        token = message.get('output_token')
        if token:
            print('[ADGS_OUTPUT:' + token + ':start]', end='', flush=True)
        if before_turn:
            before_turn(cli, message)
        if 'control_tools' in message:
            try:
                from . import hermes_control
            except ImportError:
                import hermes_control
            hermes_control.begin(cli, directory, turn_id, message)
        cli._last_turn_result = None
        response = cli.chat(instruction)
        if after_turn:
            after_turn(cli)
        result = cli._last_turn_result
        agent = getattr(cli, 'agent', None)
        if getattr(agent, 'session_id', None):
            cli.session_id = agent.session_id
        if token:
            print('[ADGS_OUTPUT:' + token + ':end]', end='', flush=True)
        channel.publish(turn_id, 'completed', {
            'pid': os.getpid(), 'agent_id': id(agent), 'session_id': cli.session_id,
            'exit_code': exit_code(result), 'output': response or '',
            'error': str((result or {}).get('error') or '') if isinstance(result, dict) else 'No turn result',
            'control_result_received': bool(hermes_control.active.resolved) if 'control_tools' in message else None,
            'usage': {key: result[key] for key in ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens')
                      if isinstance(result, dict) and isinstance(result.get(key), (int, float))},
        })
        sequence += 1


def install_resident_entry(directory):
    import cli as native

    def run(cli, query, image, quiet, oneshot, stream_json=False):
        # Preserve native MCP initialization and the actual CLI chat pipeline,
        # including staging/persistence/compression/history settlement.
        cli._single_query_mode = True
        os.environ['HERMES_SINGLE_QUERY_SESSION'] = '1'
        if not cli._claim_active_session('cli', stderr=bool(quiet)):
            raise SystemExit(1)
        try:
            serve(cli, directory, exit_code=native._single_query_exit_code,
                  before_turn=prepare_turn, after_turn=settle_background)
        finally:
            native._finalize_single_query(cli)
        raise SystemExit(0)

    native._run_single_query_mode = run
