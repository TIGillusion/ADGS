import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
import utils.context_probe
utils.context_probe.probe_context_window=lambda *a,**kw:None
from core.project_catalog import ProjectCatalog
from core.session_context import session_scope, submit_context
from config.config import config
from web.event_bus import EventBus, get_task_session


def test_catalog_persists_project_chat_titles_and_archives_without_changing_files(tmp_path):
    workspace=tmp_path/'project';workspace.mkdir()
    catalog=ProjectCatalog(tmp_path/'catalog.sqlite')
    p=catalog.create_project('研究项目',str(workspace));a=catalog.create_session(p['id']);b=catalog.create_session(p['id'],'第二会话')
    assert a['id']!=b['id'] and a['workspace']==str(workspace.resolve())
    catalog.update_session(a['id'],title='新标题');catalog.update_project(p['id'],'改名项目')
    catalog.update_session(b['id'],archived=True)
    restored=ProjectCatalog(tmp_path/'catalog.sqlite').listing()
    assert restored[0]['name']=='改名项目'
    assert [c['title'] for c in restored[0]['sessions']]==['新标题']
    assert not list(workspace.iterdir())


def test_old_sessions_are_discovered_once_without_rewriting_history(tmp_path):
    folder=tmp_path/'sessions';folder.mkdir();f=folder/'old-chat.json'
    original='{"history":[{"role":"user","content":"原用户消息"}]}'
    f.write_text(original,encoding='utf-8')
    catalog=ProjectCatalog(tmp_path/'catalog.sqlite');catalog.discover(folder,str(tmp_path));catalog.discover(folder,str(tmp_path))
    chats=catalog.listing()[0]['sessions'];assert len(chats)==1 and chats[0]['title']=='原用户消息'
    assert f.read_text(encoding='utf-8')==original


def test_project_archive_migrates_old_catalog_and_preserves_chat_archive(tmp_path, monkeypatch):
    import sqlite3
    import web_app as web

    database = tmp_path / 'projects.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,workspace TEXT NOT NULL,created_at REAL NOT NULL)')
        db.execute('INSERT INTO projects VALUES (?,?,?,?)', ('older', '旧项目', str(tmp_path), 100))

    catalog = ProjectCatalog(database)
    monkeypatch.setattr(web, 'project_catalog', catalog)
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path / 'data')
    monkeypatch.setattr(web, '_running_sessions', {})
    monkeypatch.setitem(web._busy, 'state', False)
    monkeypatch.setitem(web.app.config, 'ADGS_PROXY_RUNTIME', False)
    client = web.app.test_client()

    newer = catalog.create_project('新项目', str(tmp_path))
    project_url = '/api/projects/' + newer['id']
    chat = client.post(project_url + '/sessions', json={}).json['session']
    archived_chat = client.post(project_url + '/sessions', json={'title': '独立归档'}).json['session']
    chat_url = '/api/sessions/' + archived_chat['id']
    path = config.DATA_DIR / 'sessions' / (chat['id'] + '.json')
    original = path.read_bytes()

    assert client.patch(chat_url, json={'archived': True}).status_code == 200
    assert client.patch('/api/projects/older', json={'name': '改名后仍是旧项目'}).status_code == 200
    assert [p['id'] for p in client.get('/api/projects').json['projects']] == [newer['id'], 'older']

    web._running_sessions[chat['id']] = {}
    listing = client.get('/api/projects').json['projects']
    assert listing[0]['sessions'][0]['busy'] is True
    assert client.patch(project_url, json={'archived': True}).status_code == 409
    assert client.patch('/api/sessions/' + chat['id'], json={'archived': True}).status_code == 409
    web._running_sessions.clear()

    assert client.patch(project_url, json={'archived': True}).status_code == 200
    assert [p['id'] for p in client.get('/api/projects').json['projects']] == ['older']
    archive = client.get('/api/projects?include_archived=1').json['projects'][0]
    assert archive['archived'] and len(archive['sessions']) == 2
    assert client.post(project_url + '/sessions', json={}).status_code == 400
    assert web._start_task('请勿执行', chat['id'])['http_status'] == 409

    restored = client.patch(project_url, json={'archived': False}).json['project']
    assert restored['created_at'] == newer['created_at']
    assert [c['id'] for c in client.get('/api/projects').json['projects'][0]['sessions']] == [chat['id']]
    assert client.patch(chat_url, json={'archived': False}).status_code == 200
    assert len(client.get('/api/projects').json['projects'][0]['sessions']) == 2
    assert path.read_bytes() == original


