import pytest

from model_router_plugin.classify import classify, classify_rules
from model_router_plugin.reasoning import apply_effort, current_effort, set_model
from model_router_plugin.refusal import looks_like_refusal, matches_guardrail


@pytest.mark.parametrize("text,expected", [
    ("이 문장 영어로 번역해줘: 안녕하세요", "quick"),
    ("hi", "quick"),
    ("이 함수에서 버그 좀 찾아줘\n```python\ndef f(x): return x/0\n```", "code"),
    ("결제 시스템 아키텍처를 설계하고 트레이드오프를 비교 분석해줘", "deep"),
    ("Do a root cause analysis and design a migration plan for our auth service", "deep"),
    ("다음 주 팀 회의 안건을 정리해서 메일 초안을 작성해줘. 참석자는 다섯 명이고 주제는 분기 회고야.", "standard"),
])
def test_rules(text, expected):
    assert classify_rules(text)[0] == expected


def test_hybrid_only_asks_when_unsure(fake_llm):
    llm = fake_llm(parsed={"task_class": "deep", "reason": "planning"})
    cls, why = classify("다음 주 팀 회의 안건을 정리해서 메일 초안을 작성해줘. 참석자는 다섯 명.", [], "hybrid",
                        llm.complete_structured)
    assert cls == "deep" and why.startswith("llm")
    llm.calls.clear()
    cls, _ = classify("번역해줘: hello", [], "hybrid", llm.complete_structured)
    assert cls == "quick" and not llm.calls


def test_llm_failure_falls_back_to_rules():
    def boom(**kw):
        raise RuntimeError("no model")
    cls, why = classify("버그 고쳐줘 def f(): pass", [], "llm", boom)
    assert cls == "code" and "llm unavailable" in why


@pytest.mark.parametrize("text", [
    "I'm sorry, but I can't help with that.",
    "I cannot assist with this request.",
    "죄송하지만 해당 요청은 도와드릴 수 없습니다.",
    "이 내용은 정책 위반 때문에 제공할 수 없습니다.",
])
def test_refusals(text):
    assert looks_like_refusal(text)


@pytest.mark.parametrize("text", [
    "Here is the fix. Note that I can't run it here, but it should work.\n" + "x" * 1000,
    "물론이죠! 아래처럼 하시면 됩니다.",
    "",
])
def test_not_refusals(text):
    assert not looks_like_refusal(text)


def test_guardrail_patterns():
    assert matches_guardrail("Request BLOCKED by corp-guard", ("blocked by corp-guard",))
    assert not matches_guardrail("rate limited", ("blocked by corp-guard",))


def test_effort_codex():
    req = {"model": "a", "reasoning": {"effort": "high", "summary": "auto"}}
    assert apply_effort(req, "codex_responses", "low") == "reasoning.effort=low"
    assert req["reasoning"] == {"effort": "low", "summary": "auto"}
    # never adds a reasoning block the request didn't carry
    bare = {"model": "a"}
    assert apply_effort(bare, "codex_responses", "low") is None and "reasoning" not in bare


def test_effort_anthropic():
    adaptive = {"output_config": {"effort": "high"}, "thinking": {"type": "adaptive"}}
    apply_effort(adaptive, "anthropic_messages", "ultra")
    assert adaptive["output_config"]["effort"] == "max"
    budget = {"thinking": {"type": "enabled", "budget_tokens": 8000}, "max_tokens": 10000}
    apply_effort(budget, "anthropic_messages", "high")
    assert budget["thinking"]["budget_tokens"] == 16000 and budget["max_tokens"] == 20096


def test_effort_chat_completions():
    req = {"extra_body": {"reasoning": {"enabled": True, "effort": "medium"}, "provider": {"x": 1}}}
    apply_effort(req, "chat_completions", "high")
    assert req["extra_body"]["reasoning"]["effort"] == "high" and req["extra_body"]["provider"] == {"x": 1}
    top = {"reasoning_effort": "medium"}
    apply_effort(top, "chat_completions", "low")
    assert top["reasoning_effort"] == "low" and current_effort(top) == "low"


def test_set_model():
    req = {"model": "a"}
    assert set_model(req, "b") and req["model"] == "b"
    assert not set_model(req, "b")
