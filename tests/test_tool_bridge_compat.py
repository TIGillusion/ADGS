import json
from types import SimpleNamespace
from core.tool_bridge_compat import normalize_bridge_arguments, install_bridge_compat


def test_json_encoded_calls_retains_exact_tool_and_payload_without_mutation():
    entry={'name':'mcp__mesh__delegate_to_node','arguments':{'task_md':'正式任务\n保留路径 D:\\工作区','node_id':'a'}}
    original={'calls':json.dumps([entry],ensure_ascii=False)}
    normalized=normalize_bridge_arguments(original)
    assert normalized=={'calls':[entry]}
    assert isinstance(original['calls'],str)


def test_ambiguous_or_missing_name_is_still_validated_by_original_parser():
    calls=[]
    def original(args):
        calls.append(args)
        return (None,"missing name") if 'name' not in args['calls'][0] else (args['calls'],None)
    bridge=SimpleNamespace(normalize_tool_call_entries=original)
    install_bridge_compat(bridge)
    result=bridge.normalize_tool_call_entries({'calls':'[{"arguments":{}}]'})
    assert result==(None,'missing name')
    assert calls==[{'calls':[{'arguments':{}}]}]
    bad={'calls':'not json'};assert normalize_bridge_arguments(bad) is bad
    scalar={'calls':'{"name":"x"}'};assert normalize_bridge_arguments(scalar) is scalar


def test_compat_preserves_native_batch_rejection_and_is_idempotent():
    def original(args):return None,'multi-local batches rejected'
    bridge=SimpleNamespace(normalize_tool_call_entries=original)
    install_bridge_compat(bridge);once=bridge.normalize_tool_call_entries;install_bridge_compat(bridge)
    assert bridge.normalize_tool_call_entries is once
    assert once({'calls':'[{"name":"read_file"},{"name":"terminal"}]'})==(None,'multi-local batches rejected')
