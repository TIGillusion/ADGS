from types import SimpleNamespace
import threading


def fixture(tmp_path,monkeypatch):
    import utils.context_probe
    monkeypatch.setattr(utils.context_probe,'probe_context_window',lambda *a,**kw:None)
    import core.node as module
    from core.execution_store import ExecutionStore
    store=ExecutionStore(tmp_path/'executions.sqlite');monkeypatch.setattr(module,'execution_store',store)
    worker=module.NODE.__new__(module.NODE);worker.node_id=worker.profile='manager';worker.reg=SimpleNamespace(get=lambda _:SimpleNamespace(score=50))
    worker._parent_cancel=None
    return module,store,worker


def test_starting_saved_execution_clears_only_previous_terminal_metadata(tmp_path,monkeypatch):
    module,store,worker=fixture(tmp_path,monkeypatch)
    store.put('same',state='interrupted',error='Cancelled by operator',ended_at=123,
              coordinator=True,result={'outcome':'interrupted','error':'old cause'},session_id='same-context')
    worker._prepare_execution('same contract','t','same',workspace=str(tmp_path))
    try:
        current=store.get('same')
        assert current['state']=='running' and current.get('ended_at') is None and not current.get('error')
        assert current['session_id']=='same-context' and current['result']['error']=='old cause'
    finally:
        with module._ACTIVE_EXECUTIONS_LOCK:module._ACTIVE_EXECUTIONS.pop('same',None)


def test_resuming_coordinator_preserves_role_phase_and_status(tmp_path,monkeypatch):
    module,store,worker=fixture(tmp_path,monkeypatch)
    store.put('same',coordinator=True,state='running',phase='coordinating')
    worker._execution_id='same';worker._work_task_id='t';worker._run_id='r';worker._session_name='chat-test'
    worker._workspace=str(tmp_path);worker._deadline=float('inf');worker._cancel=threading.Event()
    worker._compute_timeouts=lambda *a:(60,60);worker._worker_model=lambda:'fake';worker._ensure_live_log=lambda:tmp_path/'live.log'
    monkeypatch.setattr(module,'_EXEC_SEM',module.DynamicSemaphore(1));monkeypatch.setattr(module,'worker_launch_on_hold',lambda:False)
    events=[];monkeypatch.setattr(module.bus,'emit',lambda kind,**data:events.append((kind,data)))
    def resume(*a,**kw):
        assert store.get('same')['phase']=='coordinating'
        assert [d['status'] for kind,d in events if kind=='node.status'][-1]=='delegating'
        return {'exit_code':0,'output':'done','session_id':'same-context'}
    worker.hermes=SimpleNamespace(resume=resume)
    worker._execute_attempt('review child reports',session_id='same-context')
