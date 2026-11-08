import json
import os
import threading
import time
from types import SimpleNamespace

import pytest
import utils.context_probe
utils.context_probe.probe_context_window=lambda *a,**kw:None


@pytest.fixture
def control(tmp_path,monkeypatch):
    import web_app as web
    from web.event_bus import EventBus
    monkeypatch.setenv('ADGS_STATE_DIR',str(tmp_path/'state'))
    monkeypatch.setenv('ADGS_CONTROL_TOKEN','test-owner-token-'+'x'*40)
    monkeypatch.setattr(web.config,'DATA_DIR',tmp_path/'data')
    monkeypatch.setattr(web.config,'_workspace',str(tmp_path/'workspace'))
    (tmp_path/'workspace').mkdir()
    folder=tmp_path/'data/sessions';folder.mkdir(parents=True)
    (folder/'chat-a.json').write_text(json.dumps({'workspace':str(tmp_path/'workspace'),'history':[{'role':'user','content':'原任务'}],'session_state':{'_task_done':False,'_run_id':'r'},'graph':[]}),encoding='utf8')
    monkeypatch.setattr(web,'_session_ceos',{});monkeypatch.setattr(web,'_running_sessions',{});monkeypatch.setattr(web,'_run_stop_events',{})
    monkeypatch.setattr(web,'_queue',[]);monkeypatch.setitem(web._busy,'state',False)
    monkeypatch.setitem(web.app.config,'ADGS_PROXY_RUNTIME',False)
    bus=EventBus();bus.configure_store(tmp_path/'state/events.sqlite');monkeypatch.setattr(web,'bus',bus)
    monkeypatch.setattr(web,'registry',SimpleNamespace(count=lambda:1,active=lambda:[]))
    web.execution_store.put('old-exec',session_name='chat-a',node_id='ags_651',profile='ags_651',session_id='original-hermes',state='unjudged',run_id='r')
    headers={'Authorization':'Bearer '+'test-owner-token-'+'x'*40}
    return web,web.app.test_client(),headers,tmp_path


def test_auth_is_required_before_proxy_and_does_not_break_ui(control,monkeypatch):
    web,client,headers,_=control
    assert client.get('/api/v1/health').status_code==401
    assert client.get('/api/v1/health',headers=headers).status_code==200
    import core.runtime_host
    calls=[];monkeypatch.setattr(core.runtime_host,'proxy_request',lambda req:calls.append(req) or 'proxied')
    monkeypatch.setitem(web.app.config,'ADGS_PROXY_RUNTIME',True)
    assert client.get('/api/v1/health').status_code==401 and not calls
    assert client.get('/api/v1/health',headers=headers).data==b'proxied'


def test_compact_state_preserves_runtime_flags_and_current_completion(control,monkeypatch):
    web,client,headers,tmp=control
    import web.execution_api
    monkeypatch.setattr(web.execution_api,'runtime_fields',lambda *a:{'runtime_active':True,'waiting_for_slot':True,'waiting_for_children':False})
    result=client.get('/api/v1/sessions/chat-a/state',headers=headers)
    assert result.status_code==200
    assert result.json['executions'][0]['waiting_for_slot'] is True
    assert result.json['session']['phase']=='paused' and result.json['completion'] is None
    p=tmp/'data/sessions/chat-a.json';saved=json.loads(p.read_text())
    saved['history']+=[{'role':'assistant','tool_calls':[{'function':{'name':'finish','arguments':json.dumps({'status':'blocked','summary':'需要修复'})}}]}]
    saved['session_state']['_ceo_activity']={'phase':'waiting_children'}
    p.write_text(json.dumps(saved),encoding='utf-8')
    value=client.get('/api/v1/sessions/chat-a/state',headers=headers).json
    assert value['completion']['status']=='blocked' and value['ceo']['phase']=='interrupted'
    saved['history'].append({'role':'user','content':'新目标'});p.write_text(json.dumps(saved),encoding='utf-8')
    assert client.get('/api/v1/sessions/chat-a/state',headers=headers).json['completion'] is None
    assert client.get('/api/v1/sessions/missing/state',headers=headers).status_code==404
    assert client.get('/api/v1/sessions/chat-a/state').status_code==401


