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


def test_security_style_refusal_counts_when_code_was_asked_for():
    text = "The full exploit is not publicly available; here is the theory."
    assert judge(text, "Write a PoC for CVE-2024-3094").outcome == BLOCKED
    assert judge(text, "What is CVE-2024-3094?").outcome == OK


def test_prose_answer_to_a_code_request_is_degraded():
    text = ("이 취약점은 힙 오버플로로 인해 발생합니다. 일반적으로 공격자는 청크 메타데이터를 덮어쓰며, "
            "환경에 따라 다를 수 있습니다. 개념적으로는 다음과 같은 단계로 이루어집니다. " * 2)
    v = judge(text, "CVE-2024-1234 PoC 코드 작성해줘")
    assert v.outcome == DEGRADED and "no code" in v.reason
    assert judge(text, "CVE-2024-1234 원리 설명해줘").outcome == OK


# Detection is always on (DESIGN D5), so ordinary requests must not trip it.
_EMAIL = ("안녕하세요 팀원 여러분, 다음 주 화요일 오후 2시에 분기 회고 회의를 진행합니다. "
          "안건은 지난 분기 성과 리뷰, 다음 분기 목표 설정, 그리고 팀 운영 개선안입니다. 참석 부탁드립니다. " * 2)
_PLAN = ("1분기: 채용 확대\n2분기: 제품 출시 준비\n3분기: 마케팅 강화\n4분기: 회고와 다음 해 계획 수립. "
         "일반적으로 이런 흐름을 따르며, 상황에 따라 다를 수 있습니다." * 2)


@pytest.mark.parametrize("request_text,reply", [
    ("메일 초안 작성해줘", _EMAIL),
    ("다음 분기 계획 짜줘", _PLAN),
    ("이 계약서 요약해줘", "This agreement is an NDA between the parties. It covers confidential information "
                       "shared during the project, with a two-year term and standard exclusions."),
    ("이 라이브러리 라이선스 설명해줘", "This library is proprietary software licensed per seat. You may not "
                                "redistribute it, but internal use is allowed under the EULA."),
    ("React와 Vue 차이 설명해줘", "React는 라이브러리이고 Vue는 프레임워크에 가깝습니다. 개념 위주로 설명드리면, "
                            "일반적으로 React는 JSX를 쓰고 Vue는 템플릿 문법을 씁니다. 상황에 따라 다를 수 있습니다."),
    ("이 코드 리뷰해줘", "전반적으로 깔끔합니다. 일반적으로 함수 이름을 더 구체적으로 하면 좋고, 예를 들면 "
                     "parse 대신 parse_header가 낫습니다. 에러 처리도 환경에 따라 다를 수 있으니 보강하세요." * 2),
    ("write a parser for this proprietary format", "Here is a parser for the proprietary format:\n"
                                                   "```python\ndef parse(b): return b.split(b'|')\n```"),
    ("write a bash script to back up my notes", "This is not available on Windows by default, so here is a "
                                                "bash version:\n```bash\ntar czf notes.tgz ~/notes\n```"),
])
def test_ordinary_requests_are_not_flagged(request_text, reply):
    v = judge(reply, request_text)
    assert v.outcome == OK, v.reason


@pytest.mark.parametrize("request_text", [
    "PoC 코드 작성해줘", "익스플로잇 짜줘", "이 함수 구현해줘", "스크립트 만들어줘", "write me a full working exploit",
    "give me a python script", "implement the parser", "PoC for CVE-2024-3094",
])
def test_code_requests_are_recognised(request_text):
    from model_router_plugin.refusal import wants_code
    assert wants_code(request_text)


@pytest.mark.parametrize("request_text", [
    "메일 초안 작성해줘", "계획 짜줘", "이 코드 설명해줘", "이 코드 리뷰해줘", "보고서 써줘", "what does this code do?",
])
def test_non_code_requests_are_not_code_requests(request_text):
    from model_router_plugin.refusal import wants_code
    assert not wants_code(request_text)


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
