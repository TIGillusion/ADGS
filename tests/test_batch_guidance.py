import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
import utils.context_probe
utils.context_probe.probe_context_window = lambda *a, **kw: None
import core.ceo as module
from web.event_bus import EventBus


def bare():
    c = module.CEO.__new__(module.CEO)
    c._active = c._talk_session = 'guidance-fixture'
    c._histories = {c._active: []}
    c._logs = {c._active: []}
    c._states = {c._active: {}}
    c._run_id = 'existing-run'
    c._task_done = False
    c._task_seq = 0
    c._round_no = 1
    return c


def queue_source(items):
    def deliver(receive):
        if not items: return False
        receive(items[:])
        items.clear()
        return True
    return deliver


def test_guidance_commits_full_text_and_keeps_context_identity(monkeypatch):
    c=bare(); c.history.append({'role':'tool','tool_call_id':'original','content':'已有汇报'})
    c._facts_for()['tasks']['e1']={'execution_id':'e1','outcome':'passed'}
    items=[{'id':'g1','text':'引导原文' * 2000},{'id':'g2','text':'第二条引导'}]
    c._guidance_source=queue_source(items)
    snapshots=[];c.save_conversation=lambda name: snapshots.append(copy.deepcopy(c.history))
    bus=EventBus();monkeypatch.setattr(module,'bus',bus)
    assert c._apply_pending_guidance()
    assert not items and c._run_id=='existing-run'
    assert c._facts_for()['tasks']['e1']['outcome']=='passed'
    assert [m['content'] for m in c.history][-2:]==['引导原文'*2000,'第二条引导']
    assert snapshots[0]==c.history
    assert [e['type'] for e in bus.history()]==['chat.user','chat.user']


def test_failed_persistence_leaves_guidance_queued_and_rolls_back_context():
    c=bare(); before=copy.deepcopy(c.history);facts=copy.deepcopy(c._facts_for())
    items=[{'id':'g1','text':'不得丢失'}];c._guidance_source=queue_source(items)
    def fail(name):raise OSError('checkpoint unavailable')
    c.save_conversation=fail
    with pytest.raises(OSError):c._apply_pending_guidance()
    assert len(items)==1 and c.history==before and c._facts_for()==facts


def task(name,deps=None):
    return dict(name=name,goal=name,requirements=[],acceptance_criteria=[],deliverables=[],permissions='只读',depends_on=deps or [])


def test_current_parallel_work_finishes_but_next_dependency_wave_waits(monkeypatch,tmp_path):
    c=bare();monkeypatch.setattr(module.config,'max_workers_effective',2)
    monkeypatch.setattr(module.config,'_workspace',str(tmp_path))
    pending=threading.Event(); release=threading.Event(); started=[threading.Event(),threading.Event()];calls=[]
    c._guidance_pending=pending.is_set
    def prepare(overall,t,idx,seq,stage,background,**kwargs):return {'idx':idx,'task_id':str(idx),'execution_id':str(idx),'run_id':'r','node':object()}
    def execute(item):
        i=item['idx'];calls.append(i)
        if i<2:
            started[i].set();assert release.wait(5)
        return {'success':True,'outcome':'passed','execution_id':str(i)}
    c._prepare_task=prepare;c._execute_prepared=execute
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(c._dispatch_step,'split_tasks',{'overall_goal':'原任务','subtasks':[task('A'),task('B'),task('C',[0,1])]})
        try:
            assert started[0].wait(5) and started[1].wait(5)
            pending.set()
            assert not future.done()
        finally:release.set()
        result=future.result(timeout=5)
    assert sorted(calls)==[0,1]
    assert result['results'][2]['outcome']=='deferred'
    assert result['results'][2]['task_definition']['goal']=='C'
    assert result['results'][0]['success'] and result['results'][1]['success']


def test_guidance_during_model_call_discards_unapplied_old_decision(monkeypatch):
    c=bare();items=[];c._guidance_source=queue_source(items);c.save_conversation=lambda name: None
    monkeypatch.setattr(module,'should_compress',lambda *a,**kw:False)
    monkeypatch.setattr(module,'bus',EventBus())
    prompts=[]
    def model(messages,*a,**kw):
        prompts.append(copy.deepcopy(messages))
        if len(prompts)==1:
            items.append({'id':'g1','text':'方向改为检查报告，不要重新执行'})
            return {'tool_calls':[{'id':'old','name':'single_task','arguments':{}}]}
        return {'tool_calls':[{'id':'new','name':'ask_user','arguments':{'question':'收到新引导，确认报告范围？'}}]}
    c.chat=SimpleNamespace(call_tools=model)
    c._dispatch_step=lambda *a: pytest.fail('stale decision executed')
    c._talk_body('原用户任务')
    assert len(prompts)==2
    assert any(m.get('content')=='方向改为检查报告，不要重新执行' for m in prompts[1])
    assert not any(m.get('tool_calls') and m['tool_calls'][0]['id']=='old' for m in c.history)


