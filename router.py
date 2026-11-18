"""Per-turn routing state, model health and learned preferences.

A turn's plan is an ordered list of allowed candidates. The ``llm_request`` middleware sends each
provider request to the first candidate on the request's provider that has not failed this turn; a
failure (guardrail, refusal, model error) marks the candidate and the next request goes to the next
one. Health and preferences persist in the plugin's state store so they survive restarts.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Mapping, Optional

from .scope import TASK_CLASSES, Candidate, Scope, build_routes, parse_candidate, parse_scope
from .tiers import Ladder, parse_tiers

logger = logging.getLogger(__name__)

_MAX_TURNS = 256
_FAIL_STREAK_COOLDOWN = 2


@dataclasses.dataclass
class Settings:
    mode: str
    scope: Scope
    routes: dict[str, list[Candidate]]
    cross_provider: bool
    classifier: str
    soft_refusal_retry: bool
    degraded_retry: bool
    cooldown_minutes: int
    max_explore: int
    extra_guardrail_patterns: tuple[str, ...]
    security_preamble: bool
    security_roe: str
    security_preamble_extra: str
    security_guardrail_defaults: bool
    security_sanitize: bool
    security_sanitize_max_level: int
    security_sanitize_llm: bool
    security_sanitize_retry_same_model: bool
    security_fact_lock: bool
    warnings: list[str]
    ladder: Ladder = dataclasses.field(default_factory=Ladder)  # 0.4.0 tiers
    retry_budget_seconds: Any = "auto"
    enrich_on_retry: bool = True

    @property
    def active(self) -> bool:
        return self.mode == "auto" and not self.scope.empty

    @property
    def ladder_active(self) -> bool:
        """0.4.0 path: a blocked reply climbs the tiers. When on, it replaces 0.3.0's re-answer."""
        return self.mode == "auto" and not self.ladder.empty


def load_settings(get: Callable[[str, Any], Any]) -> Settings:
    """``get`` is ``ctx.get_config``."""
    scope = parse_scope(get("scope", {}))
    routes, route_warnings = build_routes(get("routes", {}), scope)
    mode = str(get("mode", "auto") or "auto").lower()
    classifier = str(get("classifier", "rules") or "rules").lower()
    patterns = get("extra_guardrail_patterns", []) or []
    ladder = parse_tiers(get("tiers", []))
    return Settings(
        mode=mode if mode in ("auto", "off") else "auto",
        scope=scope, routes=routes,
        cross_provider=bool(get("cross_provider", False)),
        classifier=classifier if classifier in ("rules", "hybrid", "llm") else "rules",
        soft_refusal_retry=bool(get("soft_refusal_retry", True)),
        degraded_retry=bool(get("degraded_retry", True)),
        cooldown_minutes=_as_int(get("cooldown_minutes", 30), 30),
        max_explore=max(1, min(8, _as_int(get("max_explore", 3), 3))),
        extra_guardrail_patterns=tuple(str(p).lower() for p in patterns if str(p).strip()),
        security_preamble=bool(get("security_preamble", True)),
        security_roe=str(get("security_roe", "") or ""),
        security_preamble_extra=str(get("security_preamble_extra", "") or ""),
        security_guardrail_defaults=bool(get("security_guardrail_defaults", True)),
        security_sanitize=bool(get("security_sanitize", True)),
        security_sanitize_max_level=max(1, min(3, _as_int(get("security_sanitize_max_level", 3), 3))),
        security_sanitize_llm=bool(get("security_sanitize_llm", False)),
        security_sanitize_retry_same_model=bool(get("security_sanitize_retry_same_model", True)),
        security_fact_lock=bool(get("security_fact_lock", True)),
        warnings=scope.warnings + route_warnings + list(ladder.warnings),
        ladder=ladder,
        retry_budget_seconds=get("retry_budget_seconds", "auto"),
        enrich_on_retry=bool(get("enrich_on_retry", True)),
    )


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclasses.dataclass
class TurnPlan:
    turn_id: str
    session_id: str
    task_class: str
    why: str
    order: list[Candidate]
    user_message: str = ""
    context: list[dict[str, str]] = dataclasses.field(default_factory=list)  # recent text turns
    current: Optional[Candidate] = None
    answered_by: Optional[Candidate] = None  # set when a soft-refusal retry replaced the reply
    switched_provider: str = ""  # cross_provider: what the session was moved to, if anything
    failed: list[str] = dataclasses.field(default_factory=list)  # candidate keys
    events: list[str] = dataclasses.field(default_factory=list)
    sanitize_level: int = 0
    sanitize_tried: set[tuple[str, int]] = dataclasses.field(default_factory=set)
    sanitized: bool = False
    facts: list[str] = dataclasses.field(default_factory=list)
    glossary: dict[str, str] = dataclasses.field(default_factory=dict)  # replacement -> original
    rephrased_levels: set[int] = dataclasses.field(default_factory=set)
    rephrased: dict[int, str] = dataclasses.field(default_factory=dict)
    facts_retried: bool = False
    started: float = dataclasses.field(default_factory=time.time)

    def note(self, text: str) -> None:
        self.events.append(text)


