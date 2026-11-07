import threading
import time
from types import SimpleNamespace

import pytest
import utils.context_probe
utils.context_probe.probe_context_window=lambda *a,**kw:None

from core.delegation_runtime import DelegationThreads, ExecutionSlot, owned_execution_slot, lend_execution_slot
from core.node import DynamicSemaphore


def test_recursive_wait_can_run_more_descendants_than_old_pool_size_with_one_slot():
    sem=DynamicSemaphore(1);executor=DelegationThreads();visited=[]
    def work(depth):
        assert sem.acquire(timeout=4)
        with owned_execution_slot(str(depth),sem,time.monotonic()+5):
            visited.append(depth)
            if depth<12:
                future=executor.submit(work,depth+1)
                with lend_execution_slot(str(depth)):
                    return future.result(timeout=4)
            return 'done'
    assert executor.submit(work,0).result(timeout=6)=='done'
    assert sorted(visited)==list(range(13)) and sem._held==0


def test_mesh_wait_lends_actual_parent_slot_and_reacquires_before_return(monkeypatch):
    import web_app as web
    sem=DynamicSemaphore(1);sem.acquire();finished=threading.Event()
    record={'delegation_id':'d','parent_execution_id':'parent','status':'running','created_at':time.time()}
    monkeypatch.setattr(web,'_mesh_delegations',{'d':record})
    def child():
        assert sem.acquire(timeout=3)
        sem.release()
        with web._mesh_lock:
            record['status']='passed';web._mesh_lock.notify_all()
        finished.set()
    future=DelegationThreads().submit(child)
    with owned_execution_slot('parent',sem,time.monotonic()+4):
        result=web._mesh_wait_impl('d',3)
        assert finished.is_set() and result['status']=='passed' and sem._held==1
    future.result(timeout=1)
    assert sem._held==0


def test_close_during_wait_does_not_release_someone_elses_slot():
    sem=DynamicSemaphore(1);sem.acquire();slot=ExecutionSlot(sem,time.monotonic()+3)
    with slot.lend():
        assert sem.acquire(timeout=.5)
        slot.close()
        assert sem._held==1
    assert sem._held==1
    sem.release();assert sem._held==0


def test_cancelled_parent_does_not_leak_or_steal_child_slot():
    sem=DynamicSemaphore(1);sem.acquire();cancel=threading.Event()
    slot=ExecutionSlot(sem,time.monotonic()+2,cancel)
    with pytest.raises(TimeoutError):
        with slot.lend():
            assert sem.acquire(timeout=.5)
            cancel.set()
    slot.close();assert sem._held==1
    sem.release();assert sem._held==0


def test_reacquire_ignores_old_wall_deadline_until_operator_cancels():
    sem=DynamicSemaphore(1);sem.acquire();cancel=threading.Event()
    slot=ExecutionSlot(sem,time.monotonic()+.03,cancel)
    started=time.monotonic()
    timer=threading.Timer(.15,cancel.set)
    timer.start()
    try:
        with pytest.raises(TimeoutError,match='取消'):
            with slot.lend():assert sem.acquire(timeout=.5)
    finally:
        timer.cancel();timer.join()
    assert time.monotonic()-started>=.12
    slot.close();assert cancel.is_set() and sem._held==1
    sem.release()


def test_parallel_wait_calls_only_restore_the_slot_after_last_waiter():
    sem=DynamicSemaphore(1);sem.acquire();slot=ExecutionSlot(sem,time.monotonic()+5)
    barrier=threading.Barrier(3);exits=[threading.Event(),threading.Event()]
    def wait(index):
        with slot.lend():
            barrier.wait(timeout=3)
            assert exits[index].wait(3)
    pool=DelegationThreads();futures=[pool.submit(wait,i) for i in range(2)]
    barrier.wait(timeout=3)
    assert sem._held==0
    exits[0].set();futures[0].result(timeout=2)
    assert sem._held==0
    exits[1].set();futures[1].result(timeout=2)
    assert sem._held==1
    slot.close();assert sem._held==0


def test_nonblocking_or_completed_poll_does_not_lend_parent_slot(monkeypatch):
    import web_app as web
    sem=DynamicSemaphore(1);sem.acquire();releases=[];release=sem.release
    monkeypatch.setattr(sem,'release',lambda:releases.append(1) or release())
    record={'parent_execution_id':'poll-parent','status':'running','created_at':time.time()}
    monkeypatch.setattr(web,'_mesh_delegations',{'poll':record})
    with owned_execution_slot('poll-parent',sem,time.monotonic()+2):
        assert web._mesh_wait_impl('poll',0)['status']=='running'
        record['status']='passed'
        assert web._mesh_wait_impl('poll',1)['status']=='passed'
        assert releases==[] and sem._held==1
    assert len(releases)==1 and sem._held==0


@pytest.fixture
def mesh(monkeypatch,tmp_path):
    import web_app as web
    from core.execution_store import ExecutionStore
    store=ExecutionStore(tmp_path/'executions.sqlite')
    store.put('parent',node_id='same-role',depth=0,deadline_at=time.time()+30)
    monkeypatch.setattr(web,'registry',SimpleNamespace(get=lambda nid:object()))
    monkeypatch.setattr(web,'execution_store',store)
    monkeypatch.setattr(web,'_mesh_delegations',{})
    monkeypatch.setattr(web,'_persist_delegations',lambda:None)
    monkeypatch.setattr(web.bus,'emit',lambda *a,**kw:None)
    return web


def test_same_role_delegation_creates_independent_execution(mesh,monkeypatch):
    monkeypatch.setattr(mesh,'_MESH_POOL',SimpleNamespace(submit=lambda *args:None))
    result=mesh._mesh_delegate_impl('same-role','# 当前子任务\n独立分支','parent')
    assert result['ok']
    record=mesh._mesh_delegations[result['delegation_id']]
    assert record['child_node_id']==record['parent_node_id']=='same-role'
    assert record['child_execution_id']!='parent'


def test_submission_failure_is_terminal_and_pending_cap_is_enforced(mesh,monkeypatch):
    def fail(*args):raise RuntimeError('thread unavailable')
    monkeypatch.setattr(mesh,'_MESH_POOL',SimpleNamespace(submit=fail))
    result=mesh._mesh_delegate_impl('same-role','子任务','parent')
    assert not result['ok']
    assert next(iter(mesh._mesh_delegations.values()))['status']=='interrupted'
    monkeypatch.setattr(mesh,'_MESH_PENDING_CAP',1)
    mesh._mesh_delegations['running']={'status':'running'}
    assert '上限' in mesh._mesh_delegate_impl('same-role','下一任务','parent')['error']


def test_merged_instructions_require_work_sharing_and_respect_depth():
    from core.node import NODE
    from config.config import config
    node=NODE.__new__(NODE);node.node_id='same-role';node._execution_id='p'
    instructions=node._a2a_section()
    assert '困难且可拆' in instructions and '全新的执行实例和对话上下文' in instructions
    assert '同批独立子任务先全部派发再等待' in instructions
    node._delegation_depth=config.max_delegation_depth
    assert '不要再发新委派' in node._a2a_section()


def test_legacy_brain_also_prefers_decomposing_difficult_work():
    from core.agent import AGENT
    agent=AGENT.__new__(AGENT);agent.node_id='legacy';agent.describe='通用'
    prompt=agent._default_prompt()
    assert '默认先 split_tasks 分工' in prompt and '串行阶段也可使用 depends_on' in prompt
