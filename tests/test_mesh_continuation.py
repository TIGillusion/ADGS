"""Existing child conversations are resumed, not replaced with new instances."""
import json
from types import SimpleNamespace
import threading
from pathlib import Path
import pytest
from core.execution_store import ExecutionStore


@pytest.fixture
def mesh(monkeypatch, tmp_path):
    import web_app as web
    import core.node as nodes
    store=ExecutionStore(tmp_path/'executions.sqlite')
    store.put('parent',node_id='manager',workspace=str(tmp_path),session_name='chat',run_id='run',depth=1,interaction_round_id='parent-round')
    store.put('child',node_id='worker',profile='worker',parent_execution_id='parent',workspace=str(tmp_path),session_name='chat',run_id='run',depth=2,task_id='original-task',session_id='original-hermes',task_md='original contract',state='passed')
    record=dict(delegation_id='delegation',parent_execution_id='parent',parent_node_id='manager',child_node_id='worker',child_execution_id='child',task_md='original contract',status='passed',summary='old result',verdict={'passed':True},batch_id='old-batch',created_at=1,shared_resources=[])
    monkeypatch.setattr(web,'execution_store',store)
    monkeypatch.setattr(web,'_mesh_delegations',{'delegation':record})
    monkeypatch.setattr(web,'_mesh_delegations_file',lambda:tmp_path/'mesh.json')
    monkeypatch.setattr(nodes,'active_cancel',lambda _:None)
    submitted=[];events=[]
    monkeypatch.setattr(web,'_MESH_POOL',SimpleNamespace(submit=lambda *a,**k:submitted.append((a,k))))
    monkeypatch.setattr(web.bus,'emit',lambda kind,**data:events.append((kind,data)))
    return web,store,record,submitted,events


def test_continue_keeps_execution_session_and_previous_result(mesh):
    web,store,record,submitted,events=mesh
    result=web._mesh_continue_impl('parent','delegation','fix two concrete gaps')
    assert result['ok'] and result['child_execution_id']=='child' and result['session_id']=='original-hermes'
    assert len(web._mesh_delegations)==1 and record['resume_count']==1
    assert record['previous_results'][0]['summary']=='old result'
    assert record['previous_results'][0]['verdict']=={'passed':True}
    assert record['parent_round_id']=='parent-round' and record['batch_id']!='old-batch'
    assert store.get('parent')['pending_delegation_reviews']==['delegation']
    assert submitted[0][1]=={'resume_instruction':'fix two concrete gaps'}
    assert events[0][1]['context_mode']=='continue'
    assert store.get('child')['session_id']=='original-hermes'
    assert not web._mesh_continue_impl('parent','delegation','duplicate')['ok']
    assert len(submitted)==1


def test_parent_review_obligation_survives_restart_and_clears_only_on_verdict(mesh,monkeypatch):
    web,store,record,submitted,_=mesh
    assert web._mesh_continue_impl('parent','delegation','repair')['ok']
    record['status']='passed'
    web._persist_delegations()
    store.put('parent',pending_delegation_reviews=[])
    web._mesh_delegations.clear();web._load_delegations()
    assert store.get('parent')['pending_delegation_reviews']==['delegation']
    # Avoid population score mutations; only exercise the real obligation state.
    import core.node as nodes
    monkeypatch.setattr(nodes,'NODE',lambda _:SimpleNamespace(reg=SimpleNamespace(get=lambda _:SimpleNamespace(score=50)),_update_node=lambda *a:None))
    assert web._mesh_verdict_impl('delegation',True,'read the repaired report',0)['ok']
    assert store.get('parent')['pending_delegation_reviews']==[]
    assert web._mesh_continue_impl('parent','delegation','second repair')['ok']
    assert store.get('parent')['pending_delegation_reviews']==['delegation']


def test_continued_wait_reports_current_round_elapsed_not_lifetime(mesh,monkeypatch):
    web,_,record,_,_=mesh
    monkeypatch.setattr(web.time,'time',lambda:10000)
    assert web._mesh_continue_impl('parent','delegation','repair')['ok']
    monkeypatch.setattr(web.time,'time',lambda:10007)
    assert web._mesh_wait_impl('delegation',0)['elapsed']==7


def test_parallel_continuations_keep_both_review_obligations(mesh,monkeypatch):
    web,store,record,_,_=mesh
    import core.node as nodes
    store.put('second-child',**{k:v for k,v in store.get('child').items() if k!='execution_id'})
    web._mesh_delegations['second']={**record,'delegation_id':'second','child_execution_id':'second-child'}
    assert web._mesh_continue_impl('parent','delegation','first repair')['ok']
    assert web._mesh_continue_impl('parent','second','parallel repair')['ok']
    assert store.get('parent')['pending_delegation_reviews']==['delegation','second']
    record['status']='passed'
    monkeypatch.setattr(nodes,'NODE',lambda _:SimpleNamespace(reg=SimpleNamespace(get=lambda _:SimpleNamespace(score=50)),_update_node=lambda *a:None))
    assert web._mesh_verdict_impl('delegation',True,'review first only',0)['ok']
    assert store.get('parent')['pending_delegation_reviews']==['second']


def test_foreign_parent_or_missing_context_cannot_replace_session(mesh):
    web,store,record,submitted,_=mesh
    store.put('other',workspace=store.get('parent')['workspace'])
    assert not web._mesh_continue_impl('other','delegation','wrong owner')['ok']
    store.put('child',session_id=None,result={},state='passed')
    assert not web._mesh_continue_impl('parent','delegation','no context')['ok']
    assert record['status']=='passed' and not submitted


