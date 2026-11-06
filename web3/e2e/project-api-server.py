"""Isolated browser acceptance server. No model calls or production runtime writes."""
import os
import sys
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
state=ROOT/'web3/test/.project-api'
os.environ['ADGS_STATE_DIR']=str(state/'runtime')
import utils.context_probe
utils.context_probe.probe_context_window=lambda *a,**kw:None
import web_app as web
import core.ceo as ceo_module
from config.config import config
from web.event_bus import EventBus
config.DATA_DIR=state/'data'
config._workspace=str(state/'workspace')
Path(config._workspace).mkdir(parents=True,exist_ok=True)
web.registry=SimpleNamespace(count=lambda:4,active=lambda:[])
web.bus=EventBus();web.bus.configure_store(state/'events.sqlite')
release={}
class DemoCEO:
    def __init__(self):self._histories={};self._states={};self._talk_session=None;self.active=None
    def activate_session(self,name):self.active=name;self._histories.setdefault(name,[]);self._states.setdefault(name,{})
    def load_conversation(self,name):self.activate_session(name);return '已载入会话'
    def save_conversation(self,name):
        ceo_module.atomic_write_session(config.DATA_DIR/'sessions'/(name+'.json'),{'name':name,'history':self._histories.get(name,[]),'session_state':self._states.get(name,{}),'graph':[]})
    def mark_interrupted(self,*args):pass
    def talk(self,text):
        name=self.active;self._talk_session=name;self._states[name]['_task_done']=False
        self._histories[name].append({'role':'user','content':text});web.bus.emit('chat.user',text=text)
        identity=uuid.uuid4().hex
        web.bus.emit('graph.task',id='task-'+identity,stage_id=identity,parent='ceo',task_md=text)
        web.bus.emit('graph.dispatch',task_id='task-'+identity,execution_id=identity,parent='ceo',node='ags_shared',task_md=text)
        release.setdefault(name,threading.Event()).wait(45)
        def receive(items):
            for item in items:
                self._histories[name].append({'role':'user','content':item['text']});web.bus.emit('chat.user',text=item['text'],queue_id=item['id'])
        self._guidance_source(receive)
        self._histories[name].append({'role':'assistant','content':'本批工作完成，已保留会话上下文。'})
        web.bus.emit('chat.ceo_finish',summary='本批工作完成，已保留会话上下文。')
        self._states[name]['_task_done']=True;self._talk_session=None
ceo_module.CEO=DemoCEO
@web.app.post('/e2e/release/<session>')
def finish(session):release.setdefault(session,threading.Event()).set();return {'ok':True}
@web.app.get('/e2e/root')
def root():return {'workspace':str(state/'workspace')}
if __name__=='__main__':web.app.run('127.0.0.1',8108,threaded=True,use_reloader=False)
