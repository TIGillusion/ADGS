import random
from types import SimpleNamespace

import pytest

from config.config import config
from core import router
from core.registry import GENERALIST_DESCRIPTION, Node


@pytest.fixture
def fit(monkeypatch):
    similarities = {}
    monkeypatch.setattr(config,'router_sim_weight',3.0)
    monkeypatch.setattr(config,'router_score_weight',0.2)
    monkeypatch.setattr(config,'router_explore_beta',0.15)
    monkeypatch.setattr(config,'top_k',5)
    monkeypatch.setattr(config,'temperature',0.3)
    monkeypatch.setattr(router.emb,'get_embedding',lambda text,query=False:text)
    monkeypatch.setattr(router.emb,'cosine_sim',lambda task,description:similarities.get(description,0.0))
    return similarities


def candidates(fit):
    general=Node('general',GENERALIST_DESCRIPTION,score=20,success_rate=.5)
    expert=Node('expert','当前任务的专长',score=1,success_rate=0)
    unrelated=Node('unrelated','完全不相关的专长',score=100000,success_rate=1)
    expert.tasks_done=100;unrelated.tasks_done=1000
    fit.update({general.describe:.32,expert.describe:.75,unrelated.describe:.30})
    return general,expert,unrelated


def test_real_fit_beats_high_score_and_low_success_does_not_zero_expert(fit):
    general,expert,unrelated=candidates(fit)
    ranked=router.top_k_nodes('任务',[unrelated,general,expert])
    assert ranked[0][0] is expert
    assert router.score_nodes('任务',[expert])[expert.id]>0
    assert unrelated.id not in [node.id for node,_ in ranked]


def test_generalists_win_when_only_unrelated_specialists_exist(fit):
    general,expert,unrelated=candidates(fit)
    fit[expert.describe]=.37
    nodes=[unrelated,expert,general]
    assert [node.id for node,_ in router.top_k_nodes('新领域',nodes)]==['general']
    assert {router.temperature_select('新领域',nodes,rng=random.Random(i)).id for i in range(20)}=={'general'}


def test_many_generalists_do_not_dilute_clear_specialist_fit(fit):
    general,expert,unrelated=candidates(fit)
    fit[expert.describe]=.556
    generals=[Node(str(i),GENERALIST_DESCRIPTION) for i in range(10)]
    assert [n.id for n,_ in router.top_k_nodes('数论',generals+[unrelated,expert])]==['expert']


def test_cumulative_score_cannot_change_same_fit_ranking(fit):
    a=Node('a','相同专长',score=1,success_rate=.8)
    b=Node('b','相同专长',score=99999,success_rate=.8)
    fit[a.describe]=.7
    values=router.score_nodes('任务',[a,b])
    assert values['a']==values['b']


def test_exploration_is_bounded_and_only_matched_candidates_enter_shortlist(fit):
    general,expert,unrelated=candidates(fit)
    fit[expert.describe]=.48
    details=router.routing_details('任务',[general,expert,unrelated])
    assert details['general']['explore_boost']<=1.15
    assert details['general']['explore_boost']>details['unrelated']['explore_boost']
    assert [n.id for n,_ in router.top_k_nodes('任务',[general,expert,unrelated])]==['general']


@pytest.mark.parametrize('heading',['当前任务','任务目标','目标'])
def test_long_task_boilerplate_does_not_hide_current_goal(fit,monkeypatch,heading):
    seen=[]
    monkeypatch.setattr(router.emb,'get_embedding',lambda text,query=False:seen.append(text) or text)
    contract='# 任务书\n\n## 总体任务\n大项目\n\n## 背景\n'+('冗长历史背景'*3000)+f'\n\n## {heading}\n数论独立验证\n\n## 需求明细\n检查恒等式\n\n## 工作区\nD:/private\n'
    router.score_nodes(contract,[Node('a','数学')])
    assert seen[0].startswith('数论独立验证')
    assert '检查恒等式' in seen[0]
    assert '冗长历史背景' not in seen[0] and 'D:/private' not in seen[0]


def test_log_temperature_sampling_preserves_ratios_under_scaling(fit):
    a,b=Node('a','a'),Node('b','b')
    class Capture:
        def choices(self,population,weights,k):
            self.weights=weights
            return [population[0]]
    first,second=Capture(),Capture()
    router._sample_ranked([(a,.2),(b,.1)],.3,first)
    router._sample_ranked([(a,.0002),(b,.0001)],.3,second)
    assert first.weights==pytest.approx(second.weights)
    assert first.weights[0]>.9


def test_embedding_unavailable_does_not_claim_match_or_follow_cumulative_score(fit,monkeypatch):
    def unavailable(*a,**kw):raise RuntimeError('offline')
    monkeypatch.setattr(router.emb,'get_embedding',unavailable)
    nodes=[Node('new',GENERALIST_DESCRIPTION,score=1),Node('old','有经验的角色',score=9999)]
    details=router.routing_details('任务',nodes)
    assert all(row['similarity'] is None and not row['embedding_available'] for row in details.values())
    assert details['new']['score']==details['old']['score']


def test_ceo_and_a2a_directory_share_fit_including_implicit_parent_query(fit,monkeypatch):
    import utils.context_probe
    monkeypatch.setattr(utils.context_probe,'probe_context_window',lambda *a,**kw:None)
    import web_app as web
    nodes=list(candidates(fit))
    monkeypatch.setattr(web,'registry',SimpleNamespace(active=lambda:nodes))
    monkeypatch.setattr(web.execution_store,'get',lambda eid:{'node_id':'expert','task_md':'当前子任务'})
    monkeypatch.setattr(web,'_mesh_collab_index',lambda:{})
    rows=web._mesh_directory_impl('',15,'parent')
    assert [row['id'] for row in rows]==[n.id for n,_ in router.top_k_nodes('当前子任务',nodes,k=15)]
    assert rows[0]['same_role'] and rows[0]['relevance']==.75


def test_zero_temperature_is_deterministic_and_route_embeds_task_once(fit,monkeypatch):
    nodes=list(candidates(fit));calls=[]
    monkeypatch.setattr(router.emb,'get_embedding',lambda text,query=False:calls.append(query) or text)
    selected=router.route('任务',reg=SimpleNamespace(active=lambda:nodes),temperature=0,verbose=False)
    assert selected.id=='expert' and calls.count(True)==1