def test_resume_respects_resources_and_shared_parent_batch(mesh):
    web,store,record,submitted,_=mesh
    record['shared_resources']=['device:x']
    web._mesh_delegations['other']={'status':'running','parent_execution_id':'parent','parent_round_id':'parent-round','batch_id':'parallel-batch','shared_resources':['device:x']}
    assert not web._mesh_continue_impl('parent','delegation','resource conflict')['ok']
    web._mesh_delegations['other']['shared_resources']=[]
    assert web._mesh_continue_impl('parent','delegation','independent work')['ok']
    assert record['batch_id']=='parallel-batch'


def test_child_runner_uses_continue_and_parent_cancel(mesh,monkeypatch):
    web,store,record,submitted,events=mesh
    import core.node as nodes
    cancel=threading.Event();received=[]
    monkeypatch.setattr(nodes,'active_cancel',lambda _:cancel)
    class Node:
        def __init__(self,nid):assert nid=='worker'
        def work(self,*a,**kw):pytest.fail('new execution must not start')
        def continue_execution(self,*a,**kw):
            received.append((a,kw));return {'success':True,'outcome':'passed','summary':'verified','evaluation':{'passed':True}}
    monkeypatch.setattr(nodes,'NODE',Node)
    web._mesh_run_child('delegation','worker','original contract','parent',resume_instruction='fix')
    assert received[0][0]==('child','fix')
    assert received[0][1]=={'task_id':'original-task','run_id':'run','parent_cancel':cancel}
    assert record['status']=='passed'


def test_native_mcp_continue_receipt_and_error(monkeypatch):
    import tools.adgs_mcp_server as server
    calls=[]
    def resume(*args):
        calls.append(args);return {'ok':True,'delegation_id':'d','child_execution_id':'e'}
    monkeypatch.setattr(server,'_provider',SimpleNamespace(continue_delegation=resume))
    assert 'execution_id=e' in server.continue_delegation('p','d','new input')
    assert calls==[('p','d','new input')]
    monkeypatch.setattr(server,'_provider',SimpleNamespace(continue_delegation=lambda *a:{'ok':False,'error':'原委派仍在执行'}))
    with pytest.raises(RuntimeError,match='仍在执行'):server.continue_delegation('p','d','duplicate')


def test_continue_endpoint_checks_mesh_token_and_uses_original_record(mesh,monkeypatch):
    web,_,record,submitted,_=mesh
    client=web.app.test_client()
    monkeypatch.setattr(web,'_mesh_auth',lambda:False)
    url='/internal/mesh/delegations/delegation/continue'
    assert client.post(url,json={}).status_code==401
    monkeypatch.setattr(web,'_mesh_auth',lambda:True)
    assert client.post(url,json=['invalid']).status_code==400
    response=client.post(url,json={'parent_execution_id':'parent','instruction':'precise repair'})
    assert response.status_code==200 and response.json['child_execution_id']=='child'
    assert len(submitted)==1
    assert client.post(url,json={'parent_execution_id':'parent','instruction':'duplicate'}).status_code==409


def test_submission_failure_is_visible_and_original_can_be_retried(mesh,monkeypatch):
    web,store,record,submitted,events=mesh
    def fail(*a,**kw):raise RuntimeError('cannot start thread')
    monkeypatch.setattr(web,'_MESH_POOL',SimpleNamespace(submit=fail))
    assert not web._mesh_continue_impl('parent','delegation','repair')['ok']
    assert record['status']=='interrupted' and record['previous_results'][0]['summary']=='old result'
    assert store.get('child')['session_id']=='original-hermes'
    assert events[-1][1]['status']=='interrupted'


def test_concurrent_registry_persistence_keeps_latest_records(mesh,monkeypatch,tmp_path):
    web,_,_,_,_=mesh
    first_write=threading.Event();second_done=threading.Event()
    original=Path.write_text
    def slow_old_snapshot(path,*args,**kwargs):
        if threading.current_thread().name=='older-snapshot':
            first_write.set();second_done.wait(.2)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'write_text',slow_old_snapshot)
    first=threading.Thread(target=web._persist_delegations,name='older-snapshot');first.start()
    assert first_write.wait(2)
    def newer():
        with web._mesh_lock:web._mesh_delegations['new']={'status':'passed'}
        web._persist_delegations();second_done.set()
    second=threading.Thread(target=newer);second.start();first.join(3);second.join(3)
    assert not first.is_alive() and not second.is_alive()
    assert json.loads((tmp_path/'mesh.json').read_text(encoding='utf-8'))['new']['status']=='passed'


def test_restart_reconciles_stale_running_and_preserves_resume_identity(mesh):
    web,store,record,submitted,_=mesh
    record['status']='running'
    store.put('child',state='passed',result={'success':True,'outcome':'passed','summary':'durable result'})
    web._persist_delegations();web._mesh_delegations.clear();web._load_delegations()
    assert web._mesh_delegations['delegation']['status']=='passed'
    assert web._mesh_delegations['delegation']['child_execution_id']=='child'
    web._mesh_delegations['delegation']['status']='running'
    store.put('child',state='running',result={})
    web._persist_delegations();web._mesh_delegations.clear();web._load_delegations()
    assert web._mesh_delegations['delegation']['status']=='interrupted'
    assert web._mesh_continue_impl('parent','delegation','resume after restart')['ok']
