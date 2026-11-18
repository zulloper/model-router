"""P6b: models that keep winning move up the ladder; the user's tiers are the floor."""

import types

import model_router_plugin as plugin
from model_router_plugin import promote
from model_router_plugin.attempts import ENRICH, ORIGIN, PLAIN, Attempt, AttemptLog
from model_router_plugin.ladder import LadderRunner
from model_router_plugin.outcome import TurnSignals
from model_router_plugin.settings import load_settings
from model_router_plugin.tiers import parse_tiers
from tests.conftest import FakeCtx, FakeState

A, B, C = "openai-codex/gpt-6.2-sol", "anthropic/claude-opus-4-8", "xai/grok-5"
LADDER = parse_tiers([[A], [B], [C]])


def tries(key, ok, fail, kind=PLAIN, reason=""):
    provider, model = key.split("/", 1)
    outcomes = ["ok"] * ok + ["blocked"] * fail
    return [Attempt(turn_id="t", session_id="s", kind=kind, tier=0, index=i, provider=provider, model=model,
                    outcome=o, reason=reason if o != "ok" else "") for i, o in enumerate(outcomes)]


def keys(ladder):
    return [[c.key for c in tier] for tier in ladder.tiers]


# --- evidence ---------------------------------------------------------------------------------

def test_records_use_plain_attempts_in_a_recent_window():
    log = tries(B, 0, 30) + tries(B, 20, 0) + tries(B, 9, 0, kind=ENRICH) + tries(B, 5, 0, kind=ORIGIN)
    rec = promote.records(log)[B]
    assert rec.samples == promote.WINDOW and rec.ok == 20  # the old failures fell out of the window


def test_calls_abandoned_at_the_time_budget_do_not_count():
    log = tries(B, 6, 0) + tries(B, 0, 10, reason="time budget reached; call abandoned")
    assert promote.records(log)[B].samples == 6


# --- effective ladder -------------------------------------------------------------------------

def test_promotion_moves_a_model_up_and_keeps_tier_numbers():
    assert keys(promote.effective(LADDER, {C: 1})) == [[A], [B, C], []]
    assert keys(promote.effective(LADDER, {C: 5})) == [[A, C], [B], []]   # never above tier 1
    assert keys(promote.effective(LADDER, {"gone/model": 1})) == keys(LADDER)


# --- rules ------------------------------------------------------------------------------------

def test_promote_when_clearly_better_than_the_tier_above():
    attempts = tries(A, 4, 6) + tries(B, 9, 1)           # A 40%, B 90% > max(60%, 40%+15%)
    new, changes = promote.evaluate(LADDER, {}, attempts)
    assert new == {B: 1} and "↑ tier 2 → 1" in changes[0]


def test_no_promotion_when_only_slightly_better():
    attempts = tries(A, 7, 3) + tries(B, 8, 2)           # 70% vs 80%: not 15 points better
    assert promote.evaluate(LADDER, {}, attempts) == ({}, [])


def test_not_enough_samples_means_no_move():
    attempts = tries(A, 0, 10) + tries(B, 7, 0)           # B perfect but only 7 samples
    assert promote.evaluate(LADDER, {}, attempts) == ({}, [])


def test_with_no_judged_model_above_the_bar_is_higher():
    assert promote.evaluate(LADDER, {}, tries(B, 7, 3))[0] == {}         # 70% < 75%
    assert promote.evaluate(LADDER, {}, tries(B, 8, 2))[0] == {B: 1}     # 80%


def test_one_step_per_evaluation():
    attempts = tries(C, 10, 0)
    new, _ = promote.evaluate(LADDER, {}, attempts)
    assert new == {C: 1}
    new, _ = promote.evaluate(LADDER, new, attempts)
    assert new == {C: 2}