def test_legacy_workspaces_group_records_and_keep_unknown_separate(tmp_path):
    import json
    folder=tmp_path/'sessions';folder.mkdir()
    snapshots={
        'a':{'facts':{'tasks':{'t1':{'workspace':str(tmp_path/'A')},'t2':{'workspace':' '}}}},
        'b':{'workspace':str(tmp_path/'A')},
        'c':{'facts':{'tasks':{'t3':{'workspace':str(tmp_path/'B')}}}},
        'unknown':{},
    }
    for name,snapshot in snapshots.items():
        snapshot['history']=[{'role':'user','content':'[系统提示] 历史摘要'},{'role':'user','content':'用户任务'+name}]
        (folder/(name+'.json')).write_text(json.dumps(snapshot),encoding='utf-8')
    catalog=ProjectCatalog(tmp_path/'catalog.sqlite');catalog.discover(folder,str(tmp_path/'A'))
    groups=catalog.listing()
    assert len(groups)==3
    assert {s['id'] for p in groups if p['name']=='A' for s in p['sessions']}=={'a','b'}
    assert [s['id'] for p in groups if p['name']=='未记录工作区' for s in p['sessions']]==['unknown']
    assert catalog.session('a')['title']=='用户任务a'


def test_thread_pool_inherits_only_its_own_session_workspace_and_events(tmp_path):
    bus=EventBus();baseline=config.workspace
    def work():
        bus.emit('node.status',node='same-global-node',status='running')
        return config.workspace,get_task_session()
    with ThreadPoolExecutor(max_workers=2) as pool:
        with session_scope('a',str(tmp_path/'a')):a=submit_context(pool,work)
        with session_scope('b',str(tmp_path/'b')):b=submit_context(pool,work)
        assert a.result()==(str(tmp_path/'a'),'a');assert b.result()==(str(tmp_path/'b'),'b')
    assert config.workspace==baseline
    assert {e['session'] for e in bus.history()}=={'a','b'}


