"""The ladder (P2+P6): a blocked reply climbs the tiers; the first usable answer replaces it."""

import threading
import time
import types

import pytest

import model_router_plugin as plugin
import model_router_plugin.ladder as ladder_mod
from model_router_plugin.attempts import AttemptLog
from model_router_plugin.ladder import LadderRunner, ladder_budget
from model_router_plugin.outcome import TurnSignals
from model_router_plugin.router import load_settings
from tests.conftest import FakeCtx

REFUSAL = "I'm sorry, but I can't help with that."
TIERS = [["anthropic/claude-opus-4-8"], ["openai-codex/gpt-6.2-sol", "xai/grok-5"]]


class ModelLlm:
    """ctx.llm stand-in: a reply (or an exception, or a delay) per model."""

    def __init__(self, replies, delay=0.0):
        self.replies, self.delay, self.calls = replies, delay, []

    def complete(self, messages, *, provider=None, model=None, timeout=None, purpose=None, **kw):
        self.calls.append((provider, model, messages, timeout))
        if self.delay:
            time.sleep(self.delay)
        reply = self.replies.get(model, "")
        if isinstance(reply, Exception):
            raise reply
        usage = types.SimpleNamespace(input_tokens=11, output_tokens=7)
        return types.SimpleNamespace(text=reply, provider=provider, model=model, usage=usage)


def settings(**extra):
    cfg = {"tiers": TIERS, **extra}
    return load_settings(lambda key, default=None: cfg.get(key, default))


def runner(tmp_path, replies, timeout=120.0, delay=0.0):
    ctx = FakeCtx()
    ctx.llm = ModelLlm(replies, delay)
    signals = TurnSignals()
    log = AttemptLog(tmp_path / "data" / "attempts.jsonl")
    r = LadderRunner(ctx, signals, log, timeout_fn=lambda: timeout)
    r.note_turn("t1", "s1", "Write a parser for this format.", [
        {"role": "user", "content": "earlier question"}, {"role": "assistant", "content": "earlier answer"}])
    signals.record_success("t1", "openai-codex", "gpt-6.1-sol")
    return r, ctx, signals, log


def run(r, text="", s=None):
    return r.handle(text, turn_id="t1", session_id="s1", settings=s or settings())


def test_a_usable_reply_is_left_alone(tmp_path):
    r, ctx, _, log = runner(tmp_path, {})
    assert run(r, "Here is the parser:\n```python\ndef parse(s): ...\n```") is None
    assert ctx.llm.calls == [] and log.recent() == []


def test_soft_refusal_is_answered_by_tier_one(tmp_path):
    r, ctx, _, log = runner(tmp_path, {"claude-opus-4-8": "Here you go: def parse(s): return s.split()"})
    out = run(r, REFUSAL)
    assert out.startswith("Here you go")
    assert "openai-codex/gpt-6.1-sol was blocked" in out and "anthropic/claude-opus-4-8 answered instead (tier 1" in out
    provider, model, messages, _ = ctx.llm.calls[0]
    assert (provider, model) == ("anthropic", "claude-opus-4-8")
    assert [m["content"] for m in messages] == ["earlier question", "earlier answer", "Write a parser for this format."]
    records = log.recent()
    assert [(a.kind, a.outcome, a.delivered) for a in records] == [("origin", "blocked", False), ("plain", "ok", True)]
    assert records[1].tokens_in == 11 and records[1].prompt_hash and records[1].tier == 0


def test_climbs_to_the_next_tier_when_a_whole_tier_fails(tmp_path):
    r, ctx, _, log = runner(tmp_path, {"claude-opus-4-8": REFUSAL, "gpt-6.2-sol": "",
                                       "grok-5": "def parse(s): return s.split()"})
    out = run(r, REFUSAL)
    assert "xai/grok-5 answered instead (tier 2" in out
    called = [m for _, m, _, _ in ctx.llm.calls]
    assert called[0] == "claude-opus-4-8" and sorted(called[1:]) == ["gpt-6.2-sol", "grok-5"]  # tier 2 in parallel
    assert [(a.model, a.outcome, a.delivered) for a in log.recent()] == [
        ("gpt-6.1-sol", "blocked", False), ("claude-opus-4-8", "blocked", False),
        ("gpt-6.2-sol", "error", False), ("grok-5", "ok", True)]


def test_everything_fails_keeps_the_original_and_logs_the_attempts(tmp_path):
    r, _, _, log = runner(tmp_path, {"claude-opus-4-8": REFUSAL, "gpt-6.2-sol": REFUSAL,
                                     "grok-5": RuntimeError("provider down")})
    assert run(r, REFUSAL) is None
    records = log.recent()
    assert len(records) == 4 and not any(a.delivered for a in records)
    assert records[-1].outcome == "error" and "provider down" in records[-1].reason