def test_demotion_only_undoes_a_promotion():
    attempts = tries(B, 2, 8)                              # 20%
    assert promote.evaluate(LADDER, {B: 1}, attempts)[0] == {}
    assert promote.evaluate(LADDER, {}, attempts) == ({}, [])          # floor: never below configured tier
    assert promote.evaluate(LADDER, {}, tries(A, 0, 10)) == ({}, [])   # tier 1 is never demoted


def test_promotions_for_models_no_longer_on_the_ladder_are_dropped():
    assert promote.evaluate(LADDER, {"gone/model": 2}, []) == ({}, [])


def test_store_survives_bad_state():
    class Broken:
        def get(self, *a, **k):
            raise OSError("disk")

        def set(self, *a, **k):
            raise OSError("disk")
    store = promote.Promotions(Broken())
    assert store.get() == {}
    store.set({B: 1})  # does not raise


# --- through the ladder and /route ------------------------------------------------------------

REFUSAL = "I'm sorry, but I can't help with that."


def test_ladder_uses_and_updates_promotions(tmp_path):
    ctx = FakeCtx()
    ctx.state = FakeState()
    log = AttemptLog(tmp_path / "data" / "attempts.jsonl")
    for a in tries(A, 2, 8) + tries(B, 9, 1):
        log.append(a)
    asked = []

    def complete(messages, *, provider=None, model=None, **kw):
        asked.append(model)
        return types.SimpleNamespace(text="def parse(s): ..." if model == "claude-opus-4-8" else REFUSAL, usage=None)
    ctx.llm = types.SimpleNamespace(complete=complete)
    signals = TurnSignals()
    r = LadderRunner(ctx, signals, log, timeout_fn=lambda: 120.0, evaluate_async=False)
    settings = load_settings(lambda key, default=None: {"tiers": [[A], [B], [C]]}.get(key, default))

    r.note_turn("t1", "s1", "Write a parser.", [])
    signals.record_success("t1", "x", "origin")
    r.handle(REFUSAL, turn_id="t1", session_id="s1", settings=settings)
    assert ctx.state.data["promotions"] == {B: 1}        # learned from the log after this turn
    assert asked == ["gpt-6.2-sol", "claude-opus-4-8"]

    asked.clear()
    r.note_turn("t2", "s1", "Write a parser.", [])
    signals.record_success("t2", "x", "origin")
    out = r.handle(REFUSAL, turn_id="t2", session_id="s1", settings=settings)
    assert "tier 1" in out                               # B now answers from tier 1


def test_auto_promote_off_ignores_stored_promotions(tmp_path):
    ctx = FakeCtx()
    ctx.state = FakeState()
    ctx.state.set("promotions", {B: 1})
    asked = []

    def complete(messages, *, provider=None, model=None, **kw):
        asked.append(model)
        return types.SimpleNamespace(text=REFUSAL, usage=None)
    ctx.llm = types.SimpleNamespace(complete=complete)
    signals = TurnSignals()
    r = LadderRunner(ctx, signals, None, timeout_fn=lambda: 120.0, evaluate_async=False)
    settings = load_settings(lambda key, default=None: {"tiers": [[A], [B]], "auto_promote": False}.get(key, default))
    r.note_turn("t1", "s1", "Write a parser.", [])
    signals.record_success("t1", "x", "origin")
    r.handle(REFUSAL, turn_id="t1", session_id="s1", settings=settings)
    assert asked == ["gpt-6.2-sol", "claude-opus-4-8"]   # configured order, one tier at a time


def test_route_status_shows_the_ladder_and_reset_clears_promotions():
    ctx = FakeCtx({"tiers": [[A], [B]]})
    plugin.register(ctx)
    ctx.state.set("promotions", {B: 1})
    status = ctx.commands["route"]("")
    assert "Tier ladder (auto-promote on):" in status
    assert f"tier 1: {A}, {B} ↑1" in status and "tier 2: (empty" in status
    ctx.commands["route"]("reset")
    assert ctx.state.data["promotions"] == {}
