"""Climb the tier ladder for a blocked reply (0.4.0, DESIGN.md §3; P2+P6).

When the profile model's reply is blocked (see :mod:`outcome`), the ladder's models are asked the
same request — tier 1 first, then tier 2, … — through ``ctx.llm.complete`` (no tools, the recent
conversation as context). The first usable answer replaces the blocked reply, with a footer that
says so. Every attempt is written to the attempt log (§6.1).

Per tier the request is tried as written, then enriched (P4, :mod:`enrich`) if that adds anything.
A tier's models run in parallel; two or more good answers are combined by one more call (P5).

Two invariants hold here because ``transform_llm_output`` runs with ``hold_stream`` (DESIGN §6):

* (4) **never hit the hook timeout.** The ladder runs against its own deadline, a few seconds inside
  ``plugins.hook_callback_timeout``. A call that would overrun it is not started, and a call that
  hangs is abandoned at the deadline (it runs on its own thread), so the hook always returns in time.
* (5) **never raise.** Any failure returns None, which keeps the reply the user would have seen.
"""

from __future__ import annotations

import contextvars
import dataclasses
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Optional

from . import enrich, promote
from .attempts import ENRICH, ORIGIN, PLAIN, SYNTH, Attempt, AttemptLog, prompt_hash
from .outcome import BLOCKED, DEGRADED, OK, TurnSignals, Verdict, judge
from .scope import Candidate

logger = logging.getLogger(__name__)

HOOK_TIMEOUT_DEFAULT = 30.0     # Hermes' plugins.hook_callback_timeout default
HOOK_TIMEOUT_MAX = 600.0        # Hermes clamps the setting to this
MARGIN_SECONDS = 4.0            # finish this far inside the hook timeout
MIN_CALL_SECONDS = 5.0          # don't start a model call with less time than this left
NO_TIMEOUT_BUDGET = 600.0       # hook_callback_timeout <= 0 disables Hermes' cap; still bound ourselves
ROUND_SHARE = 0.7               # a parallel round that may be synthesized gets this share of the time left
JOIN_GRACE_SECONDS = 0.5        # a round's threads end at its deadline; allow this for them to return
SYNTH_ANSWER_CHARS = 12000      # per answer handed to the synthesizer
_MAX_TURNS = 256
_CONTEXT_MESSAGES = 6
_CONTEXT_CHARS = 4000

_FOOTER = "\n\n— model-router: {origin} {what} ({why}); {by} answered instead (tier {tier}, {how})."
_SYNTH_FOOTER = ("\n\n— model-router: {origin} {what} ({why}); answers from {by} were combined by {synth} "
                 "(tier {tier}, {how}).")
_WHAT = {BLOCKED: "was blocked", DEGRADED: "answered without the requested code"}
_SYNTH_ASK = ("Several answers to the same request follow. Combine them into one answer that is better than "
              "each: keep everything correct and useful from each, resolve disagreements (say which is right "
              "and why when it matters), and drop repetition. Do not add claims that none of them supports. "
              "Keep code complete. Reply with the combined answer only.")


def hook_timeout() -> float:
    """``plugins.hook_callback_timeout`` as Hermes applies it (0 = no cap)."""
    try:
        from hermes_cli.config import load_config
        raw = ((load_config() or {}).get("plugins") or {}).get("hook_callback_timeout")
        value = HOOK_TIMEOUT_DEFAULT if raw is None else float(raw)
    except Exception:
        value = HOOK_TIMEOUT_DEFAULT
    if value < 0:
        return HOOK_TIMEOUT_DEFAULT
    return min(value, HOOK_TIMEOUT_MAX)


def ladder_budget(hook_limit: float, setting: Any = "auto") -> float:
    """Seconds the ladder may spend: inside the hook timeout, and inside ``retry_budget_seconds``
    when that is a number."""
    ceiling = NO_TIMEOUT_BUDGET if hook_limit <= 0 else hook_limit - MARGIN_SECONDS
    try:
        wanted = float(setting)
    except (TypeError, ValueError):
        wanted = 0.0
    budget = min(wanted, ceiling) if wanted > 0 else ceiling
    return max(0.0, budget)


@dataclasses.dataclass
class _Turn:
    session_id: str
    user_message: str
    context: list[dict[str, str]]


def recent_context(history: Any, limit: int = _CONTEXT_MESSAGES) -> list[dict[str, str]]:
    out = []
    for msg in (history or [])[-limit:]:
        if isinstance(msg, dict) and msg.get("role") in ("user", "assistant"):
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                out.append({"role": msg["role"], "content": content[:_CONTEXT_CHARS]})
    return out