def test_web_queue_guidance_is_session_scoped_and_ordinary_queue_is_retained(monkeypatch):
    import web_app as web
    queue=[{'id':'q1','text':'普通排队','session':'s','mode':'queue'},
           {'id':'g1','text':'同会话引导','session':'s','mode':'interrupt'},
           {'id':'g2','text':'另一会话','session':'other','mode':'interrupt'}]
    monkeypatch.setattr(web,'_queue',queue);monkeypatch.setattr(web,'_emit_queue_notifications',lambda *a,**kw:None)
    received=[]
    assert web._deliver_guidance('s',lambda items:received.extend(items))
    assert [i['id'] for i in received]==['g1']
    assert [i['id'] for i in queue]==['q1','g2']


def test_cross_session_guidance_rejected_without_mutating_queue(monkeypatch):
    import web_app as web
    monkeypatch.setattr(web,'_queue',[]);monkeypatch.setitem(web._busy,'state',True)
    monkeypatch.setattr(web,'get_task_session',lambda:'running-chat')
    r=web.app.test_client().post('/api/chat',json={'text':'引导','session':'other-chat','mode':'interrupt'})
    assert r.status_code==409 and not web._queue


def test_guidance_queue_failure_does_not_acknowledge_items(monkeypatch):
    import web_app as web
    queued=[{'id':'g1','text':'保留','session':'s','mode':'interrupt'}]
    monkeypatch.setattr(web,'_queue',queued)
    def fail(items):raise OSError('cannot save')
    with pytest.raises(OSError):web._deliver_guidance('s',fail)
    assert len(queued)==1

def test_tool_report_precedes_guidance_in_next_model_context(monkeypatch):
    c=bare();items=[];c._guidance_source=queue_source(items);c.save_conversation=lambda name:None
    monkeypatch.setattr(module,'should_compress',lambda *a,**kw:False)
    monkeypatch.setattr(module,'bus',EventBus())
    prompts=[]
    def model(messages,*a,**kw):
        prompts.append(copy.deepcopy(messages))
        if len(prompts)==1:
            return {'tool_calls':[{'id':'dispatch','name':'split_tasks','arguments':{'reason':'并发验证','overall_goal':'原目标','subtasks':[task('A'),task('B')]}}]}
        return {'tool_calls':[{'id':'followup','name':'ask_user','arguments':{'question':'根据本批结果和新引导确认下一步'}}]}
    def dispatch(*args):
        items.append({'id':'g1','text':'本批完成后先检查质量'})
        return {'stages':1,'tasks':2,'results':[{'success':True,'outcome':'passed','summary':'A汇报'}, {'success':True,'outcome':'passed','summary':'B汇报'}]}
    c.chat=SimpleNamespace(call_tools=model);c._dispatch_step=dispatch
    c._talk_body('原任务')
    second=prompts[1]
    assert any(m.get('role')=='tool' for m in second), str(second)
    report=next(i for i,m in enumerate(second) if m.get('role')=='tool' and 'A汇报' in m.get('content',''))
    guidance=next(i for i,m in enumerate(second) if m.get('content')=='本批完成后先检查质量')
    assert guidance==report+1
    assert 'B汇报' in second[report]['content']


def test_runner_holds_ordinary_queue_for_unfinished_task(monkeypatch):
    import web_app as web
    c=SimpleNamespace(_states={'s':{'_task_done':False}}, activate_session=lambda name:None,
                      talk=lambda text:None, mark_interrupted=lambda *a:None)
    queued=[{'id':'q1','text':'下一任务','session':'s','mode':'queue'}]
    monkeypatch.setattr(web,'_queue',queued);monkeypatch.setitem(web._busy,'state',False)
    monkeypatch.setattr(web,'get_ceo',lambda:c);monkeypatch.setattr(web,'registry',SimpleNamespace(count=lambda:1))
    monkeypatch.setattr(web,'_save_current_session',lambda *a,**kw:None)
    monkeypatch.setattr(web.bus,'emit',lambda *a,**kw:None)
    monkeypatch.setattr(web,'set_task_session',lambda *a:None)
    monkeypatch.setattr(web.threading,'Thread',lambda target,**kw:SimpleNamespace(start=target))
    assert web._start_task('原任务','s')['ok']
    assert queued[0]['text']=='下一任务' and not web._busy['state']


def test_guidance_mode_can_be_edited_and_cancelled_before_delivery(monkeypatch):
    import web_app as web
    monkeypatch.setitem(web._busy,'state',True);monkeypatch.setattr(web,'_queue',[])
    monkeypatch.setattr(web,'get_task_session',lambda:'s');monkeypatch.setattr(web.bus,'emit',lambda *a,**kw:None)
    client=web.app.test_client()
    response=client.post('/api/chat',json={'text':'原引导','session':'s','mode':'interrupt'})
    assert response.json['queued'] and response.json['mode']=='interrupt'
    item=client.get('/api/queue').json['queue'][0];assert item['mode']=='interrupt'
    assert client.put('/api/queue/'+item['id'],json={'text':'修订引导'}).json['ok']
    assert web._queue[0]['text']=='修订引导'
    assert client.delete('/api/queue/'+item['id']).json['ok']
    assert not web._has_guidance('s')
