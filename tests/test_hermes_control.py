import threading
import time
from types import SimpleNamespace

from core.hermes_control import DecisionTurn


def test_native_decision_waits_for_complete_actual_result_and_only_one_dispatch(tmp_path):
    cli = SimpleNamespace(agent=SimpleNamespace(session_id='original'))
    turn = DecisionTurn(cli,tmp_path,'turn00000001',{})
    returned=[]
    worker=threading.Thread(target=lambda:returned.append(turn.submit('report',{'summary':'中文'})))
    worker.start()
    deadline=time.monotonic()+3
    while turn.channel.receive('turn00000001','decision') is None and time.monotonic()<deadline:
        time.sleep(.01)
    assert turn.channel.receive('turn00000001','decision')['arguments']=={'summary':'中文'}
    assert worker.is_alive() and returned==[]
    assert '未执行' in turn.submit('finish',{})
    content='实际结果\n'+'完整中文🙂\n'*1000
    turn.channel.publish('turn00000001','tool-result',{'content':content})
    worker.join(3)
    assert not worker.is_alive() and returned==[content] and turn.resolved


def test_native_decision_boundary_never_masks_failed_tool_persistence(monkeypatch):
    import sys
    from types import ModuleType
    import core.hermes_control as control
    agent=ModuleType('agent');loop=ModuleType('agent.conversation_loop')
    loop.run_tool_round=lambda agent,**kw:SimpleNamespace(action='continue',failed=kw['failed'],final_response=None)
    agent.conversation_loop=loop
    monkeypatch.setitem(sys.modules,'agent',agent)
    monkeypatch.setitem(sys.modules,'agent.conversation_loop',loop)
    monkeypatch.setattr(control,'active',SimpleNamespace(resolved=True))
    control.install_decision_boundary()
    assert loop.run_tool_round(None,failed=True).action=='continue'
    result=loop.run_tool_round(None,failed=False)
    assert result.action=='break' and result.final_response==''
    assert result._turn_exit_reason=='adgs_decision_settled'


def test_ceo_reuses_real_tool_result_without_reinjecting_external_history(tmp_path,monkeypatch):
    import json
    import core.hermes as hermes
    import core.hermes_process as processes
    import core.terminal_runtime as runtime
    from core.hermes_ceo import HermesCEOChat
    monkeypatch.setenv('ADGS_HERMES_RESIDENT','1')
    monkeypatch.setattr(hermes,'HermesInstance',lambda name:SimpleNamespace(profile_dir=tmp_path))
    monkeypatch.setattr(hermes,'_configure_profile',lambda *a,**kw:True)
    monkeypatch.setattr(runtime,'terminal_store',SimpleNamespace(paths=lambda *a:tmp_path))
    queries=[];outcomes=[]
    def run(profile,query,**kwargs):
        queries.append(query)
        action={'name':'report','arguments':{'summary':'ready'}}
        (tmp_path/'ceo-decision.json').write_text(json.dumps(action),encoding='utf-8')
        return {'exit_code':0,'session_id':'native-original','control_decision':action}
    monkeypatch.setattr(runtime,'run_hermes',run)
    def resolve(content):
        outcomes.append(content)
        return {'exit_code':0,'session_id':'native-original'}
    process=SimpleNamespace(alive=lambda:True,resolve_decision=resolve)
    pool=processes.ResidentPool();pool.entries['ceo-e']=(process,())
    monkeypatch.setattr(processes,'resident_pool',pool)
    owner=SimpleNamespace(_hermes_input_count=0,_hermes_input_hash='',_hermes_session_id=None,
        _hermes_execution_id='ceo-e',_run_id='run',_set_activity=lambda *a,**kw:None)
    chat=HermesCEOChat(owner,url='http://fixture.invalid',model='fixture',key='fixture')
    messages=[{'role':'system','content':'固定规则'}, {'role':'user','content':'首轮原文不可丢'}]
    first=chat.call_tools(messages,[{'function':{'name':'report'}}])
    assert '首轮原文不可丢' in queries[0]
    tc=first['tool_calls'][0]
    messages.extend([{'role':'assistant','tool_calls':[{'id':tc['id']}]},
        {'role':'tool','tool_call_id':tc['id'],'content':'实际平台结果只交接一次'},
        {'role':'user','content':'新增引导原文'}])
    second=chat.call_tools(messages,[{'function':{'name':'report'}}])
    assert second.get('tool_calls'),second
    assert outcomes==['实际平台结果只交接一次']
    assert '新增引导原文' in queries[1]
    assert '实际平台结果只交接一次' not in queries[1]
    assert '首轮原文不可丢' not in queries[1]
    assert '当前固定职责与协调技能' not in queries[1]