def test_two_project_sessions_run_concurrently_with_isolated_ceos_queues_and_workspaces(tmp_path,monkeypatch):
    import core.ceo
    import web_app as web
    monkeypatch.setenv('ADGS_STATE_DIR',str(tmp_path/'runtime'))
    monkeypatch.setattr(config,'DATA_DIR',tmp_path/'data')
    monkeypatch.setattr(web,'_session_ceos',{});monkeypatch.setattr(web,'_running_sessions',{})
    monkeypatch.setattr(web,'_queue',[]);monkeypatch.setitem(web._busy,'state',False)
    monkeypatch.setattr(web,'registry',SimpleNamespace(count=lambda:1,active=lambda:[]))
    monkeypatch.setattr(web,'_save_current_session',lambda *a,**kw:None)
    bus=EventBus();monkeypatch.setattr(web,'bus',bus)
    started={};releases={};observed=[];objects=[]
    class FakeCEO:
        def __init__(self):self._states={};self._histories={};self._talk_session=None;objects.append(self)
        def activate_session(self,name):self.name=name;self._states[name]={'_task_done':False}
        def load_conversation(self,name):return '保留实时上下文'
        def talk(self,text):
            self._talk_session=self.name;self._histories[self.name]=[{'role':'user','content':text}]
            bus.emit('chat.user',text=text)
            with ThreadPoolExecutor(max_workers=1) as pool:
                inherited=submit_context(pool,lambda:(config.workspace,get_task_session())).result()
            observed.append((self.name,text,config.workspace,inherited))
            started[self.name].set()
            try:assert releases[self.name].wait(8)
            finally:
                self._guidance_source(lambda items:observed.extend((self.name,i['text'],'guidance',None) for i in items))
                self._states[self.name]['_task_done']=True;self._talk_session=None
        def mark_interrupted(self,*a):pass
    monkeypatch.setattr(core.ceo,'CEO',FakeCEO)
    client=web.app.test_client()
    dirs=[tmp_path/'a',tmp_path/'b']
    sessions=[]
    for directory in dirs:
        directory.mkdir();p=client.post('/api/projects',json={'name':directory.name,'workspace':str(directory)}).json['project']
        chat=client.post('/api/projects/'+p['id']+'/sessions',json={}).json['session'];sessions.append(chat)
        started[chat['id']]=threading.Event();releases[chat['id']]=threading.Event()
    a,b=[s['id'] for s in sessions]
    baseline=config.workspace
    try:
        assert not client.post('/api/chat',json={'text':'任务A','session':a}).json['queued']
        assert not client.post('/api/chat',json={'text':'任务B','session':b}).json['queued']
        assert started[a].wait(5) and started[b].wait(5)
        assert len(objects)==2 and objects[0] is not objects[1]
        assert len(web._running_sessions)==2
        assert client.post('/api/chat',json={'text':'引导A','session':a}).json['queued']
        item=client.get('/api/state',query_string={'session':a}).json['queue'][0]
        assert client.post('/api/queue/'+item['id']+'/guide',json={}).json['ok']
        assert web._has_guidance(a) and not web._has_guidance(b)
        assert client.get('/api/state',query_string={'session':b}).json['queue_len']==0
        loaded=client.post('/api/session/load',json={'name':a}).json
        assert loaded['running'] and loaded['messages'][0]['text']=='任务A'
        assert config.workspace==baseline
        for name,text,workspace,inherited in observed:
            expected=str(dirs[0] if name==a else dirs[1]);assert workspace==expected and inherited==(expected,name)
        # Another chat may be idle while waiting for user input or recovery.
        # Completing A/B must never start that chat's ordinary pending message.
        web._queue.append({'id':'blocked-other','text':'等待原任务结束','session':'waiting-other',
                           'mode':'queue','workspace':str(dirs[0])})
    finally:
        for release in releases.values():release.set()
        deadline=time.monotonic()+5
        while web._running_sessions and time.monotonic()<deadline:time.sleep(.01)
    assert not web._running_sessions and not web._busy['state']
    assert [item['id'] for item in web._queue]==['blocked-other']
    assert any(row[0]==a and row[1]=='引导A' for row in observed)
    assert {e['session'] for e in bus.history() if e['type']=='chat.user'}=={a,b}

def test_mesh_verdict_event_uses_delegation_owner_not_request_context(monkeypatch):
    import web_app as web
    import core.node as node_module
    bus=EventBus();monkeypatch.setattr(web,'bus',bus)
    record={'delegation_id':'d','parent_execution_id':'parent','parent_node_id':'n1','child_node_id':'n2','status':'passed','session_name':'chat-b'}
    monkeypatch.setattr(web,'_mesh_delegations',{'d':record});monkeypatch.setattr(web,'_persist_delegations',lambda:None)
    fake=SimpleNamespace(reg=SimpleNamespace(get=lambda name:SimpleNamespace(score=50)),_update_node=lambda *a:None)
    monkeypatch.setattr(node_module,'NODE',lambda *a,**kw:fake)
    with session_scope('chat-a'):
        result=web._mesh_verdict_impl('d',True,'完整复核内容'*100,3)
    assert result['ok'] and bus.history()[-1]['session']=='chat-b'
    assert bus.history()[-1]['data']['detail']=='完整复核内容'*100
