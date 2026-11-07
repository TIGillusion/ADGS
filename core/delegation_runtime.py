"""Execution slots can be lent to descendants while a worker waits on MCP."""
import threading
from concurrent.futures import Future
from contextlib import contextmanager
from contextvars import copy_context
from itertools import count

_slots = {}
_slots_lock = threading.Lock()


class ExecutionSlot:
    def __init__(self, semaphore, deadline, cancel=None, activity=None):
        self.semaphore = semaphore
        self.deadline = deadline
        self.cancel = cancel
        self.lock = threading.Lock()
        self.held = True  # Caller has already acquired the semaphore.
        self.closed = False
        self.waiters = 0
        self.resuming = False
        self.activity = activity

    def emit_status(self, status, reason):
        if not self.activity:
            return
        try:
            from web.event_bus import bus
            bus.emit('node.status', **self.activity, status=status, waiting_reason=reason)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Could not publish execution wait status')

    def is_set(self):
        return self.closed or (self.cancel is not None and self.cancel.is_set())

    def close(self):
        with self.lock:
            self.closed = True
            if self.held:
                self.held = False
                self.semaphore.release()

    @contextmanager
    def lend(self):
        with self.lock:
            tracked = not self.closed
            if tracked:
                self.waiters += 1
                if self.held:
                    self.held = False
                    self.semaphore.release()
            notify = tracked and self.waiters == 1
        if notify:
            self.emit_status('delegating', '等待下属汇报')
        try:
            yield
        finally:
            resume = False
            with self.lock:
                if tracked:
                    self.waiters -= 1
                    resume = not self.waiters and not self.closed and not self.resuming and not self.held
                    if resume:
                        self.resuming = True
            if resume:
                self.emit_status('waiting', '重新取得执行名额')
                acquired = self.semaphore.acquire(cancel=self)
                with self.lock:
                    self.resuming = False
                    closed = self.closed
                    if acquired and not closed and not self.waiters:
                        self.held = True
                    elif acquired:
                        self.semaphore.release()
                    running = self.held and not self.closed and not self.waiters
                if running:
                    self.emit_status('running', '')
                if not acquired and not closed:
                    if self.cancel is not None:
                        self.cancel.set()
                    raise TimeoutError('等待下属后重新取得执行名额已取消')


@contextmanager
def owned_execution_slot(execution_id, semaphore, deadline, cancel=None, activity=None):
    slot = ExecutionSlot(semaphore, deadline, cancel,
                         {**activity, 'execution_id': execution_id} if activity else None)
    try:
        with _slots_lock:
            if execution_id in _slots:
                raise RuntimeError('同一执行实例已有活动执行名额')
            _slots[execution_id] = slot
        yield slot
    finally:
        with _slots_lock:
            if _slots.get(execution_id) is slot:
                _slots.pop(execution_id, None)
        slot.close()


@contextmanager
def lend_execution_slot(execution_id):
    with _slots_lock:
        slot = _slots.get(execution_id)
    if slot is None:
        yield
    else:
        with slot.lend():
            yield


def execution_is_waiting(execution_id):
    return execution_wait_state(execution_id) is not None


def execution_wait_state(execution_id):
    with _slots_lock:
        slot = _slots.get(execution_id)
    if slot is None:
        return None
    with slot.lock:
        if slot.closed:
            return None
        if slot.waiters > 0:
            return 'children'
        return 'slot' if slot.resuming or not slot.held else None


class DelegationThreads:
    """Waiting ancestors must not occupy a fixed pool ahead of their children.

    Admission is bounded by the mesh pending cap; actual Hermes executions are
    still bounded by the shared execution semaphore.
    """
    def __init__(self):
        self.sequence = count(1)

    def submit(self, function, *args, **kwargs):
        future = Future()
        context = copy_context()
        def run():
            if future.set_running_or_notify_cancel():
                try:
                    future.set_result(context.run(function, *args, **kwargs))
                except BaseException as error:
                    future.set_exception(error)
        threading.Thread(target=run, daemon=True, name=f'adgs-mesh-{next(self.sequence)}').start()
        return future