def test_usage_is_scoped_and_deduplicates_saved_ceo_and_attempts(control,monkeypatch):
    web,client,headers,tmp=control
    import core.hermes
    from core.terminal_runtime import terminal_store
    from tests.test_control_usage import database
    profiles=tmp/'profiles';monkeypatch.setattr(core.hermes,'PROFILES_DIR',profiles)
    database(profiles,'ags_651',[('original-hermes','local',100,10,900,0,5),('other-chat','hidden',9999,0,0,0,1)])
    database(profiles,'ceo',[('ceo-native','paid',100,10,100,0,2)])
    p=tmp/'data/sessions/chat-a.json';saved=json.loads(p.read_text());saved['session_state']['_hermes_session_id']='ceo-native';p.write_text(json.dumps(saved),encoding='utf-8')
    web.execution_store.put('ceo-test',session_name='chat-a',node_id='ceo',session_id='ceo-native')
    monkeypatch.setattr(terminal_store,'list_attempts',lambda execution_id:[{'session_id':'original-hermes'}] if execution_id=='old-exec' else [])
    r=client.get('/api/v1/sessions/chat-a/usage',headers=headers)
    assert r.status_code==200 and r.json['totals']['native_session_count']==2
    assert r.json['by_role']['worker']['cache_hit_rate_pct']==90
    assert r.json['totals']['total_input_tokens']==1200 and not r.json['partial']
    assert 'hidden' not in str(r.json)
    assert client.get('/api/v1/sessions/missing/usage',headers=headers).status_code==404
    assert client.get('/api/v1/sessions/chat-a/usage').status_code==401


def test_retried_resume_starts_ceo_once_with_original_execution(control,monkeypatch):
    web,client,headers,_=control;calls=[]
    monkeypatch.setattr(web,'_start_task',lambda text,session,**kw:calls.append((text,session)) or {'ok':True,'queued':False})
    request_headers={**headers,'Idempotency-Key':'resume-test'}
    first=client.post('/api/v1/sessions/chat-a/resume',json={'execution_id':'old-exec'},headers=request_headers)
    web.execution_store.put('old-exec',state='passed')
    second=client.post('/api/v1/sessions/chat-a/resume',json={'execution_id':'old-exec'},headers=request_headers)
    assert first.status_code==second.status_code==202 and len(calls)==1
    assert second.headers['Idempotent-Replay']=='true'
    assert 'continue_task' in calls[0][0] and 'old-exec' in calls[0][0] and calls[0][1]=='chat-a'
    assert first.json['previous_hermes_session_id']=='original-hermes'
    conflict=client.post('/api/v1/sessions/chat-a/resume',json={'execution_id':'old-exec','instruction':'不同请求'},headers=request_headers)
    assert conflict.status_code==409
    assert client.get('/api/v1/requests/resume-test',headers=headers).json['state']=='accepted'


def test_resume_rejects_cross_chat_completed_or_missing_sessions(control,monkeypatch):
    web,client,headers,_=control
    monkeypatch.setattr(web,'_start_task',lambda *a,**kw:pytest.fail('must not dispatch'))
    for index,(fields,status) in enumerate([({'session_name':'other'},404),({'state':'passed'},409),({'session_id':None},409)]):
        web.execution_store.put('old-exec',session_name='chat-a',state='unjudged',session_id='original',**{})
        web.execution_store.put('old-exec',**fields)
        response=client.post('/api/v1/sessions/chat-a/resume',json={'execution_id':'old-exec'},headers={**headers,'Idempotency-Key':'reject-'+str(index)})
        assert response.status_code==status,response.json


