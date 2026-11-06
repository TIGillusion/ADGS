"""Model-generated malformed values must produce protocol repair, not a crash."""
from unittest.mock import Mock
import sys

import pytest

from core.agent import AGENT
from core.contracts import validate_arguments
from utils.chat import _clean_args, validate_arguments as chat_validate


@pytest.mark.parametrize("validator", [validate_arguments, chat_validate])
def test_large_json_integer_validation_never_overflows(validator):
    huge = 10 ** 400
    assert validator(huge, {"type": "integer"}) is None
    assert validator(huge, {"type": "number", "maximum": 10})
    assert validator(float("nan"), {"type": "number"})


@pytest.mark.parametrize("validator", [validate_arguments, chat_validate])
def test_common_schema_constraints_are_enforced_by_both_callers(validator):
    assert validator("", {"type": "string", "minLength": 1})
    assert validator([1, 2], {"type": "array", "maxItems": 1})
    assert validator({"unexpected": 1}, {"type": "object", "additionalProperties": False})


def test_json_integer_parser_limit_is_rejected_without_exception():
    limit = sys.get_int_max_str_digits()
    if not limit:
        pytest.skip("Interpreter integer parser limit disabled")
    assert _clean_args('{"score":' + "9" * (limit + 1) + "}") is None


def test_huge_acceptance_score_repairs_report_without_execution_or_score_change():
    agent = AGENT("http://fixture.invalid", "fixture", "fixture")
    malformed = {"tool_calls": [{"name": "accept_report", "arguments": {
        "passed": True, "score": 10 ** 400, "passed_items": [], "failed_items": []}}]}
    agent.chat.call_tools = Mock(return_value=malformed)
    result = agent.evaluate("contract", "evidence", old_score=50)
    assert result["unjudged"] and result["outcome"] == "protocol_error"
    assert result["score"] == 50 and agent.chat.call_tools.call_count == 2