class Router:
    """Thread-safe: hooks and middleware run on worker threads."""

    def __init__(self, state: Any = None, clock: Callable[[], float] = time.time):
        self._state = state  # ctx.state (get/set); None in tests that don't persist
        self._clock = clock
        self._lock = threading.RLock()
        self._turns: "OrderedDict[str, TurnPlan]" = OrderedDict()
        self._last_by_session: dict[str, str] = {}
        self._pins: dict[str, Candidate] = {}
        self._sec: set[str] = set()  # sessions pinned to the sec task class (/route sec)
        self._sec_level: dict[str, int] = {}  # session -> sanitize level that last worked
        self._escalate: dict[str, str] = {}  # session -> candidate key that just failed (soft refusal)
        self._health: dict[str, dict[str, Any]] = self._load("health", {})
        self._preferred: dict[str, str] = self._load("preferred", {})

    # --- persistence -------------------------------------------------------------------------
    def _load(self, key: str, default: Any) -> Any:
        if self._state is None:
            return dict(default)
        try:
            value = self._state.get(key, default=default)
            return dict(value) if isinstance(value, Mapping) else dict(default)
        except Exception as exc:
            logger.warning("model-router: could not read state %s: %s", key, exc)
            return dict(default)

    def _save(self, key: str, value: Any) -> None:
        if self._state is None:
            return
        try:
            self._state.set(key, value)
        except Exception as exc:
            logger.warning("model-router: could not save state %s: %s", key, exc)

    # --- planning ----------------------------------------------------------------------------
    def reload(self) -> None:
        """Re-read health and preferences: the Desktop settings page edits the same state file from
        the web server process, so each turn starts from what is on disk."""
        if self._state is None:
            return
        health, preferred = self._load("health", {}), self._load("preferred", {})
        with self._lock:
            self._health, self._preferred = health, preferred

    def plan(self, settings: Settings, *, turn_id: str, session_id: str, task_class: str, why: str,
             user_message: str = "", context: Optional[list[dict[str, str]]] = None) -> TurnPlan:
        self.reload()
        order = self.order_for(settings, task_class, session_id=session_id, consume_escalation=True)
        plan = TurnPlan(turn_id=turn_id, session_id=session_id, task_class=task_class, why=why,
                        order=order, user_message=user_message, context=list(context or []))
        with self._lock:
            self._turns[turn_id] = plan
            self._turns.move_to_end(turn_id)
            while len(self._turns) > _MAX_TURNS:
                self._turns.popitem(last=False)
            if session_id:
                self._last_by_session[session_id] = turn_id
        return plan

    def order_for(self, settings: Settings, task_class: str, *, session_id: str = "",
                  consume_escalation: bool = False) -> list[Candidate]:
        """Candidates for a task class: pin > learned preference > route order; cooling-down models
        go last; everything outside the allowlist is dropped."""
        scope = settings.scope
        with self._lock:
            pin = self._pins.get(session_id)
            if pin is not None and scope.allows_candidate(pin):
                return [pin]
            route = list(settings.routes.get(task_class) or settings.routes.get("standard") or [])
            pref_key = self._preferred.get(task_class)
            if pref_key:
                pref = next((c for c in route if c.key == pref_key), None)
                if pref is None:
                    stored = parse_candidate(pref_key)
                    if stored is not None and scope.allows_candidate(stored):
                        effort = route[0].effort if route else None
                        pref = dataclasses.replace(stored, effort=stored.effort or effort)
                if pref is not None:
                    route = [pref] + [c for c in route if c.key != pref.key]
            escalate = (self._escalate.pop(session_id, None) if consume_escalation
                        else self._escalate.get(session_id))
            if escalate and len(route) > 1:
                route = [c for c in route if c.key != escalate] + [c for c in route if c.key == escalate]
            healthy = [c for c in route if not self.cooling(c, settings)]
            cooling = [c for c in route if self.cooling(c, settings)]
            return [c for c in healthy + cooling if scope.allows_candidate(c)]

    def turn(self, turn_id: str) -> Optional[TurnPlan]:
        with self._lock:
            return self._turns.get(turn_id) if turn_id else None

    def last_turn(self, session_id: str) -> Optional[TurnPlan]:
        with self._lock:
            tid = self._last_by_session.get(session_id or "")
            return self._turns.get(tid) if tid else None

    def pick(self, plan: TurnPlan, provider: str, allow_retry: bool = False) -> Optional[Candidate]:
        """The candidate this provider request should use: the turn's current one while it hasn't
        failed and sits on this provider, else the next unfailed one on this provider.

        ``allow_retry`` lets a candidate that failed this turn run once more at a new sanitize
        level (guardrail round-trip); the (candidate, level) pair is recorded so it cannot loop.
        """
        prov = (provider or "").strip().lower()
        with self._lock:
            cur = plan.current

            def eligible(c: Candidate) -> bool:
                if c.key not in plan.failed:
                    return True
                return allow_retry and (c.key, plan.sanitize_level) not in plan.sanitize_tried

            if cur is not None and cur.provider == prov and eligible(cur):
                if cur.key in plan.failed:
                    plan.sanitize_tried.add((cur.key, plan.sanitize_level))
                    plan.note(f"↻ {cur} (sanitize L{plan.sanitize_level})")
                return cur
            nxt = next((c for c in plan.order if c.provider == prov and eligible(c)), None)
            if nxt is not None and nxt.key in plan.failed:
                plan.sanitize_tried.add((nxt.key, plan.sanitize_level))
                plan.note(f"↻ {nxt} (sanitize L{plan.sanitize_level})")
            if nxt is not None and (cur is None or nxt.key != cur.key):
                plan.note(f"→ {nxt}")
            plan.current = nxt
            return nxt

    def next_alternative(self, plan: TurnPlan, settings: Settings,
                         allow_retry: bool = False) -> Optional[Candidate]:
        """Another allowed model (any allowed provider) that hasn't failed this turn. With
        ``allow_retry`` the current model may be retried once at a new sanitize level first."""
        with self._lock:
            cur = plan.current
            if (allow_retry and cur is not None
                    and (cur.key, plan.sanitize_level) not in plan.sanitize_tried):
                plan.sanitize_tried.add((cur.key, plan.sanitize_level))
                return cur
            for c in plan.order:
                if c.key not in plan.failed and (plan.current is None or c.key != plan.current.key):
                    return c
            for c in settings.scope.all_models():
                if c.key not in plan.failed and (plan.current is None or c.key != plan.current.key):
                    return dataclasses.replace(c, effort=(plan.order[0].effort if plan.order else None))
        return None

    # --- outcomes ----------------------------------------------------------------------------
    def mark_failed(self, plan: TurnPlan, cand: Optional[Candidate], reason: str) -> None:
        if cand is None:
            return
        with self._lock:
            if cand.key not in plan.failed:
                plan.failed.append(cand.key)
            plan.note(f"✗ {cand.key}: {reason}")
            h = self._health.setdefault(cand.key, {"ok": 0, "fail": 0, "streak": 0, "last_fail": 0.0})
            h["fail"] = int(h.get("fail", 0)) + 1
            h["streak"] = int(h.get("streak", 0)) + 1
            h["last_fail"] = self._clock()
            h["last_reason"] = reason[:200]
            snapshot = dict(self._health)
        self._save("health", snapshot)

    def mark_ok(self, cand: Optional[Candidate]) -> None:
        if cand is None:
            return
        with self._lock:
            h = self._health.setdefault(cand.key, {"ok": 0, "fail": 0, "streak": 0, "last_fail": 0.0})
            h["ok"] = int(h.get("ok", 0)) + 1
            h["streak"] = 0
            snapshot = dict(self._health)
        self._save("health", snapshot)

    def cooling(self, cand: Candidate, settings: Settings) -> bool:
        h = self._health.get(cand.key) or {}
        if int(h.get("streak", 0)) < _FAIL_STREAK_COOLDOWN:
            return False
        return self._clock() - float(h.get("last_fail", 0.0)) < settings.cooldown_minutes * 60

    def escalate_next(self, session_id: str, failed_key: str) -> None:
        with self._lock:
            if session_id:
                self._escalate[session_id] = failed_key

    # --- pins and preferences ----------------------------------------------------------------
    def pin(self, session_id: str, cand: Optional[Candidate]) -> None:
        with self._lock:
            if cand is None:
                self._pins.pop(session_id, None)
            else:
                self._pins[session_id] = cand

    def pinned(self, session_id: str) -> Optional[Candidate]:
        with self._lock:
            return self._pins.get(session_id)

    def set_sec(self, session_id: str, on: bool) -> None:
        """Force this chat's turns onto the sec class (preamble included), or stop forcing."""
        with self._lock:
            self._sec_level.pop(session_id, None)
            if on and session_id:
                self._sec.add(session_id)
            else:
                self._sec.discard(session_id)

    def sec_on(self, session_id: str) -> bool:
        with self._lock:
            return bool(session_id) and session_id in self._sec

    def remember_level(self, session_id: str, level: int) -> None:
        """Start later turns of a chat at the level that worked, so a model that refused is not
        re-hit with the original wording every turn. L3 (the split) stays a within-turn escalation."""
        with self._lock:
            if session_id and level > 0:
                self._sec_level[session_id] = min(level, 2)
            else:
                self._sec_level.pop(session_id, None)

    def level_for(self, session_id: str) -> int:
        with self._lock:
            return self._sec_level.get(session_id, 0)

    def sanitize_levels(self) -> dict[str, int]:
        with self._lock:
            return dict(self._sec_level)

    def prefer(self, task_class: str, cand: Optional[Candidate]) -> None:
        if task_class not in TASK_CLASSES:
            return
        with self._lock:
            if cand is None:
                self._preferred.pop(task_class, None)
            else:
                self._preferred[task_class] = str(cand)
            snapshot = dict(self._preferred)
        self._save("preferred", snapshot)

    def preferences(self) -> dict[str, str]:
        with self._lock:
            return dict(self._preferred)

    def health(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {k: dict(v) for k, v in self._health.items()}

    def reset_stats(self) -> None:
        with self._lock:
            self._health.clear()
            self._preferred.clear()
            self._sec_level.clear()
        self._save("health", {})
        self._save("preferred", {})