def _call_before(deadline: float, clock: Callable[[], float], fn: Callable[[], Any]) -> tuple[Any, Optional[str]]:
    """Run ``fn`` on its own thread and wait until ``deadline`` at most. -> (result, error). A call
    still running then is abandoned: its result is ignored and the hook returns in time."""
    box: dict[str, Any] = {}
    done = threading.Event()
    run_in_context = contextvars.copy_context().run  # ctx.llm may rely on the turn's context vars

    def worker() -> None:
        try:
            box["value"] = run_in_context(fn)
        except BaseException as exc:  # reported to the caller, never raised past the hook
            box["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            done.set()

    threading.Thread(target=worker, name="model-router-ladder", daemon=True).start()
    if not done.wait(timeout=max(0.0, deadline - clock())):
        return None, "time budget reached; call abandoned"
    return box.get("value"), box.get("error")


class LadderRunner:
    def __init__(self, ctx: Any, signals: TurnSignals, log: Optional[AttemptLog] = None,
                 clock: Callable[[], float] = time.monotonic,
                 timeout_fn: Callable[[], float] = hook_timeout,
                 promotions: Optional[promote.Promotions] = None, evaluate_async: bool = True) -> None:
        self.ctx, self.signals, self.log = ctx, signals, log
        self._clock, self._timeout_fn = clock, timeout_fn
        self.promotions = promotions or promote.Promotions(getattr(ctx, "state", None))
        self._evaluate_async = evaluate_async
        self._lock = threading.Lock()
        self._turns: "OrderedDict[str, _Turn]" = OrderedDict()

    # --- turn context (pre_llm_call) --------------------------------------------------------
    def note_turn(self, turn_id: str, session_id: str, user_message: str, history: Any) -> None:
        if not turn_id:
            return
        with self._lock:
            self._turns[turn_id] = _Turn(session_id, user_message or "", recent_context(history))
            self._turns.move_to_end(turn_id)
            while len(self._turns) > _MAX_TURNS:
                self._turns.popitem(last=False)

    def _turn(self, turn_id: str) -> Optional[_Turn]:
        with self._lock:
            return self._turns.get(turn_id)

    # --- transform_llm_output -----------------------------------------------------------------
    def handle(self, response_text: str, *, turn_id: str, session_id: str, settings: Any,
               model_hint: str = "") -> Optional[str]:
        """The replacement reply, or None to keep ``response_text``. Never raises (invariant 5)."""
        try:
            return self._handle(response_text, turn_id, session_id, settings, model_hint)
        except Exception:
            logger.exception("model-router: ladder failed; keeping the original reply")
            return None

    def _handle(self, response_text: str, turn_id: str, session_id: str, settings: Any,
                model_hint: str) -> Optional[str]:
        started = self._clock()
        deadline = started + ladder_budget(self._timeout_fn(), getattr(settings, "retry_budget_seconds", "auto"))
        turn = self._turn(turn_id)
        if turn is None or not turn.user_message.strip():
            return None
        failure = self.signals.ended_on_error(turn_id)
        verdict = judge(response_text, turn.user_message, failure=failure,
                        guardrail_patterns=tuple(getattr(settings, "extra_guardrail_patterns", ()) or ()))
        if not verdict.climb:
            return None
        provider, model = self.signals.route(turn_id)
        origin = Candidate(provider or "?", model or model_hint or "?")
        records = [Attempt(turn_id=turn_id, session_id=session_id or turn.session_id, kind=ORIGIN, tier=None,
                           index=0, provider=origin.provider, model=origin.model, outcome=verdict.outcome,
                           reason=verdict.reason)]
        messages = list(turn.context)
        if not messages or messages[-1].get("content") != turn.user_message:
            messages.append({"role": "user", "content": turn.user_message})

        enriched: Optional[enrich.Enriched] = None
        if getattr(settings, "enrich_on_retry", True):
            enriched = enrich.build(turn.context, turn.user_message, roe=getattr(settings, "security_roe", ""))
            if not enrich.worth_it(enriched):
                enriched = None  # it would only repeat the plain round

        # Per tier: the request as written, then (if it adds anything) the enriched request.
        # The plain round skips the model that just failed (same question again); the enriched
        # round may ask it, since the request is no longer the same.
        auto_promote = getattr(settings, "auto_promote", True)
        ladder = promote.effective(settings.ladder, self.promotions.get()) if auto_promote else settings.ladder
        chosen: Optional[_Chosen] = None
        for tier_index, tier in enumerate(ladder.tiers):
            for kind in (PLAIN, ENRICH):
                if kind == ENRICH and enriched is None:
                    continue
                cands = [c for c in tier if not (kind == PLAIN and c.key == origin.key)]
                if not cands or deadline - self._clock() < MIN_CALL_SECONDS:
                    continue
                tries = self._round(cands, kind, messages, enriched, turn_id, turn, tier_index,
                                    len(records), deadline, settings)
                records.extend(t.attempt for t in tries)
                good = [t for t in tries if t.attempt.outcome == OK]
                if good:
                    chosen = self._choose(good, kind, enriched, turn_id, turn, tier_index, records,
                                          deadline, settings)
                    break
            if chosen or deadline - self._clock() < MIN_CALL_SECONDS:
                break

        self._write(records)
        if auto_promote:
            self._evaluate(settings.ladder)
        if not chosen:
            return None
        how = "enriched request, without tools" if chosen.kind == ENRICH else "without tools"
        what, why = _WHAT.get(verdict.outcome, "failed"), verdict.reason[:80]
        if len(chosen.sources) > 1:
            footer = _SYNTH_FOOTER.format(origin=origin.key, what=what, why=why, by=" and ".join(chosen.sources),
                                          synth=chosen.by, tier=chosen.tier + 1, how=how)
        else:
            footer = _FOOTER.format(origin=origin.key, what=what, why=why, by=chosen.by, tier=chosen.tier + 1,
                                    how=how)
        return chosen.text.rstrip() + footer

    # --- one round of a tier ------------------------------------------------------------------
    def _round(self, cands: list[Candidate], kind: str, messages: list, enriched: Optional["enrich.Enriched"],
               turn_id: str, turn: _Turn, tier_index: int, first_index: int, deadline: float,
               settings: Any) -> list["_Try"]:
        """Ask ``cands`` the round's request. In parallel (default) every model gets its own thread
        and the round ends when all answered or at its deadline; sequentially the first good answer
        ends it. A model that would combine answers afterwards needs time, so a parallel round that
        may be synthesized gets only part of what is left."""
        if not getattr(settings, "parallel", True) or len(cands) == 1:
            tries: list[_Try] = []
            for i, cand in enumerate(cands):
                if deadline - self._clock() < MIN_CALL_SECONDS:
                    break
                t = self._try(cand, kind, messages, enriched, turn_id, turn, tier_index, first_index + i, deadline)
                tries.append(t)
                if t.attempt.outcome == OK:
                    break
            return tries
        now = self._clock()
        round_deadline = deadline
        if getattr(settings, "synthesize", True):
            round_deadline = now + ROUND_SHARE * (deadline - now)
        slots: list[Optional[_Try]] = [None] * len(cands)
        threads = []
        for i, cand in enumerate(cands):
            run = contextvars.copy_context().run  # one context copy per thread: ctx.llm may read the turn's vars

            def work(i: int = i, cand: Candidate = cand, run: Callable = run) -> None:
                try:
                    slots[i] = run(self._try, cand, kind, messages, enriched, turn_id, turn, tier_index,
                                   first_index + i, round_deadline)
                except Exception as exc:  # _try reports failures itself; this is the last guard
                    slots[i] = self._failed(cand, kind, turn_id, turn, tier_index, first_index + i,
                                            f"{type(exc).__name__}: {exc}")
            thread = threading.Thread(target=work, name=f"model-router-tier-{tier_index + 1}", daemon=True)
            thread.start()
            threads.append(thread)
        for thread in threads:
            thread.join(timeout=max(0.0, round_deadline - self._clock()) + JOIN_GRACE_SECONDS)
        return [slot if slot is not None else
                self._failed(cands[i], kind, turn_id, turn, tier_index, first_index + i,
                             "time budget reached; call abandoned")
                for i, slot in enumerate(slots)]

    def _choose(self, good: list["_Try"], kind: str, enriched: Optional["enrich.Enriched"], turn_id: str,
                turn: _Turn, tier_index: int, records: list[Attempt], deadline: float,
                settings: Any) -> "_Chosen":
        """One good answer is delivered as is. Two or more are combined by one more call when
        synthesis is on and time allows; if that call fails the first good answer (tier order) wins."""
        first = good[0]
        if (len(good) > 1 and getattr(settings, "synthesize", True)
                and deadline - self._clock() >= MIN_CALL_SECONDS):
            synth = self._synthesize(good, kind, enriched, turn_id, turn, tier_index, len(records), deadline)
            records.append(synth.attempt)
            if synth.attempt.outcome == OK:
                synth.attempt.delivered = True
                for t in good:
                    t.attempt.delivered = t.attempt.won_synthesis = True
                return _Chosen(synth.text, kind, tier_index, synth.attempt.key, [t.attempt.key for t in good])
        first.attempt.delivered = True
        return _Chosen(first.text, kind, tier_index, first.attempt.key, [first.attempt.key])

    def _synthesize(self, good: list["_Try"], kind: str, enriched: Optional["enrich.Enriched"], turn_id: str,
                    turn: _Turn, tier_index: int, index: int, deadline: float) -> "_Try":
        """Combine the answers on the first good answer's model. For an enriched round it sees the
        enriched request and the answers as the models wrote them; its answer is restored after."""
        request = enriched.messages[-1]["content"] if kind == ENRICH and enriched is not None else turn.user_message
        parts = [_SYNTH_ASK, f"Request:\n{request}"]
        for n, t in enumerate(good, start=1):
            parts.append(f"Answer {n} ({t.attempt.key}):\n{t.raw[:SYNTH_ANSWER_CHARS]}")
        messages = [{"role": "user", "content": "\n\n".join(parts)}]
        return self._call(good[0].cand, SYNTH, messages, enriched if kind == ENRICH else None, turn_id, turn,
                          tier_index, index, deadline, purpose="model-router: combine tier answers")

    # --- one call -----------------------------------------------------------------------------
    def _try(self, cand: Candidate, kind: str, plain_messages: list, enriched: Optional["enrich.Enriched"],
             turn_id: str, turn: _Turn, tier_index: int, index: int, deadline: float) -> "_Try":
        messages = enriched.messages if kind == ENRICH and enriched is not None else plain_messages
        return self._call(cand, kind, messages, enriched if kind == ENRICH else None, turn_id, turn, tier_index,
                          index, deadline, purpose="model-router: tier retry")

    def _call(self, cand: Candidate, kind: str, messages: list, enriched: Optional["enrich.Enriched"],
              turn_id: str, turn: _Turn, tier_index: int, index: int, deadline: float, purpose: str) -> "_Try":
        llm = getattr(self.ctx, "llm", None)
        t0 = self._clock()
        call_timeout = max(1.0, deadline - t0 - 1.0)
        if llm is None:
            res, err = None, "ctx.llm unavailable"
        else:
            res, err = _call_before(deadline, self._clock, lambda: llm.complete(
                messages, provider=cand.provider, model=cand.model, timeout=call_timeout, purpose=purpose))
        latency = int((self._clock() - t0) * 1000)
        raw = str(getattr(res, "text", "") or "").strip() if res is not None else ""
        text, missing = raw, []
        if enriched is not None and raw:
            text, missing = enrich.finish(raw, enriched)
        verdict = Verdict("error", err) if err else judge(text, turn.user_message)
        if verdict.outcome == OK and missing:  # invariant 1: locked facts are checked in the answer
            verdict = Verdict(DEGRADED, "missing locked facts: " + ", ".join(missing[:8]))
        usage = getattr(res, "usage", None)
        attempt = Attempt(
            turn_id=turn_id, session_id=turn.session_id, kind=kind, tier=tier_index, index=index,
            provider=cand.provider, model=cand.model, outcome=verdict.outcome, reason=verdict.reason[:300],
            prompt_hash=prompt_hash(messages), prompt=messages, latency_ms=latency,
            glossary_hash=enriched.glossary_hash if enriched is not None else "",
            facts_expected=list(enriched.facts) if enriched is not None else [],
            facts_missing=missing,
            tokens_in=getattr(usage, "input_tokens", None), tokens_out=getattr(usage, "output_tokens", None))
        return _Try(cand, attempt, text, raw)

    def _failed(self, cand: Candidate, kind: str, turn_id: str, turn: _Turn, tier_index: int, index: int,
                reason: str) -> "_Try":
        attempt = Attempt(turn_id=turn_id, session_id=turn.session_id, kind=kind, tier=tier_index, index=index,
                          provider=cand.provider, model=cand.model, outcome="error", reason=reason[:300])
        return _Try(cand, attempt, "", "")

    def _evaluate(self, configured: Any) -> None:
        """Re-check promotions against the attempt log, off the hook's thread: reading the log must
        not cost the reply any time (invariant 4)."""
        if self.log is None:
            return

        def run() -> None:
            try:
                changes = promote.maybe_evaluate(configured, self.promotions, self.log.recent(1000))
                for line in changes or ():
                    logger.info("model-router: promotion %s", line)
            except Exception:
                logger.exception("model-router: promotion check failed")

        if self._evaluate_async:
            threading.Thread(target=run, name="model-router-promote", daemon=True).start()
        else:
            run()

    def _write(self, records: list[Attempt]) -> None:
        if self.log is None:
            return
        for record in records:
            self.log.append(record)


@dataclasses.dataclass
class _Try:
    cand: Candidate
    attempt: Attempt
    text: str   # as delivered (terms restored for an enriched request)
    raw: str    # as the model wrote it


@dataclasses.dataclass
class _Chosen:
    text: str
    kind: str
    tier: int
    by: str               # the model whose text is delivered (the synthesizer when combined)
    sources: list[str]    # the answers it came from
