"""Execution-local ownership, propagated explicitly to worker thread pools."""
from contextlib import contextmanager
from contextvars import ContextVar, copy_context

runtime_session = ContextVar('adgs_runtime_session', default=None)
runtime_workspace = ContextVar('adgs_runtime_workspace', default=None)

@contextmanager
def session_scope(session, workspace=None):
    session_token = runtime_session.set(session)
    workspace_token = runtime_workspace.set(str(workspace) if workspace else None)
    try:
        yield
    finally:
        runtime_workspace.reset(workspace_token)
        runtime_session.reset(session_token)


def submit_context(pool, function, *args, **kwargs):
    return pool.submit(copy_context().run, function, *args, **kwargs)