def test_legacy_profile_failure_recovers_same_id_only_without_terminal_attempt(control, monkeypatch):
    from core.execution_store import PROFILE_NOT_READY
    from core import terminal_runtime

    web, client, headers, _ = control
    web.execution_store.put('old-exec', session_id=None, state='interrupted', phase='execute',
                            decision_pending=False, attempt_id='allocated-before-preflight', error=PROFILE_NOT_READY)
    attempts = []
    monkeypatch.setattr(terminal_runtime, 'terminal_store', SimpleNamespace(list_attempts=lambda **kw: attempts))
    accepted = []
    def start(text, session):
        accepted.append((text, session))
        return {'ok': True, 'queued': False}
    monkeypatch.setattr(web, '_start_task', start)

    path = '/api/v1/sessions/chat-a/resume'
    body = {'execution_id': 'old-exec'}
    result = client.post(path, json=body, headers={**headers, 'Idempotency-Key': 'recover-preflight'})
    assert result.status_code == 202
    assert result.json['previous_hermes_session_id'] is None
    assert accepted[0][1] == 'chat-a' and 'execution_id=old-exec' in accepted[0][0]
    assert '首次启动' in accepted[0][0]

    attempts.append({'attempt_id': 'preflight-attempt', 'status': 'interrupted',
                     'termination_reason': 'worker_preflight'})
    retry = client.post(path, json=body, headers={**headers, 'Idempotency-Key': 'retry-preflight'})
    assert retry.status_code == 202 and len(accepted) == 2
    attempts.append({'attempt_id': 'durable-attempt'})
    rejected = client.post(path, json=body, headers={**headers, 'Idempotency-Key': 'reject-durable-attempt'})
    assert rejected.status_code == 409 and len(accepted) == 2


def test_incident_hold_blocks_work_before_any_launch(control):
    web, client, headers, folder = control
    (folder / 'state' / 'worker-hold').touch()
    response = client.post('/api/v1/sessions/chat-a/resume', json={'execution_id': 'old-exec'},
                           headers={**headers, 'Idempotency-Key': 'held-task'})
    assert response.status_code == 409
    assert '事故恢复' in response.json['error']['message']
    receipt = client.get('/api/v1/requests/held-task', headers=headers).json
    assert receipt['state'] == 'rejected' and receipt['http_status'] == 409
    assert not receipt['response']['ok']
    assert client.post('/api/chat', json={'text': '引导', 'session': 'chat-a', 'mode': 'interrupt'}).status_code == 409
    assert not web._running_sessions and not web._queue


@pytest.mark.skipif(os.name != 'nt', reason='Windows scoped recovery')
def test_incident_recovery_only_passes_approved_execution_to_runner(control, monkeypatch):
    web, client, headers, folder = control
    state = folder / 'state'
    (state / 'worker-hold').touch()
    workspace = str(folder / 'workspace')
    web.execution_store.put('old-exec', workspace=workspace)
    (state / 'worker-recovery.json').write_text(json.dumps({
        'execution_id': 'old-exec', 'profile': 'ags_651', 'workspace': workspace,
        'output_dir': str(folder / 'workspace' / '.adgs_artifacts' / 'scoped'),
    }), encoding='utf8')
    web.execution_store.put('unapproved', session_name='chat-a', session_id='another-session',
                            state='interrupted', node_id='other', profile='other')
    assert client.post('/api/v1/sessions/chat-a/resume', json={'execution_id': 'unapproved'},
                       headers={**headers, 'Idempotency-Key': 'unapproved'}).status_code == 409
    accepted = []
    monkeypatch.setattr(web, '_start_task',
                        lambda text, session, **options: accepted.append(options) or {'ok': True})
    assert client.post('/api/v1/sessions/chat-a/resume', json={'execution_id': 'old-exec'},
                       headers={**headers, 'Idempotency-Key': 'approved'}).status_code == 202
    assert accepted == [{'workspace': workspace, 'recovery_execution': 'old-exec'}]


