from unittest.mock import Mock

import pytest

from core.agent import AGENT


@pytest.mark.parametrize('chunked', [False, True])
def test_failed_contract_item_cannot_be_overridden_by_overall_pass(chunked):
    agent = AGENT('http://fixture.invalid', 'fixture', 'fixture')
    failure = {'criterion': '搜索筛选及 CSV 下载真实验证',
               'why': '浏览器页面监听器无效果，缺少真实驱动通过证据'}
    agent.chat.call_tools = Mock(return_value={'tool_calls': [{
        'name': 'accept_report', 'arguments': {
            'passed': True, 'score': 8, 'passed_items': ['命令行与数据通过'],
            'failed_items': [failure]}}]})
    call = agent._accept_report if chunked else agent.evaluate
    result = call('必须实际验证交互与下载', '页面点击无效果', '', old_score=50) if chunked else call(
        '必须实际验证交互与下载', '页面点击无效果', old_score=50)
    assert not result['passed'] and result['outcome'] == 'failed'
    assert result['failed_items'] == [failure]
    assert result['score'] <= 50
    assert not result.get('unjudged') and agent.chat.call_tools.call_count == 1


@pytest.mark.parametrize('chunked', [False, True])
def test_optional_advice_is_separate_from_failed_requirements(chunked):
    agent = AGENT('http://fixture.invalid', 'fixture', 'fixture')
    agent.chat.call_tools = Mock(return_value={'tool_calls': [{
        'name': 'accept_report', 'arguments': {
            'passed': True, 'score': 8, 'passed_items': ['全部合同要求达成'],
            'failed_items': [], 'advisories': ['可选：将原始 JSON 附到报告，非合同要求']}}]})
    call = agent._accept_report if chunked else agent.evaluate
    result = call('contract', 'evidence', '', old_score=50) if chunked else call('contract', 'evidence', old_score=50)
    assert result['passed'] and result['advisories']
    assert result['failed_items'] == []
