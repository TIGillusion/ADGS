"""Owned resident processes and explicit per-turn terminal output boundaries."""
import atexit
import codecs
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path

from core.hermes_channel import HermesChannel


class TurnOutput:
    def __init__(self, token, sink):
        self.start = '[ADGS_OUTPUT:' + token + ':start]'
        self.end = '[ADGS_OUTPUT:' + token + ':end]'
        self.sink = sink
        self.drained = threading.Event()


class OutputRouter:
    """Handle arbitrary pipe/UTF-8 chunking without attaching late logs to a new turn."""
    def __init__(self, outside=lambda text: None):
        self.outside = outside
        self.route = None
        self.inside = False
        self.buffer = ''
        self.lock = threading.RLock()

    def bind(self, route):
        with self.lock:
            if self.route and not self.route.drained.is_set():
                raise RuntimeError('Previous turn output has not drained')
            self.route = route
            self.inside = False

    def feed(self, text, final=False):
        with self.lock:
            self.buffer += text
            while self.buffer:
                route = self.route
                if not route or route.drained.is_set():
                    self.outside(self.buffer); self.buffer = ''; break
                marker = route.end if self.inside else route.start
                index = self.buffer.find(marker)
                sink = route.sink if self.inside else self.outside
                if index >= 0:
                    if index: sink(self.buffer[:index])
                    self.buffer = self.buffer[index+len(marker):]
                    if self.inside:
                        self.inside = False
                        route.drained.set()
                    else:
                        self.inside = True
                    continue
                # Hold only a suffix that could actually be a split marker.
                # Holding marker_length bytes unconditionally hides short tool
                # output for the entire time that a long-running tool is busy.
                pending = 0
                if not final:
                    for size in range(min(len(self.buffer), len(marker)-1), 0, -1):
                        if marker.startswith(self.buffer[-size:]):
                            pending = size
                            break
                safe = len(self.buffer)-pending
                if safe:
                    sink(self.buffer[:safe]); self.buffer = self.buffer[safe:]
                break


