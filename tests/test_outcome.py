import ast
from pathlib import Path

import pytest

from model_router_plugin.outcome import BLOCKED, DEGRADED, ERROR, OK, TurnSignals, judge


# --- turn signals (post_api_request / api_request_error) -------------------------------------

def test_turn_that_ends_on_success_has_no_failure():
    sig = TurnSignals()
    sig.record_error("t1", reason="rate_limit", retryable=True)
    sig.record_success("t1")
    assert sig.ended_on_error("t1") is None


def test_turn_that_ends_on_error_keeps_the_last_failure():
    sig = TurnSignals()
    sig.record_success("t1")
    sig.record_error("t1", reason="content_policy_blocked", retryable=False, status_code=400,
                     error={"message": "blocked by policy"}, provider="OpenAI-Codex", model="gpt-6.1-sol")
    failure = sig.ended_on_error("t1")
    assert failure.reason == "content_policy_blocked" and failure.policy
    assert failure.provider == "openai-codex" and failure.status_code == 400
    assert failure.message == "blocked by policy"


def test_structured_refusal_in_the_error_body_is_a_policy_block():
    sig = TurnSignals()
    sig.record_error("t1", reason="unknown", error={"stop_reason": "refusal"})
    sig.record_error("t2", reason="format_error", error={"stop_details": {"category": "cyber"}})
    assert sig.ended_on_error("t1").structured_block == "refusal"
    assert sig.ended_on_error("t2").policy


def test_turns_are_independent_and_bounded():
    sig = TurnSignals(max_turns=2)
    sig.record_error("a", reason="x")
    sig.record_error("b", reason="y")
    sig.record_error("c", reason="z")
    assert sig.ended_on_error("a") is None  # evicted
    assert sig.ended_on_error("b").reason == "y" and sig.ended_on_error("c").reason == "z"
    assert sig.ended_on_error("") is None


def test_odd_payload_types_do_not_raise():
    sig = TurnSignals()
    sig.record_error("t", reason=None, retryable="yes", status_code="400", error=RuntimeError("boom"))
    failure = sig.ended_on_error("t")
    assert failure.retryable is None and failure.status_code is None and failure.message == "boom"


# --- judge ------------------------------------------------------------------------------------

def failure(reason, **kw):
    sig = TurnSignals()
    sig.record_error("t", reason=reason, **kw)
    return sig.ended_on_error("t")


def test_policy_error_is_blocked():
    v = judge("ChatGPT or Codex Subscription rejected the request", failure=failure("content_policy_blocked"))
    assert v.outcome == BLOCKED and "content_policy_blocked" in v.reason and v.climb


def test_non_policy_error_is_error_and_says_when_retries_ran_out():
    assert judge("…", failure=failure("model_not_found", retryable=False)).outcome == ERROR
    v = judge("…", failure=failure("rate_limit", retryable=True))
    assert v.outcome == ERROR and "retries exhausted" in v.reason


def test_user_guardrail_phrase_in_the_error_message_is_blocked():
    f = failure("unknown", error={"message": "Request flagged by Acme Shield"})
    assert judge("…", failure=f, guardrail_patterns=("acme shield",)).outcome == BLOCKED
    assert judge("…", failure=f).outcome == ERROR


def test_soft_refusal_is_blocked_with_the_matched_text():
    v = judge("I'm sorry, but I can't help with that.")
    assert v.outcome == BLOCKED and "can't help" in v.reason


def test_security_only_refusal_needs_the_security_switch():
    text = "The full exploit is not publicly available; here is the theory."
    assert judge(text).outcome == OK
    assert judge(text, security=True).outcome == BLOCKED


def test_prose_answer_to_a_code_request_is_degraded_only_with_the_security_switch():
    text = ("이 취약점은 힙 오버플로로 인해 발생합니다. 일반적으로 공격자는 청크 메타데이터를 덮어쓰며, "
            "환경에 따라 다를 수 있습니다. 개념적으로는 다음과 같은 단계로 이루어집니다. " * 2)
    assert judge(text, "CVE-2024-1234 PoC 코드 작성해줘").outcome == OK
    v = judge(text, "CVE-2024-1234 PoC 코드 작성해줘", security=True)
    assert v.outcome == DEGRADED and "no code" in v.reason


@pytest.mark.parametrize("text", ["", "   "])
def test_empty_reply_is_an_error(text):
    assert judge(text).outcome == ERROR


def test_a_normal_answer_is_ok():
    v = judge("Here is the parser:\n```python\ndef parse(s): ...\n```")
    assert v.outcome == OK and not v.climb


def test_outcome_module_does_not_depend_on_task_classification():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "outcome.py").read_text())
    imported = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not any("classify" in name for name in imported)