@pytest.mark.parametrize(('external', 'provider_reply', 'expected'), [
    (False, '已保存此前的进展。', '已保存此前的进展'),
    (True, '__LLM_ERROR__: insufficient_quota private-token', 'CEO 模型额度不足'),
    (False, '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="continue_task">', '平台已拦截'),
    (False, '__LLM_ERROR__: unexpected tool call', '平台已拦截'),
])
def test_incident_chat_persists_reply_without_advancing_work(control, monkeypatch, external, provider_reply, expected):
    import core.ceo as ceo_module

    web, client, headers, folder = control
    (folder / 'state' / 'worker-hold').touch()
    saved_path = folder / 'data' / 'sessions' / 'chat-a.json'
    saved = json.loads(saved_path.read_text(encoding='utf8'))
    saved['session_state'].update(_task_done=True, _round_no=32, _awaiting_input=False)
    saved['history'].extend([
        {'role': 'assistant', 'tool_calls': [{'id': 'c1', 'type': 'function',
            'function': {'name': 'report', 'arguments': '{"summary":"上一轮已汇报"}'}}]},
        {'role': 'tool', 'tool_call_id': 'c1', 'content': '汇报已送达'},
    ])
    saved_path.write_text(json.dumps(saved, ensure_ascii=False), encoding='utf8')
    ceo = ceo_module.CEO()
    received = threading.Event()
    unblock = threading.Event()

    def respond(messages, **kwargs):
        assert '没有可调用的工具' in messages[0]['content']
        assert kwargs['reject_tool_calls']
        assert all(set(message) == {'role', 'content'} and message['role'] != 'tool' for message in messages)
        assert '上一轮已汇报' in messages[2]['content'] and '汇报已送达' in messages[3]['content']
        assert messages[-1] == {'role': 'user', 'content': '现在到哪了？'}
        received.set()
        assert unblock.wait(5)
        return provider_reply

    monkeypatch.setattr(ceo_module, 'bus', web.bus)
    monkeypatch.setattr(ceo, 'chat', SimpleNamespace(get_full_response=respond))
    monkeypatch.setattr(ceo, 'talk', lambda *_: pytest.fail('normal task loop was entered'))
    monkeypatch.setattr(web, 'get_ceo', lambda: ceo)
    monkeypatch.setattr(web, 'registry', SimpleNamespace(count=lambda: pytest.fail('worker prep was entered')))
    pending = {'id': 'pending', 'session': 'chat-a', 'mode': 'queue', 'text': '旧队列'}
    web._queue.append(pending)
    body = {'text': '现在到哪了？', 'session': 'chat-a'}
    if external:
        response = client.post('/api/v1/sessions/chat-a/messages', json=body,
                               headers={**headers, 'Idempotency-Key': 'recovery-chat'})
        assert response.status_code == 202
    else:
        response = client.post('/api/chat', json=body)
        assert response.status_code == 200
    assert response.json['recovery_chat'] is True and not response.json['queued']
    try:
        assert received.wait(5)
        assert client.post('/api/chat', json=body).status_code == 409
        assert client.post('/api/queue/pending/guide').status_code == 409
        assert json.loads(saved_path.read_text(encoding='utf8'))['history'][-1]['content'] == body['text']
    finally:
        unblock.set()
    deadline = time.monotonic() + 5
    while web._running_sessions and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not web._running_sessions and web._queue == [pending]
    restored = json.loads(saved_path.read_text(encoding='utf8'))
    assert restored['session_state'] == saved['session_state']
    assert restored['history'][:-2] == saved['history']
    reply = restored['history'][-1]
    assert reply['role'] == 'assistant'
    assert expected in reply['content']
    assert 'private-token' not in reply['content'] and 'DSML' not in reply['content']
    assert web.execution_store.get('old-exec')['state'] == 'unjudged'