def test_turn_that_ended_on_a_policy_error_climbs(tmp_path):
    r, _, signals, log = runner(tmp_path, {"claude-opus-4-8": "def parse(s): return s.split()"})
    signals.record_error("t1", reason="content_policy_blocked", provider="openai-codex", model="gpt-6.1-sol")
    out = run(r, "ChatGPT or Codex Subscription rejected the request and retrying won't help.")
    assert out.startswith("def parse")
    assert log.recent()[0].reason == "provider: content_policy_blocked"


def test_the_model_that_just_failed_is_not_asked_again(tmp_path):
    r, ctx, signals, _ = runner(tmp_path, {"gpt-6.2-sol": REFUSAL, "grok-5": "ok: def parse(s): ..."})
    signals.record_success("t1", "anthropic", "claude-opus-4-8")  # origin is the tier-1 model
    run(r, REFUSAL)
    assert sorted(m for _, m, _, _ in ctx.llm.calls) == ["gpt-6.2-sol", "grok-5"]


def test_no_call_starts_without_enough_time_left(tmp_path):
    r, ctx, _, log = runner(tmp_path, {"claude-opus-4-8": "fine"}, timeout=8.0)  # budget 4s < 5s minimum
    assert run(r, REFUSAL) is None
    assert ctx.llm.calls == []
    assert [a.kind for a in log.recent()] == ["origin"]


def test_a_hanging_call_is_abandoned_at_the_deadline(tmp_path, monkeypatch):
    monkeypatch.setattr(ladder_mod, "MARGIN_SECONDS", 0.0)
    monkeypatch.setattr(ladder_mod, "MIN_CALL_SECONDS", 0.05)
    r, _, _, log = runner(tmp_path, {"claude-opus-4-8": "late"}, timeout=0.3, delay=2.0)
    started = time.monotonic()
    assert run(r, REFUSAL) is None
    assert time.monotonic() - started < 1.0
    assert "time budget" in log.recent()[1].reason


def test_retry_budget_setting_caps_the_ladder(tmp_path):
    assert ladder_budget(300, "auto") == 296
    assert ladder_budget(300, 60) == 60
    assert ladder_budget(30, 600) == 26          # never past the hook timeout
    assert ladder_budget(0, "auto") == ladder_mod.NO_TIMEOUT_BUDGET
    assert ladder_budget(3, "auto") == 0


def test_never_raises(tmp_path):
    r, _, _, _ = runner(tmp_path, {})
    broken = types.SimpleNamespace(ladder=None, extra_guardrail_patterns=(), retry_budget_seconds="auto")
    assert r.handle(REFUSAL, turn_id="t1", session_id="s1", settings=broken) is None


def test_unknown_turn_is_left_alone(tmp_path):
    r, ctx, _, _ = runner(tmp_path, {"claude-opus-4-8": "fine"})
    assert r.handle(REFUSAL, turn_id="other", session_id="s1", settings=settings()) is None
    assert ctx.llm.calls == []


# --- through the registered hooks -------------------------------------------------------------

def test_hooks_route_a_blocked_turn_through_the_ladder(tmp_path):
    ctx = FakeCtx({"tiers": TIERS})
    ctx.llm = ModelLlm({"claude-opus-4-8": "def parse(s): return s.split()"})
    plugin.register(ctx)
    ctx.hooks["pre_llm_call"][0](session_id="s1", user_message="Write a parser.", conversation_history=[],
                                 turn_id="t1")
    ctx.hooks["post_api_request"][0](turn_id="t1", provider="openai-codex", model="gpt-6.1-sol")
    out = ctx.hooks["transform_llm_output"][0](response_text=REFUSAL, session_id="s1", turn_id="t1",
                                               model="gpt-6.1-sol")
    assert out.startswith("def parse") and "tier 1" in out
    assert ctx.hooks["transform_llm_output"][1] is True  # held: tiers configured


def test_without_tiers_the_ladder_stays_out_of_the_way(tmp_path):
    ctx = FakeCtx({})
    ctx.llm = ModelLlm({"claude-opus-4-8": "x"})
    plugin.register(ctx)
    ctx.hooks["pre_llm_call"][0](session_id="s1", user_message="Write a parser.", conversation_history=[],
                                 turn_id="t1")
    assert ctx.hooks["transform_llm_output"][0](response_text=REFUSAL, session_id="s1", turn_id="t1") is None
    assert ctx.llm.calls == []
