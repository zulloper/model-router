"""Climb the tier ladder for a blocked reply (0.4.0, DESIGN.md §3; P2+P6).

When the profile model's reply is blocked (see :mod:`outcome`), the ladder's models are asked the
same request — tier 1 first, then tier 2, … — through ``ctx.llm.complete`` (no tools, the recent
conversation as context). The first usable answer replaces the blocked reply, with a footer that
says so. Every attempt is written to the attempt log (§6.1).

This is the minimal path: models run one after another and the first good answer wins. Parallel
calls and synthesis come in P5, the enriched retry in P4.

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

from .attempts import ORIGIN, PLAIN, Attempt, AttemptLog, prompt_hash
from .outcome import BLOCKED, DEGRADED, OK, TurnSignals, Verdict, judge
from .scope import Candidate

logger = logging.getLogger(__name__)

HOOK_TIMEOUT_DEFAULT = 30.0     # Hermes' plugins.hook_callback_timeout default
HOOK_TIMEOUT_MAX = 600.0        # Hermes clamps the setting to this
MARGIN_SECONDS = 4.0            # finish this far inside the hook timeout
MIN_CALL_SECONDS = 5.0          # don't start a model call with less time than this left
NO_TIMEOUT_BUDGET = 600.0       # hook_callback_timeout <= 0 disables Hermes' cap; still bound ourselves
_MAX_TURNS = 256
_CONTEXT_MESSAGES = 6
_CONTEXT_CHARS = 4000

_FOOTER = "\n\n— model-router: {origin} {what} ({why}); {by} answered instead (tier {tier}, without tools)."
_WHAT = {BLOCKED: "was blocked", DEGRADED: "answered without the requested code"}


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
                 timeout_fn: Callable[[], float] = hook_timeout) -> None:
        self.ctx, self.signals, self.log = ctx, signals, log
        self._clock, self._timeout_fn = clock, timeout_fn
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

        chosen: Optional[tuple[Attempt, str, int]] = None
        for tier_index, tier in enumerate(settings.ladder.tiers):
            for cand in tier:
                if cand.key == origin.key:
                    continue  # the model that just failed, asked the same thing again
                if deadline - self._clock() < MIN_CALL_SECONDS:
                    break
                attempt, text = self._try(cand, messages, turn_id, turn, tier_index, len(records), deadline)
                records.append(attempt)
                if attempt.outcome == OK:
                    chosen = (attempt, text, tier_index)
                    break
            if chosen or deadline - self._clock() < MIN_CALL_SECONDS:
                break

        if chosen:
            chosen[0].delivered = True
        self._write(records)
        if not chosen:
            return None
        attempt, text, tier_index = chosen
        footer = _FOOTER.format(origin=origin.key, what=_WHAT.get(verdict.outcome, "failed"),
                                why=verdict.reason[:80], by=attempt.key, tier=tier_index + 1)
        return text.rstrip() + footer

    def _try(self, cand: Candidate, messages: list, turn_id: str, turn: _Turn, tier_index: int, index: int,
             deadline: float) -> tuple[Attempt, str]:
        llm = getattr(self.ctx, "llm", None)
        t0 = self._clock()
        call_timeout = max(1.0, deadline - t0 - 1.0)
        if llm is None:
            res, err = None, "ctx.llm unavailable"
        else:
            res, err = _call_before(deadline, self._clock, lambda: llm.complete(
                messages, provider=cand.provider, model=cand.model, timeout=call_timeout,
                purpose="model-router: tier retry"))
        latency = int((self._clock() - t0) * 1000)
        text = str(getattr(res, "text", "") or "").strip() if res is not None else ""
        verdict = Verdict("error", err) if err else judge(text, turn.user_message)
        usage = getattr(res, "usage", None)
        attempt = Attempt(
            turn_id=turn_id, session_id=turn.session_id, kind=PLAIN, tier=tier_index, index=index,
            provider=cand.provider, model=cand.model, outcome=verdict.outcome, reason=verdict.reason[:300],
            prompt_hash=prompt_hash(messages), prompt=messages, latency_ms=latency,
            tokens_in=getattr(usage, "input_tokens", None), tokens_out=getattr(usage, "output_tokens", None))
        return attempt, text

    def _write(self, records: list[Attempt]) -> None:
        if self.log is None:
            return
        for record in records:
            self.log.append(record)
