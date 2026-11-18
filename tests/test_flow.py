"""A turn through the real register(): plan -> route requests -> fail -> next model -> success."""

import json

import pytest

import model_router_plugin as plugin
from model_router_plugin.scope import Candidate


@pytest.fixture
def wired(ctx):
    plugin.register(ctx)
    return ctx


def _req(ctx, **kw):
    payload = {"request": {"model": "gpt-6.2-sol", "reasoning": {"effort": "high", "summary": "auto"}},
               "turn_id": "t1", "provider": "openai-codex", "api_mode": "codex_responses"}
    payload.update(kw)
    return ctx.middleware["llm_request"](**payload)


def _start(ctx, msg, turn="t1", session="s1"):
    ctx.hooks["pre_llm_call"][0](session_id=session, user_message=msg, conversation_history=[],
                                 turn_id=turn, model="gpt-6.2-sol", platform="cli")


def test_register_surface(wired):
    assert set(wired.hooks) == {"pre_llm_call", "post_llm_call", "api_request_error",
                                "transform_api_error_classification", "transform_llm_output"}
    assert "llm_request" in wired.middleware
    assert "route_explore" in wired.tools and "route" in wired.commands and "model-router" in wired.cli
    assert wired.hooks["transform_llm_output"][1] is False  # hold_stream off by default


def test_quick_turn_goes_to_small_model_with_low_effort(wired):
    _start(wired, "번역해줘: good morning")
    out = _req(wired)
    assert out["request"]["model"] == "gpt-mini"
    assert out["request"]["reasoning"]["effort"] == "low"
    assert out["source"] == "model-router"


def test_public_mode_stays_on_the_request_provider(wired):
    # deep route starts on anthropic, but the session is on openai-codex and cross_provider is off
    _start(wired, "결제 시스템 아키텍처를 설계하고 트레이드오프를 비교 분석해줘")
    out = _req(wired)
    assert out["request"]["model"] == "gpt-6.2-sol" and out["request"]["reasoning"]["effort"] == "xhigh"


def test_guardrail_moves_to_next_allowed_model_then_fallback_provider(wired):
    _start(wired, "버그 고쳐줘 def f(): return 1/0")  # code: no route -> all allowed @high
    assert _req(wired) is None  # already gpt-6.2-sol @high: nothing to rewrite
    wired.hooks["api_request_error"][0](turn_id="t1", provider="openai-codex",
                                        reason="content_policy_blocked", retryable=False,
                                        error={"message": "flagged"})
    assert _req(wired)["request"]["model"] == "gpt-mini"
    wired.hooks["api_request_error"][0](turn_id="t1", provider="openai-codex", reason="model_not_found")
    # nothing allowed left on openai-codex: leave Hermes' own fallback model alone
    assert _req(wired, request={"model": "gpt-daybreak"}) is None
    # Hermes fell back to anthropic: route to the allowed anthropic model
    out = _req(wired, provider="anthropic", api_mode="anthropic_messages",
               request={"model": "claude-x", "output_config": {"effort": "medium"}})
    assert out["request"]["model"] == "claude-opus-4-8" and out["request"]["output_config"]["effort"] == "high"


def test_transient_errors_keep_the_model(wired):
    _start(wired, "버그 고쳐줘 def f(): return 1/0")
    _req(wired)
    wired.hooks["api_request_error"][0](turn_id="t1", provider="openai-codex", reason="rate_limit", retryable=True)
    assert _req(wired) is None  # same model, same effort: nothing to rewrite


def test_two_failures_cool_a_model_down(wired):
    for turn in ("a", "b"):
        _start(wired, "버그 고쳐줘 def f(): return 1/0", turn=turn)
        _req(wired, turn_id=turn)
        wired.hooks["api_request_error"][0](turn_id=turn, provider="openai-codex", reason="content_policy_blocked")
    _start(wired, "버그 고쳐줘 def f(): return 1/0", turn="c")
    assert _req(wired, turn_id="c")["request"]["model"] == "gpt-mini"


def test_success_resets_streak(wired):
    _start(wired, "번역해줘: hi")
    _req(wired)
    wired.hooks["post_llm_call"][0](session_id="s1", turn_id="t1")
    health = wired.state.data["health"]["openai-codex/gpt-mini"]
    assert health["ok"] == 1 and health["streak"] == 0


def test_soft_refusal_reanswered_on_other_allowed_model(wired):
    wired.llm.text = "여기 답변입니다."
    _start(wired, "번역해줘: hi")
    _req(wired)
    out = wired.hooks["transform_llm_output"][0](response_text="죄송하지만 도와드릴 수 없습니다.",
                                                 session_id="s1", model="gpt-mini")
    assert out.startswith("여기 답변입니다.") and "model-router" in out
    kind, messages, kw = wired.llm.calls[-1]
    assert kw["model"] != "gpt-mini" and messages[-1] == {"role": "user", "content": "번역해줘: hi"}
    # the next turn of this chat starts away from the model that refused
    _start(wired, "번역해줘: again", turn="t2")
    assert _req(wired, turn_id="t2")["request"]["model"] != "gpt-mini"


