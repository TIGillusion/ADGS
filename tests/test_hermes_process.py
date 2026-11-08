from types import SimpleNamespace
import pytest
from core.hermes_process import OutputRouter,TurnOutput,ResidentPool


@pytest.mark.parametrize('chunk_size',[1,2,7,1000])
def test_terminal_boundaries_survive_arbitrary_chunking(chunk_size):
    orphan=[];first=[];second=[]
    router=OutputRouter(orphan.append)
    route=TurnOutput('abc',first.append);router.bind(route)
    wire='startup'+route.start+'中文\n工具输出'+route.end+'late'
    for index in range(0,len(wire),chunk_size):router.feed(wire[index:index+chunk_size])
    assert route.drained.is_set()
    assert ''.join(first)=='中文\n工具输出'
    next_route=TurnOutput('next',second.append);router.bind(next_route)
    router.feed(next_route.start+'second'+next_route.end)
    assert ''.join(second)=='second'
    assert ''.join(orphan)=='startuplate'


def test_next_turn_cannot_steal_undrained_output():
    router=OutputRouter();first=TurnOutput('a',lambda text:None);router.bind(first)
    with pytest.raises(RuntimeError,match='drained'):router.bind(TurnOutput('b',lambda text:None))


def test_short_output_is_visible_while_a_tool_is_still_running():
    output=[]
    router=OutputRouter()
    route=TurnOutput('long-boundary-token',output.append)
    router.bind(route)
    router.feed(route.start+'开始执行\n')
    assert ''.join(output)=='开始执行\n'
    assert not route.drained.is_set()


def test_pool_only_reuses_owned_live_context_and_cleans_dead_process():
    pool=ResidentPool();closed=[]
    process=SimpleNamespace(session_id='original',alive=lambda:True,close=lambda **kw:closed.append(kw))
    pool.add('execution',('profile','workspace','model'),process)
    assert pool.get('execution',('profile','workspace','model'),'original') is process
    with pytest.raises(RuntimeError,match='different'):pool.get('execution',('other','workspace','model'),'original')
    with pytest.raises(RuntimeError,match='different'):pool.get('execution',('profile','workspace','model'),'other-session')
    process.alive=lambda:False
    assert pool.get('execution',('profile','workspace','model'),'original') is None
    assert closed==[{'grace':0}]
