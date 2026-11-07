import json
import pytest
from core.message_queue import DurableMessageQueue


def item():
    return {'id':'same-id','text':'完整中文引导\n第二行🙂','session':'chat-a','workspace':'D:/work','mode':'queue','queued_at':123.5}


def test_pending_message_full_identity_survives_owner_restart(tmp_path):
    path=tmp_path/'queue.json';queue=DurableMessageQueue(path);queue.activate();queue.append(item())
    restored=DurableMessageQueue(path);restored.activate()
    assert list(restored)==[item()]
    restored[0]['text']='已编辑全文';restored[0]['mode']='interrupt';restored.checkpoint()
    again=DurableMessageQueue(path);again.activate()
    assert again[0]['id']=='same-id' and again[0]['mode']=='interrupt' and again[0]['text']=='已编辑全文'
    again.pop();empty=DurableMessageQueue(path);empty.activate();assert list(empty)==[]


def test_proxy_import_does_not_load_or_overwrite_runtime_queue(tmp_path):
    path=tmp_path/'queue.json';path.write_text(json.dumps([item()],ensure_ascii=False),encoding='utf-8');before=path.read_bytes()
    proxy=DurableMessageQueue(path);assert list(proxy)==[]
    proxy.clear();assert path.read_bytes()==before


def test_failed_atomic_write_preserves_previous_queue_and_file(tmp_path,monkeypatch):
    import core.message_queue as module
    path=tmp_path/'queue.json';queue=DurableMessageQueue(path);queue.activate();queue.append(item());before=path.read_bytes()
    def fail(*a):raise OSError('disk unavailable')
    monkeypatch.setattr(module.os,'replace',fail)
    with pytest.raises(OSError):queue.append({**item(),'id':'new'})
    assert list(queue)==[item()] and path.read_bytes()==before
    queue[0]['text']='not saved'
    with pytest.raises(OSError):queue.checkpoint()
    assert list(queue)==[item()]


def test_invalid_checkpoint_is_not_silently_discarded(tmp_path):
    path=tmp_path/'queue.json';path.write_text('{broken',encoding='utf-8');before=path.read_bytes()
    with pytest.raises(ValueError):DurableMessageQueue(path).activate()
    assert path.read_bytes()==before


def test_queue_slices_clear_and_rollback_persist(tmp_path):
    path=tmp_path/'queue.json';queue=DurableMessageQueue(path);queue.activate();queue.extend([item(),{**item(),'id':'second'}]);queue[:]=queue[1:]
    restored=DurableMessageQueue(path);restored.activate();assert [v['id'] for v in restored]==['second']
    restored.insert(0,item());restored.remove(item());restored.clear()
    final=DurableMessageQueue(path);final.activate();assert not final


def test_real_queue_endpoints_persist_edit_promotion_and_revocation(tmp_path,monkeypatch):
    import utils.context_probe
    monkeypatch.setattr(utils.context_probe,'probe_context_window',lambda *a,**kw:None)
    import web_app as web
    path=tmp_path/'queue.json';queue=DurableMessageQueue(path);queue.activate();queue.append(item())
    monkeypatch.setattr(web,'_queue',queue)
    monkeypatch.setattr(web,'_session_busy',lambda _:True)
    monkeypatch.setattr(web,'worker_launch_on_hold',lambda:False)
    client=web.app.test_client()
    assert client.put('/api/queue/same-id',json={'text':'编辑后全文🙂'}).status_code==200
    assert client.post('/api/queue/same-id/guide').status_code==200
    restored=DurableMessageQueue(path);restored.activate()
    assert restored[0]['id']=='same-id' and restored[0]['mode']=='interrupt' and restored[0]['text']=='编辑后全文🙂'
    assert client.delete('/api/queue/same-id').status_code==200
    restored=DurableMessageQueue(path);restored.activate();assert not restored


def test_owner_bootstrap_restores_pending_queue_before_threads(tmp_path,monkeypatch):
    import web_app as web
    path=tmp_path/'queue.json';saved=DurableMessageQueue(path);saved.activate();saved.append(item())
    queue=DurableMessageQueue(path);monkeypatch.setattr(web,'_queue',queue)
    monkeypatch.setattr(web,'_bootstrapped',False)
    monkeypatch.setattr(web,'_bootstrap_started',{'adgs-evolution','adgs-autosave'})
    web._bootstrap_runtime()
    assert list(queue)==[item()]
