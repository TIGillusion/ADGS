"""Authenticated, versioned owner API for external AI controllers."""
import copy
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file
from werkzeug.exceptions import HTTPException

_token_lock = threading.Lock()
_ID = re.compile(r'^[A-Za-z0-9_-]{1,80}$')
_KEY = re.compile(r'^[A-Za-z0-9_.:-]{1,128}$')


def state_directory():
    return Path(os.environ.get('ADGS_STATE_DIR', str(Path(__file__).resolve().parents[1]/'.adgs_runtime')))


def control_token():
    configured = os.environ.get('ADGS_CONTROL_TOKEN')
    if configured:
        if len(configured) < 32:
            raise ValueError('ADGS_CONTROL_TOKEN must contain at least 32 characters')
        return configured
    with _token_lock:
        path = state_directory()/'control_token'
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open('x', encoding='utf8') as stream:
                stream.write(secrets.token_urlsafe(48))
            path.chmod(0o600)
        except FileExistsError:
            pass
        token = path.read_text(encoding='utf8').strip()
        if len(token)<32:
            raise ValueError('Control token is invalid')
        return token


def authenticate_control():
    if request.path == '/api/v1' or request.path.startswith('/api/v1/'):
        supplied = request.headers.get('Authorization','')
        try:
            expected = 'Bearer '+control_token()
        except (OSError,ValueError):
            return jsonify(ok=False,error={'code':'control_unavailable','message':'控制令牌未正确配置'}),503
        if not secrets.compare_digest(supplied.encode(),expected.encode()):
            return jsonify(ok=False,error={'code':'unauthorized','message':'需要有效的 Bearer 控制令牌'}),401,{'WWW-Authenticate':'Bearer'}
        if request.content_length and request.content_length>2*1024*1024:
            return jsonify(ok=False,error={'code':'payload_too_large','message':'控制请求体上限为 2 MiB'}),413


class ApiProblem(Exception):
    def __init__(self, code, message, status=400):
        self.code,self.message,self.status=code,message,status


@contextmanager
def receipts():
    path=state_directory()/'control.sqlite'
    path.parent.mkdir(parents=True,exist_ok=True)
    connection=sqlite3.connect(path,timeout=15)
    connection.execute('CREATE TABLE IF NOT EXISTS requests (key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, response TEXT, status INTEGER, created REAL NOT NULL)')
    try:
        with connection:yield connection
    finally:connection.close()


def accept_once(action):
    key=request.headers.get('Idempotency-Key','')
    if not _KEY.fullmatch(key):
        raise ApiProblem('idempotency_key_required','请提供 1–128 字符的 Idempotency-Key（字母、数字、_.:-）')
    fingerprint=hashlib.sha256((request.path+'\n'+json.dumps(request.get_json() or {},sort_keys=True,ensure_ascii=False)).encode()).hexdigest()
    with receipts() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT fingerprint,response,status FROM requests WHERE key=?',(key,)).fetchone()
        if row:
            if row[0]!=fingerprint:raise ApiProblem('idempotency_conflict','此幂等键已用于不同请求',409)
            if row[1] is None:raise ApiProblem('acceptance_unknown','上次提交状态未知，请先查询会话和事件；不要换键重复提交',409)
            return jsonify(json.loads(row[1])),row[2],{'Idempotent-Replay':'true'}
        db.execute('INSERT INTO requests VALUES (?,?,NULL,NULL,?)',(key,fingerprint,time.time()))
    # Persist the claim first: a crash after launch must never cause a replay to
    # launch a second task. Uncertain claims remain queryable through the 409.
    try:
        body,status=action()
    except ApiProblem as exc:
        body,status={'ok':False,'error':{'code':exc.code,'message':exc.message}},exc.status
    with receipts() as db:
        db.execute('UPDATE requests SET response=?,status=? WHERE key=?',(json.dumps(body,ensure_ascii=False),status,key))
    return jsonify(body),status


