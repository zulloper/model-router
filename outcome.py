"""Is this reply blocked? (0.4.0, DESIGN.md §3 and §6.1)

Every attempt — the profile model's own reply and each tier retry — gets one outcome:

* ``ok``        a usable answer.
* ``blocked``   a guardrail or refusal: a policy error from the provider, or a reply whose text
                declines the task.
* ``degraded``  answered, but not what was asked: code/a PoC was requested and the reply is prose.
* ``error``     the model could not answer for another reason (unknown model, entitlement,
                retries exhausted on a transient error, empty reply).

Anything but ``ok`` climbs the ladder. Keeping the four apart matters for statistics: a model that
is often blocked is a different problem from one that answers poorly.

Whether the turn ended on a provider error is not in ``transform_llm_output``'s payload, so
:class:`TurnSignals` follows the turn's provider attempts through the ``post_api_request`` and
``api_request_error`` observers. Plugin ``ctx.llm`` calls are auxiliary calls and do not fire those
hooks, so the ladder's own retries never show up here.

Nothing in this module depends on task classification: ``security`` is the chat's explicit
security switch (``/route sec on``), not a guess from the text.
"""

from __future__ import annotations

import dataclasses
import threading
from collections import OrderedDict
from typing import Any, Mapping, Optional

from .refusal import degraded_reason, matches_guardrail, refusal_match

OK, BLOCKED, DEGRADED, ERROR = "ok", "blocked", "degraded", "error"
OUTCOMES = (OK, BLOCKED, DEGRADED, ERROR)

# Hermes FailoverReason values meaning a policy stopped the request (rewording or another model
# may help), as opposed to the model being unable to serve it at all.
POLICY_REASONS = frozenset({
    "content_policy_blocked", "provider_policy_blocked", "data_policy_denied", "upstream_blocked",
    "refusal", "content_filter",
})
_STRUCTURED_BLOCK_CATEGORIES = ("cyber", "bio", "reasoning_extraction")
_MAX_TURNS = 512


@dataclasses.dataclass(frozen=True)
class ApiFailure:
    """The last failed provider attempt of a turn."""
    reason: str
    retryable: Optional[bool] = None
    status_code: Optional[int] = None
    message: str = ""
    provider: str = ""
    model: str = ""
    structured_block: str = ""  # e.g. "refusal" or "cyber" when the provider said so in the body

    @property
    def policy(self) -> bool:
        return self.reason in POLICY_REASONS or bool(self.structured_block)


@dataclasses.dataclass(frozen=True)
class Verdict:
    outcome: str
    reason: str  # what decided it: the error reason or the matched text (for the attempt log)

    @property
    def climb(self) -> bool:
        return self.outcome != OK


def _structured_block(error: Any) -> str:
    if not isinstance(error, Mapping):
        return ""
    if str(error.get("stop_reason") or "") == "refusal":
        return "refusal"
    details = error.get("stop_details") or error.get("details") or {}
    if isinstance(details, Mapping):
        category = str(details.get("category") or "").lower()
        if category in _STRUCTURED_BLOCK_CATEGORIES:
            return category
    return ""


def _message(error: Any) -> str:
    if isinstance(error, Mapping):
        return str(error.get("message") or error.get("detail") or "")[:300]
    return str(error or "")[:300]


class TurnSignals:
    """The last provider attempt per turn. Thread-safe; keeps the most recent turns only."""

    def __init__(self, max_turns: int = _MAX_TURNS) -> None:
        self._lock = threading.Lock()
        self._last: "OrderedDict[str, Optional[ApiFailure]]" = OrderedDict()  # None = last one succeeded
        self._max = max_turns

    def _put(self, turn_id: str, value: Optional[ApiFailure]) -> None:
        if not turn_id:
            return
        with self._lock:
            self._last[turn_id] = value
            self._last.move_to_end(turn_id)
            while len(self._last) > self._max:
                self._last.popitem(last=False)

    def record_success(self, turn_id: str) -> None:
        self._put(turn_id, None)

    def record_error(self, turn_id: str, *, reason: Any = "", retryable: Any = None, status_code: Any = None,
                     error: Any = None, provider: Any = "", model: Any = "") -> None:
        status = status_code if isinstance(status_code, int) else None
        self._put(turn_id, ApiFailure(
            reason=str(reason or ""), retryable=retryable if isinstance(retryable, bool) else None,
            status_code=status, message=_message(error), provider=str(provider or "").lower(),
            model=str(model or ""), structured_block=_structured_block(error)))

    def ended_on_error(self, turn_id: str) -> Optional[ApiFailure]:
        """The failure the turn ended on, or None when its last provider attempt succeeded (or it
        made none we saw)."""
        with self._lock:
            return self._last.get(turn_id) if turn_id else None

    def forget(self, turn_id: str) -> None:
        with self._lock:
            self._last.pop(turn_id, None)


def judge(text: str, request: str = "", *, failure: Optional[ApiFailure] = None, security: bool = False,
          guardrail_patterns: tuple[str, ...] = ()) -> Verdict:
    """The outcome of one attempt. ``failure`` is set when the turn ended on a provider error: then
    ``text`` is Hermes' error notice, not a model answer, and the error decides."""
    if failure is not None:
        if failure.policy:
            return Verdict(BLOCKED, f"provider: {failure.structured_block or failure.reason}")
        if guardrail_patterns and matches_guardrail(failure.message, guardrail_patterns):
            return Verdict(BLOCKED, f"provider guardrail phrase: {failure.reason or 'error'}")
        transient = " (retries exhausted)" if failure.retryable else ""
        return Verdict(ERROR, f"provider: {failure.reason or 'error'}{transient}")
    if not (text or "").strip():
        return Verdict(ERROR, "empty reply")
    matched = refusal_match(text, security)
    if matched:
        return Verdict(BLOCKED, f"refusal: {matched}")
    why = degraded_reason(text, request, security)
    if why:
        return Verdict(DEGRADED, why)
    return Verdict(OK, "")
