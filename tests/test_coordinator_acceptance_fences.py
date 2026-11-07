"""A model verdict cannot complete unfinished descendants or missing files."""
from types import SimpleNamespace
import pytest
from core.execution_store import ExecutionStore
import core.node as module


def node_fixture(tmp_path,monkeypatch):
    store=ExecutionStore(tmp_path/'executions.sqlite');monkeypatch.setattr(module,'execution_store',store)
    monkeypatch.setattr(module.bus,'emit',lambda *a,**kw:None)
    n=module.NODE.__new__(module.NODE)
    n.node_id=n.profile='manager';n._execution_id='parent';n._work_task_id='task';n._workspace=str(tmp_path);n._run_id='run'
    n._score_base=50;n._deadline=float('inf');n.reg=SimpleNamespace(get=lambda _:SimpleNamespace(score=50))
    n.agent=SimpleNamespace(append_history=lambda *a:None,compress_history_if_needed=lambda:None,
        history_brief=lambda:'',evaluate=lambda *a,**k:{'passed':True,'score':60,'raw_score':10})
    n._execution_evidence=lambda *a:'worker says all complete'
    n._emit_terminal_status=lambda *a,**k:None
    scores=[];n._update_node=lambda *a:scores.append(a)
    return n,store,scores


@pytest.mark.parametrize('state',['running','queued'])
def test_native_exit_cannot_complete_a_parent_with_active_descendants(tmp_path,monkeypatch,state):
    n,store,scores=node_fixture(tmp_path,monkeypatch)
    store.put('child',parent_execution_id='parent',state=state)
    result=n._complete_execution('contract',{'exit_code':0,'output':'done'},'ceo',True)
    assert not result['success'] and result['unjudged'] and not result['infra_failure']
    assert 'child' in result['error'] and not scores
    assert store.get('child')['state']==state
    merged=n._independent_acceptance('contract','done','passed','done')
    assert merged['unjudged_result']['outcome']=='unjudged'


def test_resumed_generic_acceptance_cannot_override_missing_output(tmp_path,monkeypatch):
    n,store,scores=node_fixture(tmp_path,monkeypatch)
    result=n._complete_execution('## 交付物\n- missing/report.md\n',{'exit_code':0,'output':'all done'},'manager',True)
    assert not result['success'] and result['outcome']=='failed'
    assert any('missing/report.md' in str(x) for x in result['evaluation']['failed_items'])
    assert result['evaluation']['score']==50 and scores==[(False,50)]


def test_unjudged_result_preserves_own_confirmed_resume_session(tmp_path,monkeypatch):
    n,store,scores=node_fixture(tmp_path,monkeypatch)
    store.put('parent',node_id='manager',profile='manager',session_id='confirmed-context')
    result=n._save_result({'success':False,'outcome':'unjudged','unjudged':True})
    assert result['hermes_session_id']=='confirmed-context'
    assert store.get('parent')['session_id']=='confirmed-context'


def test_finished_child_still_requires_current_parent_review(tmp_path,monkeypatch):
    n,store,scores=node_fixture(tmp_path,monkeypatch)
    store.put('child',parent_execution_id='parent',state='passed')
    store.put('parent',pending_delegation_reviews=['continued-child'])
    result=n._complete_execution('updated contract',{'exit_code':0,'session_id':'same-context'},'ceo',True)
    assert result['unjudged'] and not result['infra_failure'] and not scores
    assert 'continued-child' in result['error'] and 'report_delegation_verdict' in result['error']
    store.put('parent',pending_delegation_reviews=[])
    assert n._pending_children_result('updated contract',{}) is None


def test_pending_review_emits_unknown_acceptance_without_infrastructure_failure(tmp_path,monkeypatch):
    n,store,scores=node_fixture(tmp_path,monkeypatch)
    store.put('parent',pending_delegation_reviews=['original-delegation'])
    n._emit_terminal_status=module.NODE._emit_terminal_status.__get__(n)
    events=[];monkeypatch.setattr(module.bus,'emit',lambda kind,**data:events.append((kind,data)))
    result=n._pending_children_result('contract',{'session_id':'same-context'})
    status=next(d for kind,d in events if kind=='node.status')
    acceptance=next(d for kind,d in events if kind=='node.eval')
    assert status['status']=='unjudged' and not status.get('infra')
    assert acceptance['unjudged'] and not acceptance['infra_failure'] and 'passed' not in acceptance
    assert 'original-delegation' in acceptance['detail']
    assert result['evaluation']=={k:v for k,v in acceptance.items() if k not in {'node','profile'}}
    assert result['session_id']=='same-context' and not scores
