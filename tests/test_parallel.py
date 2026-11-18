"""P5: a tier's models run in parallel; two or more good answers are combined by one more call."""

import contextvars
import threading
import time
import types

import model_router_plugin.ladder as ladder_mod
from model_router_plugin.attempts import AttemptLog
from model_router_plugin.ladder import LadderRunner
from model_router_plugin.outcome import TurnSignals
from model_router_plugin.settings import load_settings
from tests.conftest import FakeCtx

REFUSAL = "I'm sorry, but I can't help with that."
TURN_VAR = contextvars.ContextVar("turn_var", default=None)


class Llm:
    """Per-model replies; a synthesis request (it contains 'Answer 1') gets ``synth``."""

    def __init__(self, replies, synth="COMBINED: def parse(s): return s.split()", delays=None):
        self.replies, self.synth, self.delays = replies, synth, delays or {}
        self.calls, self.seen_var, self.lock = [], [], threading.Lock()

    def complete(self, messages, *, provider=None, model=None, timeout=None, purpose=None, **kw):
        is_synth = "Answer 1 (" in messages[-1]["content"]
        with self.lock:
            self.calls.append(("synth" if is_synth else "ask", model, time.monotonic()))
            self.seen_var.append(TURN_VAR.get())
        time.sleep(self.delays.get(model, 0.0) if not is_synth else 0.0)
        text = self.synth if is_synth else self.replies.get(model, "")
        if isinstance(text, Exception):
            raise text
        return types.SimpleNamespace(text=text, usage=None)


def setup(tmp_path, llm, tiers, timeout=120.0, **cfg):
    ctx = FakeCtx()
    ctx.llm = llm
    signals = TurnSignals()
    log = AttemptLog(tmp_path / "data" / "attempts.jsonl")
    r = LadderRunner(ctx, signals, log, timeout_fn=lambda: timeout)
    r.note_turn("t1", "s1", "Write a parser for this format.", [])
    signals.record_success("t1", "openai-codex", "gpt-6.1-sol-900k")
    s = load_settings(lambda key, default=None: {"tiers": tiers, **cfg}.get(key, default))
    return r, log, s


def handle(r, s):
    return r.handle(REFUSAL, turn_id="t1", session_id="s1", settings=s)


TIER = [["anthropic/claude-opus-4-8", "xai/grok-5"]]


def test_two_good_answers_are_combined_on_the_first_models_side(tmp_path):
    llm = Llm({"claude-opus-4-8": "A: def parse(s): ...", "grok-5": "B: def parse(s): ..."})
    r, log, s = setup(tmp_path, llm, TIER)
    out = handle(r, s)
    assert out.startswith("COMBINED")
    assert "answers from anthropic/claude-opus-4-8 and xai/grok-5 were combined by anthropic/claude-opus-4-8" in out
    synth_call = next(c for c in llm.calls if c[0] == "synth")
    assert synth_call[1] == "claude-opus-4-8"
    records = log.recent()
    assert [(a.kind, a.model, a.outcome, a.delivered, a.won_synthesis) for a in records] == [
        ("origin", "gpt-6.1-sol-900k", "blocked", False, False),
        ("plain", "claude-opus-4-8", "ok", True, True),
        ("plain", "grok-5", "ok", True, True),
        ("synthesis", "claude-opus-4-8", "ok", True, False)]
    assert "Answer 1 (anthropic/claude-opus-4-8)" in records[-1].prompt[0]["content"]
    assert "do not mention that there were several answers" in records[-1].prompt[0]["content"]


def test_failed_synthesis_falls_back_to_the_first_good_answer_in_tier_order(tmp_path):
    llm = Llm({"claude-opus-4-8": "A: def parse(s): ...", "grok-5": "B: def parse(s): ..."}, synth=REFUSAL)
    r, log, s = setup(tmp_path, llm, TIER)
    out = handle(r, s)
    assert out.startswith("A: def parse") and "anthropic/claude-opus-4-8 answered instead" in out
    records = log.recent()
    assert records[-1].kind == "synthesis" and records[-1].outcome == "blocked"
    assert [a.delivered for a in records] == [False, True, False, False]


def test_one_good_answer_needs_no_synthesis(tmp_path):
    llm = Llm({"claude-opus-4-8": REFUSAL, "grok-5": "B: def parse(s): ..."})
    r, log, s = setup(tmp_path, llm, TIER)
    assert handle(r, s).startswith("B: def parse")
    assert not any(c[0] == "synth" for c in llm.calls)


def test_synthesis_can_be_turned_off(tmp_path):
    llm = Llm({"claude-opus-4-8": "A: def parse(s): ...", "grok-5": "B: def parse(s): ..."})
    r, _, s = setup(tmp_path, llm, TIER, synthesize=False)
    assert handle(r, s).startswith("A: def parse")
    assert not any(c[0] == "synth" for c in llm.calls)


def test_models_in_a_tier_really_run_at_the_same_time(tmp_path):
    llm = Llm({"claude-opus-4-8": "A: def parse(s): ...", "grok-5": "B: def parse(s): ..."},
              delays={"claude-opus-4-8": 0.4, "grok-5": 0.4})
    r, _, s = setup(tmp_path, llm, TIER, synthesize=False)
    started = time.monotonic()
    handle(r, s)
    assert time.monotonic() - started < 0.7  # sequential would take 0.8s


def test_sequential_mode_stops_at_the_first_good_answer(tmp_path):
    llm = Llm({"claude-opus-4-8": "A: def parse(s): ...", "grok-5": "B: def parse(s): ..."})
    r, _, s = setup(tmp_path, llm, TIER, parallel=False)
    handle(r, s)
    assert [c[1] for c in llm.calls] == ["claude-opus-4-8"]


def test_a_slow_model_is_abandoned_and_the_fast_one_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(ladder_mod, "MARGIN_SECONDS", 0.0)
    monkeypatch.setattr(ladder_mod, "MIN_CALL_SECONDS", 0.05)
    llm = Llm({"claude-opus-4-8": "A: def parse(s): ...", "grok-5": "late"}, delays={"grok-5": 3.0})
    r, log, s = setup(tmp_path, llm, TIER, timeout=1.0)
    started = time.monotonic()
    out = handle(r, s)
    assert time.monotonic() - started < 1.5
    assert out.startswith("A: def parse")
    slow = next(a for a in log.recent() if a.model == "grok-5")
    assert slow.outcome == "error" and "time budget" in slow.reason


def test_the_turns_context_reaches_every_parallel_call(tmp_path):
    llm = Llm({"claude-opus-4-8": "A: def parse(s): ...", "grok-5": "B: def parse(s): ..."})
    r, _, s = setup(tmp_path, llm, TIER)
    token = TURN_VAR.set("turn-ctx")
    try:
        handle(r, s)
    finally:
        TURN_VAR.reset(token)
    assert llm.seen_var and all(v == "turn-ctx" for v in llm.seen_var)


def test_a_worker_crash_becomes_an_error_attempt(tmp_path):
    llm = Llm({"claude-opus-4-8": RuntimeError("boom"), "grok-5": "B: def parse(s): ..."})
    r, log, s = setup(tmp_path, llm, TIER)
    assert handle(r, s).startswith("B: def parse")
    crashed = next(a for a in log.recent() if a.model == "claude-opus-4-8")
    assert crashed.outcome == "error" and "boom" in crashed.reason