def create_control_api(host):
    api=Blueprint('control_v1',__name__,url_prefix='/api/v1')

    @api.errorhandler(ApiProblem)
    def problem(error):return jsonify(ok=False,error={'code':error.code,'message':error.message}),error.status

    @api.errorhandler(Exception)
    def unexpected(error):
        if isinstance(error,HTTPException):return jsonify(ok=False,error={'code':'http_error','message':error.description}),error.code
        host.app.logger.exception('Control API request failed')
        return jsonify(ok=False,error={'code':'internal_error','message':'请求未完成；提交请求请用原幂等键核对回执与会话状态'}),500

    @api.before_request
    def validate_body():
        if request.method in {'POST','PUT','PATCH','DELETE'}:
            data=request.get_json(silent=True)
            if data is None and not request.content_length:return
            if not isinstance(data,dict):raise ApiProblem('invalid_json','请求体必须是 JSON 对象')

    @api.after_request
    def private_response(response):
        response.headers['Cache-Control']='no-store'
        if not response.is_streamed and response.is_json:
            payload=response.get_json(silent=True)
            if isinstance(payload,dict) and payload.get('ok') is False and isinstance(payload.get('error'),str):
                response.set_data(json.dumps({**payload,'error':{'code':'request_rejected','message':payload['error']}},ensure_ascii=False))
                if response.status_code<400:response.status_code=400
        return response

    def data():return request.get_json(silent=True) or {}

    def integer(name,default,maximum):
        try:value=int(request.args.get(name,default))
        except (TypeError,ValueError):raise ApiProblem('invalid_parameter',f'{name} 必须为整数')
        if value<0 or value>maximum:raise ApiProblem('invalid_parameter',f'{name} 范围为 0–{maximum}')
        return value

    def session_path(session):
        if not _ID.fullmatch(session):raise ApiProblem('invalid_session','非法会话 ID')
        path=host.config.DATA_DIR/'sessions'/(session+'.json')
        if not path.is_file():raise ApiProblem('session_not_found','会话不存在',404)
        return path

    def snapshot(session):
        path=session_path(session)
        try:saved=json.loads(path.read_text(encoding='utf8'))
        except FileNotFoundError:raise ApiProblem('session_not_found','会话不存在',404)
        except (OSError,ValueError):raise ApiProblem('session_unavailable','会话暂不可读',503)
        if not isinstance(saved,dict):raise ApiProblem('session_unavailable','会话格式错误',503)
        ceo=host._session_ceos.get(session)
        if host._session_busy(session) and ceo:
            with ceo._lock():
                saved={**saved,'history':copy.deepcopy(ceo._histories.get(session,saved.get('history',[]))),
                       'session_state':copy.deepcopy(ceo._states.get(session,{}))}
        return saved

    def summaries(session,limit=100):
        fields=['execution_id','node_id','profile','task_id','run_id','session_name','session_id','state','phase','parent_execution_id','attempt_id','updated_at','deadline_at']
        with host.execution_store.lock,host.execution_store._connect() as db:
            selection=','.join("json_extract(payload,'$."+field+"')" for field in fields)
            rows=db.execute('SELECT '+selection+" FROM executions WHERE json_extract(payload,'$.session_name')=? ORDER BY json_extract(payload,'$.updated_at') DESC LIMIT ?",(session,limit)).fetchall()
        return [dict(zip(fields,row)) for row in rows]

    def describe_session(session,saved):
        state=saved.get('session_state') or {}
        busy=host._session_busy(session)
        stop=host._run_stop_events.get(session)
        stopping=bool(stop and stop.is_set())
        phase='pausing' if stopping else 'running' if busy else 'awaiting_input' if state.get('_awaiting_input') else 'completed' if state.get('_task_done') else 'paused' if saved.get('history') else 'idle'
        return dict(id=session,busy=busy,phase=phase,run_id=state.get('_run_id'),
                    task_done=bool(state.get('_task_done')),saved_at=saved.get('saved_at'),
                    message_count=len(saved.get('history') or []),queue=host._queue_snapshot(session))

    @api.get('')
    @api.get('/')
    def capabilities():
        return jsonify(ok=True,version='1',capabilities=['projects','sessions','messages','queue','batch_guidance','pause','resume','executions','events','files','nodes','settings','models'],
                       endpoints=[{'path':str(rule),'methods':sorted(rule.methods-{'HEAD','OPTIONS'})} for rule in host.app.url_map.iter_rules() if str(rule).startswith('/api/v1')])

    @api.get('/health')
    def health():
        from core.execution_store import worker_launch_on_hold
        return jsonify(ok=True, version='1', busy=host._busy['state'], runs=host._running_sessions.copy(),
                       event_cursor=host.bus.last_id, mesh=host._mesh_status, worker_hold=worker_launch_on_hold())

    @api.get('/requests/<key>')
    def receipt(key):
        if not _KEY.fullmatch(key):raise ApiProblem('invalid_key','非法请求键')
        with receipts() as db:row=db.execute('SELECT response,status,created FROM requests WHERE key=?',(key,)).fetchone()
        if row is None:raise ApiProblem('request_not_found','请求键不存在',404)
        response=json.loads(row[0]) if row[0] is not None else None
        if response is None:
            state='unknown'
        elif 200 <= row[1] < 300 and response.get('ok') is True:
            state='accepted'
        else:
            state='rejected'
        return jsonify(ok=True,key=key,state=state,response=response,http_status=row[1],created_at=row[2])

    @api.get('/sessions')
    def sessions():
        host._catalog_refresh()
        return jsonify(ok=True,sessions=[{**chat,'workspace':project['workspace'],'project_name':project['name'],'busy':host._session_busy(chat['id'])} for project in host.project_catalog.listing() for chat in project['sessions']])

    @api.get('/sessions/<session>')
    def session_detail(session):return jsonify(ok=True,session=describe_session(session,snapshot(session)))

    @api.get('/sessions/<session>/messages')
    def messages(session):
        saved=snapshot(session);history=saved.get('history') or []
        items=history if request.args.get('format')=='raw' else host._session_messages(history)
        after=integer('after',0,10000000);limit=max(1,integer('limit',100,200))
        return jsonify(ok=True,messages=items[after:after+limit],next_offset=min(after+limit,len(items)),has_more=after+limit<len(items),total=len(items))

    @api.get('/sessions/<session>/graph')
    def graph(session):return jsonify(ok=True,graph=snapshot(session).get('graph') or [],event_cursor=host.bus.last_id)

    @api.get('/sessions/<session>/executions')
    def executions(session):
        session_path(session)
        return jsonify(ok=True,executions=summaries(session,max(1,integer('limit',100,500))))

    @api.post('/sessions/<session>/messages')
    def submit_message(session):
        snapshot(session)
        text=data().get('text');mode=data().get('mode','queue')
        if not isinstance(text,str) or not text.strip() or text.lstrip().startswith('/'):
            raise ApiProblem('invalid_text','text 必须是非空任务消息；此接口不接受斜杠命令')
        if mode not in ('queue','interrupt'):raise ApiProblem('invalid_mode','mode 必须是 queue 或 interrupt')
        def submit():
            result=host._submit_chat_message(text.strip(),session,mode)
            return {**result,'session':session},202 if result['ok'] else result.get('http_status',503)
        return accept_once(submit)

    @api.post('/sessions/<session>/resume')
    def resume(session):
        snapshot(session)
        instruction=data().get('instruction','继续原任务，保留已完成成果，先核对并恢复中断执行，再补齐汇报与验收。')
        if not isinstance(instruction,str) or not instruction.strip():raise ApiProblem('invalid_instruction','instruction 必须是非空字符串')
        def submit():
            execution=data().get('execution_id')
            if execution is None:
                execution=next((row['execution_id'] for row in summaries(session,500) if row['session_id'] and row['state'] not in {'passed','completed','done'}),None)
            saved=host.execution_store.get(execution) if isinstance(execution,str) else None
            if not saved or saved.get('session_name')!=session:raise ApiProblem('execution_not_found','需要属于本会话的可恢复 execution_id',404)
            if saved.get('state') in {'passed','completed','done'}:raise ApiProblem('already_completed','执行已完成，不应重复恢复',409)
            previous_session=saved.get('session_id') or (saved.get('result') or {}).get('session_id')
            if not previous_session:
                from core.execution_store import preworker_attempts_safe, preworker_resume_evidence
                from core.terminal_runtime import terminal_store
                if (not preworker_resume_evidence(saved)
                        or not preworker_attempts_safe(terminal_store.list_attempts(execution_id=execution))):
                    raise ApiProblem('session_missing','原执行没有可确认的 Hermes 会话或启动前检查点',409)
            context = ('沿用此执行的原节点、profile 和 Hermes 会话，' if previous_session else
                       '此执行在 Hermes 启动前受阻，保留原执行 ID、节点、profile 和任务书完成首次启动。')
            with host._task_lock:
                if host._session_busy(session):raise ApiProblem('session_busy','会话正在运行，请查询状态或发送引导',409)
                text=(f'从中断处继续当前会话的原任务。先核对并调用 continue_task，execution_id={execution}。'
                      + context + '保留原权限、证据与已完成成果，不用新派同一任务代替恢复。'
                      '先补齐该执行的报告与验收，再处理原目标剩余义务，并向用户说明恢复进度。\n'+instruction)
                from core.execution_store import recovery_grant
                grant=recovery_grant(execution)
                if grant:
                    text+=('\n事故保护仍开启。本轮只能恢复这个 execution_id。'
                           '收到该执行结果后向用户汇报，等待下一次批准。')
                    result=host._start_task(text,session,workspace=saved.get('workspace'),recovery_execution=execution)
                else:
                    result=host._start_task(text,session)
            return {**result,'session':session,'execution_id':execution,'previous_hermes_session_id':previous_session},202 if result['ok'] else result.get('http_status',503)
        return accept_once(submit)

    @api.post('/sessions/<session>/pause')
    def pause(session):
        session_path(session)
        from core.node import _ACTIVE_EXECUTIONS,_ACTIVE_EXECUTIONS_LOCK
        with host._task_lock:
            stop=host._run_stop_events.get(session)
            if stop is None:return jsonify(ok=True,session=session,phase='already_idle')
            stop.set()
            with _ACTIVE_EXECUTIONS_LOCK:active=list(_ACTIVE_EXECUTIONS.items())
        cancelled=[]
        for eid,event in active:
            if (host.execution_store.get(eid) or {}).get('session_name')==session:
                # Signal only the captured attempt; a later resume may reuse eid.
                with _ACTIVE_EXECUTIONS_LOCK:
                    if _ACTIVE_EXECUTIONS.get(eid) is event:
                        event.set();cancelled.append(eid)
        return jsonify(ok=True,session=session,phase='pausing',cancel_requested=cancelled),202

    @api.get('/events')
    def events():
        session=request.args.get('session')
        if session:session_path(session)
        after=integer('after',0,2**63-1);limit=max(1,integer('limit',100,500))
        observed_cursor=host.bus.last_id
        records=host.bus.control_events(after,session,limit+1)
        visible=records[:limit]
        cursor=visible[-1]['id'] if visible else max(after,observed_cursor)
        return jsonify(ok=True,events=visible,next_cursor=cursor,latest_cursor=host.bus.last_id,has_more=len(records)>limit)

    @api.get('/sessions/<session>/files')
    def files(session):
        saved=snapshot(session);meta=host.project_catalog.session(session)
        workspace=Path((meta or {}).get('workspace') or host.session_workspace(saved) or host.config.workspace).resolve()
        requested=Path(request.args.get('path','.'));target=(workspace/requested).resolve()
        if not target.is_relative_to(workspace):raise ApiProblem('outside_workspace','文件必须位于本会话工作目录内',403)
        if target.is_file():return send_file(target,as_attachment=True,download_name=target.name)
        if not target.is_dir():raise ApiProblem('file_not_found','路径不存在',404)
        after=integer('after',0,10000000);limit=max(1,integer('limit',100,200))
        entries=sorted(target.iterdir(),key=lambda p:p.name.lower())
        return jsonify(ok=True,path=str(target),entries=[{'name':p.name,'directory':p.is_dir(),'size':p.stat().st_size if p.is_file() else None} for p in entries[after:after+limit]],has_more=after+limit<len(entries),next_offset=min(after+limit,len(entries)))

    # Reuse the actual product handlers, rather than a second implementation.
    aliases=[('GET','/projects','api_projects',{}),('POST','/projects','api_project_create',{}),
             ('PATCH','/projects/<project_id>','api_project_update',{}),('POST','/projects/<project_id>/sessions','api_project_session_create',{}),
             ('PATCH','/sessions/<session_id>','api_project_session_update',{}),
             ('GET','/queue','api_queue_get',{}),('PUT','/queue/<item_id>','api_queue_update',{}),('DELETE','/queue/<item_id>','api_queue_remove',{}),
             ('POST','/queue/<item_id>/guide','api_queue_guide',{}),('GET','/settings','api_settings_get',{}),('POST','/settings','api_settings_set',{}),
             ('GET','/nodes','api_nodes',{}),('GET','/nodes/<node_id>','api_node',{}),('GET','/nodes/<node_id>/logs','api_node_logs',{}),
             ('POST','/nodes/<node_id>/describe','api_node_describe',{}),('POST','/nodes/<node_id>/skills/install','api_node_skill_install',{}),
             ('POST','/nodes/<node_id>/skills/remove','api_node_skill_remove',{}),('POST','/nodes/<node_id>/revive','api_node_revive',{}),
             ('GET','/delegations','api_mesh_delegations',{}),('POST','/evolve','api_evolve',{}),
             ('GET','/executions/<execution_id>','executions.execution_detail',{}),('POST','/executions/<execution_id>/cancel','executions.execution_cancel',{}),
             ('GET','/attempts/<attempt_id>/terminal','executions.terminal_events',{}),('GET','/attempts/<attempt_id>/download','executions.terminal_download',{})]
    for index,(method,path,view,_) in enumerate(aliases):
        def handler(_view=view,**kwargs):
            if _view=='api_settings_set':
                for field in ('runtime','advanced'):
                    if field in data() and not isinstance(data()[field],dict):raise ApiProblem('invalid_parameter',field+' 必须是对象')
            fields={'api_project_create':('name','workspace'),
                    'api_queue_update':('text',),'api_node_describe':('describe',),
                    'api_node_skill_install':('from_node','name'),'api_node_skill_remove':('name',)}.get(_view,())
            for field in fields:
                if not isinstance(data().get(field),str):raise ApiProblem('invalid_parameter',field+' 必须是字符串')
            return host.app.view_functions[_view](**kwargs)
        api.add_url_rule(path,'alias_'+str(index),handler,methods=[method])

    def redact(value):
        if isinstance(value,dict):return {key:('***' if item else '') if key.lower() in {'key','api_key','token'} else redact(item) for key,item in value.items()}
        if isinstance(value,list):return [redact(item) for item in value]
        return value

    @api.get('/models')
    def models():return jsonify(redact(host.api_models_get().get_json()))

    @api.post('/models')
    def update_models():
        response=host.app.make_response(host.api_models_set())
        return jsonify(redact(response.get_json())),response.status_code

    @api.get('/guide')
    def guide():return send_file(host.ROOT/'docs/外部AI控制接口.md',mimetype='text/markdown')

    return api
