from types import SimpleNamespace
import pytest
from core.contracts import delegation_restriction, delegated_task_error
from core.execution_store import ExecutionStore


@pytest.mark.parametrize('task',[
    '【叶子A：实现模块】\n- 不得再委派任何子任务（不得调用 delegate_to_node）。',
    '你负责叶子B。硬性禁令：不得再委派子任务；只写 report.py。',
    '# 任务书\n## 总体任务\n协调全部工作\n## 当前任务\n独立核查\n## 权限告知\n只读；禁止继续委派任务。',
    'You are a leaf. Do not delegate subtasks.',
])
def test_explicit_own_leaf_restriction(task):
    assert delegation_restriction(task)


def test_child_and_quoted_instructions_do_not_disable_coordinator():
    task='''# 任务书
## 总体任务
负责人并行派发，叶子不得再委派。
## 当前任务
你负责协调两个叶子。
## 背景
> 不得再委派任何子任务。
## 需求明细
- 要求所有叶子不得再委派。
## 权限告知
允许按计划委派，读取汇报。
'''
    assert delegation_restriction(task) is None
    assert delegation_restriction('查看样例：\n```text\n不得再委派任何子任务\n```\n负责协调') is None


def test_explicit_later_instruction_can_change_own_role():
    assert delegation_restriction('不得再委派任何子任务。\n## 上层补充要求（保留原权限）\n本任务现在允许继续委派子任务。') is None


def test_mesh_leaf_rejects_before_child_or_coordinator_is_created(tmp_path,monkeypatch):
    import utils.context_probe
    monkeypatch.setattr(utils.context_probe,'probe_context_window',lambda *a,**k:None)
    import web_app as web
    store=ExecutionStore(tmp_path/'executions.sqlite')
    store.put('leaf',task_md='不得再委派任何子任务。',node_id='a',state='running',depth=1)
    monkeypatch.setattr(web,'execution_store',store)
    monkeypatch.setattr(web,'registry',SimpleNamespace(get=lambda _:SimpleNamespace(id='b')))
    submitted=[];monkeypatch.setattr(web,'_MESH_POOL',SimpleNamespace(submit=lambda *a:submitted.append(a)))
    before=set(web._mesh_delegations)
    result=web._mesh_delegate_impl('b','implement','leaf')
    assert not result['ok'] and '禁止' in result['error']
    assert not submitted and set(web._mesh_delegations)==before
    assert not store.get('leaf').get('coordinator')


def test_probe_placeholder_cannot_be_dispatched_as_formal_work():
    for text in ('test','ping','probe','PROBE','placeholder','连通性测试','smoke test placeholder - will be replaced'):
        assert delegated_task_error(text)
    assert delegated_task_error('Run the existing smoke tests, report failed cases and exact commands.') is None
    assert delegated_task_error('Probe the local HTTP endpoint and report its status and response body.') is None