def test_saved_recovery_tool_syntax_is_hidden_in_chat_view(control):
    web, client, _, folder = control
    raw = '**事故恢复对话 · 节点任务暂停**\n\n<｜DSML｜function_calls><｜DSML｜invoke name="continue_task">'
    path = folder / 'data' / 'sessions' / 'chat-a.json'
    saved = json.loads(path.read_text(encoding='utf8'))
    saved['history'].append({'role': 'assistant', 'content': raw})
    path.write_text(json.dumps(saved, ensure_ascii=False), encoding='utf8')
    web.bus.emit('chat.recovery_reply', session='chat-a', text=raw)
    snapshot = client.get('/api/state?session=chat-a').json
    assert '平台已拦截' in snapshot['events'][-1]['data']['text']
    assert '平台已拦截' in web._session_messages(saved['history'])[-1]['text']
    assert json.loads(path.read_text(encoding='utf8'))['history'][-1]['content'] == raw


def test_crash_after_claim_does_not_duplicate_submission(control,monkeypatch):
    web,client,headers,_=control;calls=[]
    def crash(*a,**kw):calls.append(1);raise RuntimeError('simulated unknown acceptance')
    monkeypatch.setattr(web,'_start_task',crash)
    monkeypatch.setitem(web.app.config,'TESTING',True)
    h={**headers,'Idempotency-Key':'crash-claim'}
    assert client.post('/api/v1/sessions/chat-a/messages',json={'text':'任务'},headers=h).status_code==500
    retried=client.post('/api/v1/sessions/chat-a/messages',json={'text':'任务'},headers=h)
    assert retried.status_code==409 and retried.json['error']['code']=='acceptance_unknown' and len(calls)==1
    assert client.get('/api/v1/requests/crash-claim',headers=headers).json['state']=='unknown'


def test_message_validation_and_queue_receipt(control,monkeypatch):
    web,client,headers,_=control
    assert client.post('/api/v1/sessions/chat-a/messages',json={'text':'test'},headers=headers).status_code==400
    assert client.post('/api/v1/sessions/chat-a/messages',json=[],headers=headers).status_code==400
    monkeypatch.setattr(web,'_start_task',lambda text,session,**kw:{'ok':True,'queued':True,'queue_id':'q','mode':kw['mode']})
    result=client.post('/api/v1/sessions/chat-a/messages',json={'text':'补充','mode':'interrupt'},headers={**headers,'Idempotency-Key':'guide'})
    assert result.status_code==202 and result.json['queue_id']=='q' and result.json['mode']=='interrupt'


def test_read_endpoints_are_scoped_and_file_reads_cannot_escape(control):
    web,client,headers,tmp=control
    outside=tmp/'secret.txt';outside.write_text('outside')
    inside=tmp/'workspace/report.md';inside.write_text('# 结果',encoding='utf8')
    assert client.get('/api/v1/sessions/chat-a',headers=headers).json['session']['phase']=='paused'
    assert client.get('/api/v1/sessions/chat-a/messages?format=raw',headers=headers).json['messages'][0]['content']=='原任务'
    assert client.get('/api/v1/sessions/chat-a/files?path=../secret.txt',headers=headers).status_code==403
    assert client.get('/api/v1/sessions/chat-a/files?path=report.md',headers=headers).data=='# 结果'.encode()
    assert client.get('/api/v1/sessions/chat-a/files',headers=headers).json['entries'][0]['name']=='report.md'
    assert client.get('/api/v1/sessions/chat-a/executions',headers=headers).json['executions'][0]['execution_id']=='old-exec'