class ResidentProcess:
    def __init__(self, command, *, cwd, env, directory, gate, backend='pipe', session_id=None):
        from core.terminal_runtime import WindowsJob, ConPtyProcess
        self.directory = Path(directory)
        self.channel = HermesChannel(self.directory/'channel')
        self.lock = threading.RLock()
        self.sequence = 0
        self.current = None
        self.session_id = session_id
        self.reader_error = None
        self.session_leases = {}
        self.backend = backend
        self.job = WindowsJob() if os.name == 'nt' else None
        self.proc = None
        self.closed = False
        self.console = (self.directory/'process.log').open('a', encoding='utf-8')
        self.router = OutputRouter()
        child_env = dict(env, ADGS_PERSISTENT_CHANNEL=str(self.directory/'channel'))
        try:
            if backend == 'conpty':
                self.proc = ConPtyProcess(command, cwd, child_env)
            else:
                self.proc = subprocess.Popen(command, cwd=cwd, env=child_env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,
                    start_new_session=os.name!='nt')
            if self.job: self.job.assign(self.proc.pid)
            self.reader = threading.Thread(target=self._read, daemon=True, name='adgs-resident-output')
            self.reader.start()
            Path(gate).touch()
        except BaseException:
            self.close(grace=0)
            raise

    @property
    def pid(self):
        return self.proc.pid if self.proc else None

    def alive(self):
        if self.closed or self.proc is None: return False
        return self.proc.isalive() if self.backend=='conpty' else self.proc.poll() is None

    def _read(self):
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        try:
            while True:
                if self.backend=='conpty':
                    try: text = self.proc.read()
                    except EOFError: break
                else:
                    raw = self.proc.stdout.read1(65536)
                    if not raw: break
                    text = decoder.decode(raw)
                if text:
                    self.console.write(text); self.console.flush()
                    self.router.feed(text)
            self.router.feed(decoder.decode(b'',final=True), final=True)
        except Exception as exc:
            self.reader_error = str(exc)

    def ready(self):
        result = self.channel.receive('process', 'ready')
        if result:
            sid = result['session_id']
            if self.session_id and self.sequence==0 and self.session_id!=sid:
                raise RuntimeError('Resident restored a different session')
            if self.sequence==0: self.session_id = sid
        return result

    def begin(self, instruction, sink, **metadata):
        with self.lock:
            if not self.alive(): raise RuntimeError('Resident process exited')
            if not self.ready(): raise RuntimeError('Resident process is not ready')
            if self.current is not None: raise RuntimeError('Resident already owns an active turn')
            number = self.sequence+1
            tid = f'turn{number:08d}'
            token = uuid.uuid4().hex
            route = TurnOutput(token, sink)
            self.router.bind(route)
            try:
                self.channel.publish(tid, 'input', {**metadata,'instruction':instruction,'output_token':token})
            except BaseException:
                # Publication may already have reached the child. Retire the
                # process, never resend an input with uncertain consumption.
                self.close(grace=0)
                raise
            self.current = (tid, route)
            self.sequence = number
            return tid

    def result(self):
        with self.lock:
            if not self.current: return None
            tid, route = self.current
            result = self.channel.receive(tid,'completed')
            if not result or not route.drained.is_set(): return None
            self.session_id = result['session_id']
            return result

    def decision(self):
        with self.lock:
            if not self.current: return None
            tid, route = self.current
            result = self.channel.receive(tid, 'decision')
            if not result or not route.drained.is_set(): return None
            self.session_id = result['session_id']
            return result

    def resolve_decision(self, content, timeout=120):
        with self.lock:
            if self.decision() is None:
                raise RuntimeError('No pending CEO decision to resolve')
            tid, _ = self.current
            self.channel.publish(tid, 'tool-result', {'content': content})
        deadline = time.monotonic()+timeout
        while self.result() is None:
            if not self.alive() or self.reader_error:
                raise RuntimeError(self.reader_error or 'CEO exited while persisting tool result')
            if time.monotonic() > deadline:
                raise RuntimeError('CEO tool result was submitted but settlement is unconfirmed; do not resend')
            time.sleep(.05)
        return self.finish()

    def finish(self):
        with self.lock:
            result = self.result()
            if result is None: raise RuntimeError('Turn result/output is incomplete')
            self.current = None
            return result

    def close(self, grace=2):
        if self.closed: return
        try:
            if self.proc:
                self.channel.publish('process','shutdown',{})
                until = time.monotonic()+grace
                while self.alive() and time.monotonic()<until: time.sleep(.05)
                if self.job: self.job.terminate()
                elif self.alive():
                    import signal
                    os.killpg(self.pid, signal.SIGKILL)
                if self.backend=='conpty': self.proc.close(force=True)
                else:
                    if self.proc.poll() is None: self.proc.kill()
                    self.proc.wait(timeout=5)
        finally:
            self.closed = True
            if self.job: self.job.close()
            if hasattr(self,'reader'): self.reader.join(3)
            self.console.close()
            for lease in self.session_leases.values(): lease.close()
            self.session_leases.clear()


class ResidentPool:
    def __init__(self):
        self.entries = {}
        self.lock = threading.RLock()

    def get(self, execution_id, identity, session_id):
        with self.lock:
            saved = self.entries.get(execution_id)
            if not saved: return None
            process, bound_identity = saved
            if not process.alive():
                process.close(grace=0); del self.entries[execution_id]; return None
            if identity!=bound_identity or session_id!=process.session_id:
                raise RuntimeError('Live resident belongs to a different execution context')
            return process

    def add(self, execution_id, identity, process):
        with self.lock:
            if execution_id in self.entries: raise RuntimeError('Execution already has a resident')
            self.entries[execution_id] = (process, identity)

    def discard(self, execution_id, process):
        with self.lock:
            if self.entries.get(execution_id, (None,))[0] is process:
                del self.entries[execution_id]
        process.close(grace=0)

    def describe(self, execution_id):
        with self.lock:
            saved = self.entries.get(execution_id)
        if not saved or not saved[0].alive():
            return {'hermes_process_alive': False, 'hermes_process_state': 'exited'}
        process = saved[0]
        with process.lock:
            state = 'waiting_input' if process.current is None else 'waiting_dispatch' if process.decision() else 'working'
            return {'hermes_process_alive': True, 'hermes_process_state': state,
                    'hermes_process_pid': process.pid}

    def close(self):
        with self.lock:
            for process,_ in self.entries.values(): process.close()
            self.entries.clear()


resident_pool = ResidentPool()
atexit.register(resident_pool.close)
