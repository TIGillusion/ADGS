"""Record/check the real idle runtime before and after a controlled reopen."""
import hashlib
import json
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / '.adgs_runtime' / 'reopen-check-20261103'
URL = 'http://127.0.0.1:8021'

def request(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(URL + path, data=data, headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def summaries():
    result={}
    for file in (ROOT/'data/sessions').glob('*.json'):
        data=json.loads(file.read_text(encoding='utf8'))
        result[file.stem]={'sha256':hashlib.sha256(file.read_bytes()).hexdigest(),
            'history_count':len(data.get('history',[])), 'graph_count':len(data.get('graph',[])),
            **{key:digest(data.get(key)) for key in ('history','facts','session_state','execution_results','workflow_log','graph')}}
    return result

mode=sys.argv[1]
state=request('/api/state')
assert not state['busy'] and not state['queue_len'], 'Refuse to close a busy runtime'
if mode=='before':
    OUT.mkdir(parents=True,exist_ok=True)
    session=next(e['session'] for e in reversed(state['events']) if e.get('session'))
    loaded=request('/api/session/load',{'name':session})
    assert loaded['ok'],loaded.get('error')
    data={'session':session,'boot_ts':state.get('boot_ts'),'busy':state['busy'],'queue_len':state['queue_len'],
          'sessions':summaries(),'messages':digest(loaded['messages']),
          'message_count':len(loaded['messages']),
          'artifact_count':sum(len(m.get('artifacts') or []) for m in loaded['messages'])}
    (OUT/'before.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
    shutil.copy2(ROOT/'data/sessions'/(session+'.json'),OUT/(session+'.json'))
    print(json.dumps({k:data[k] for k in ('session','busy','queue_len','message_count','artifact_count')}))
    print('saved_sessions',len(data['sessions']),'current_history',data['sessions'][session]['history_count'],'current_graph',data['sessions'][session]['graph_count'])
else:
    before=json.loads((OUT/'before.json').read_text(encoding='utf8'))
    loaded=request('/api/session/load',{'name':before['session']})
    assert loaded['ok'],loaded.get('error')
    current=summaries()
    changed=[name for name,summary in before['sessions'].items() if current.get(name)!=summary]
    assert not changed, 'Saved session data changed: '+str(changed)
    assert digest(loaded['messages'])==before['messages'], 'Restored transcript changed'
    result={'sessions_unchanged':len(current),'current_session':before['session'],'messages':len(loaded['messages']),
            'artifacts':sum(len(m.get('artifacts') or []) for m in loaded['messages']),
            'boot_ts':state.get('boot_ts'),'projects_supported':bool((state.get('capabilities') or {}).get('projects'))}
    (OUT/'after.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps(result))