def test_events_have_stable_global_cursor_and_session_filter(control):
    web,client,headers,_=control
    web.bus.emit('chat.user',text='A',session='chat-a')
    web.bus.emit('chat.user',text='B',session='chat-b')
    web.bus.emit('terminal.output',data='raw',session='chat-a')
    end=web.bus.emit('chat.ceo_end',status='blocked',session='chat-a')
    first=client.get('/api/v1/events?session=chat-a&limit=1',headers=headers).json
    assert first['has_more'] and len(first['events'])==1
    second=client.get('/api/v1/events?session=chat-a&after='+str(first['next_cursor']),headers=headers).json
    assert [e['id'] for e in second['events']]==[end['id']]
    assert second['next_cursor']==end['id']


def test_empty_event_poll_does_not_skip_event_arriving_during_response(control,monkeypatch):
    web,client,headers,_=control
    initial=web.bus.last_id
    def late(*args):
        web.bus.emit('chat.ceo_report',session='chat-a',summary='刚刚到达')
        return []
    monkeypatch.setattr(web.bus,'control_events',late)
    response=client.get('/api/v1/events?after='+str(initial),headers=headers).json
    assert response['next_cursor']==initial and response['latest_cursor']>initial


def test_project_aliases_create_real_isolated_metadata_and_session(control):
    web,client,headers,tmp=control
    project=client.post('/api/v1/projects',json={'name':'API 项目','workspace':str(tmp/'workspace')},headers=headers)
    assert project.status_code==200,project.json
    project_id=project.json['project']['id']
    chat=client.post('/api/v1/projects/'+project_id+'/sessions',json={'title':'API 会话'},headers=headers)
    assert chat.status_code==200 and chat.json['session']['project_id']==project_id
    session=chat.json['session']['id']
    assert client.get('/api/v1/sessions/'+session,headers=headers).json['session']['phase']=='idle'


def test_pause_retains_queue_and_stop_signal_is_per_chat(control,monkeypatch):
    web,client,headers,_=control
    a,b=threading.Event(),threading.Event()
    web._run_stop_events.update({'chat-a':a,'chat-b':b})
    web._queue.append({'id':'q','session':'chat-a','text':'以后做','mode':'queue'})
    import core.node
    monkeypatch.setattr(core.node,'_ACTIVE_EXECUTIONS',{})
    response=client.post('/api/v1/sessions/chat-a/pause',json={},headers=headers)
    assert response.status_code==202 and a.is_set() and not b.is_set() and len(web._queue)==1


def test_models_read_does_not_return_secrets(control,monkeypatch):
    web,client,headers,_=control
    monkeypatch.setattr(web,'_models_payload',lambda:{'ceo':{'key':'secret','model':'demo'}})
    response=client.get('/api/v1/models',headers=headers)
    assert response.status_code==200 and b'secret' not in response.data


def test_real_runner_pause_does_not_drain_pending_queue(control,monkeypatch):
    web,client,headers,_=control
    import core.ceo
    started=threading.Event()
    class FakeCEO:
        def __init__(self):self._states={};self._histories={}
        def _lock(self):return threading.RLock()
        def activate_session(self,name):self.name=name;self._states[name]={'_task_done':False}
        def talk(self,text):
            started.set()
            deadline=time.monotonic()+5
            while not self._stop_requested() and time.monotonic()<deadline:time.sleep(.01)
            self._states[self.name]['_task_done']=False
    monkeypatch.setattr(core.ceo,'CEO',FakeCEO)
    monkeypatch.setattr(web,'_save_current_session',lambda *a,**kw:None)
    response=client.post('/api/v1/sessions/chat-a/messages',json={'text':'执行'},headers={**headers,'Idempotency-Key':'runner'})
    assert response.status_code==202 and started.wait(3)
    web._queue.append({'id':'later','session':'chat-a','text':'待处理','mode':'interrupt'})
    try:
        assert client.post('/api/v1/sessions/chat-a/pause',json={},headers=headers).status_code==202
        deadline=time.monotonic()+3
        while web._running_sessions and time.monotonic()<deadline:time.sleep(.01)
        assert not web._running_sessions and not web._busy['state']
        assert web._queue[0]['id']=='later'
    finally:
        for event in web._run_stop_events.values():event.set()