def test_soft_refusal_off(wired):
    wired.settings["soft_refusal_retry"] = False
    _start(wired, "번역해줘: hi")
    assert wired.hooks["transform_llm_output"][0](response_text="I can't help with that.", session_id="s1") is None


def test_off_mode_and_empty_scope_do_nothing(make_ctx):
    ctx = make_ctx({})
    plugin.register(ctx)
    _start(ctx, "번역해줘: hi")
    assert _req(ctx) is None
    ctx2 = make_ctx({"scope": {"openai-codex": ["gpt-mini"]}, "mode": "off"})
    plugin.register(ctx2)
    _start(ctx2, "번역해줘: hi")
    assert _req(ctx2) is None


def test_extra_guardrail_phrase_forces_fallback(wired):
    cb = wired.hooks["transform_api_error_classification"][0]
    assert cb(error_message="blocked by corp-guard") is None
    wired.settings["extra_guardrail_patterns"] = ["corp-guard"]
    assert cb(error_message="Blocked by CORP-GUARD")["should_fallback"] is True


def test_slash_pin_and_scope_enforcement(wired):
    route = wired.commands["route"]
    assert "not in the allowlist" in route("pin openai-codex/gpt-9", session_id="s1")
    assert "now uses" in route("pin anthropic/claude-opus-4-8@low", session_id="s1")
    _start(wired, "번역해줘: hi")
    out = _req(wired, provider="anthropic", api_mode="anthropic_messages",
               request={"model": "x", "output_config": {"effort": "high"}})
    assert out["request"]["model"] == "claude-opus-4-8" and out["request"]["output_config"]["effort"] == "low"
    # pinned to anthropic: an openai-codex request has no allowed candidate, so it's left alone
    assert _req(wired) is None
    assert "Unpinned" in route("unpin", session_id="s1")


def test_slash_status_prefer_stats_reset(wired):
    route = wired.commands["route"]
    status = route("", session_id="s1")
    assert "openai-codex: gpt-6.2-sol, gpt-mini" in status and "deep" in status
    assert "now start on" in route("prefer quick openai-codex/gpt-6.2-sol", session_id="s1")
    _start(wired, "번역해줘: hi")
    assert _req(wired)["request"]["model"] == "gpt-6.2-sol"
    assert "No outcomes" in route("stats")
    assert "Forgot" in route("reset")
    assert route("off") and wired.settings["mode"] == "off"
    assert route("bogus").startswith("Usage")


def test_preference_outside_allowlist_is_ignored(wired):
    wired.state.data["preferred"] = {"quick": "evil/model@high"}
    plugin.register(wired)  # fresh router reads state
    _start(wired, "번역해줘: hi")
    assert _req(wired)["request"]["model"] == "gpt-mini"


def test_cross_provider_uses_private_switch(wired, monkeypatch):
    from model_router_plugin import private_switch

    class Agent:
        provider, model, reasoning_config = "openai-codex", "gpt-6.2-sol", None
    agent = Agent()
    switched = []
    monkeypatch.setattr(private_switch, "live_agent", lambda: agent)

    def fake_switch(a, cand):
        switched.append(cand)
        a.provider, a.model = cand.provider, cand.model
    monkeypatch.setattr(private_switch, "switch_to", fake_switch)
    wired.settings["cross_provider"] = True
    _start(wired, "결제 시스템 아키텍처를 설계하고 트레이드오프를 비교 분석해줘")
    assert switched == [Candidate("anthropic", "claude-opus-4-8", "high")]
    assert agent.reasoning_config == {"enabled": True, "effort": "high"}
    # turning routing off moves the session back where it was
    wired.settings["mode"] = "off"
    _start(wired, "anything", turn="t2")
    assert switched[-1] == Candidate("openai-codex", "gpt-6.2-sol")


def test_cross_provider_failure_keeps_public_routing(wired, monkeypatch):
    from model_router_plugin import private_switch
    monkeypatch.setattr(private_switch, "live_agent", lambda: None)
    wired.settings["cross_provider"] = True
    _start(wired, "결제 시스템 아키텍처를 설계하고 트레이드오프를 비교 분석해줘")
    assert _req(wired)["request"]["model"] == "gpt-6.2-sol"


def test_explore_without_scope(make_ctx):
    ctx = make_ctx({})
    plugin.register(ctx)
    out = json.loads(ctx.tools["route_explore"][1]({"task": "x"}))
    assert out["success"] is False and "setup" in out["error"]
