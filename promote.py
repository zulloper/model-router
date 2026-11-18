"""Tier promotion (0.4.0, DESIGN.md §10-2, §6.1; P6b): models that keep winning move up.

The user's ``tiers`` setting is never rewritten. Promotions are a separate, learned layer kept in
the plugin state: ``{"provider/model": tiers_moved_up}``. The ladder used for a turn is the
configured one with each promoted model moved up that many tiers (to the end of the tier it lands
in). Promotion only lifts: a model never sits lower than where the user put it, and demotion only
undoes a promotion.

Evidence is the attempt log, ``plain`` attempts only — the request as the user wrote it, so a
model's record is about the task, not about how it reacts to rewording (§6.1). Calls abandoned at
the ladder's time budget are not the model's fault and are left out.

Rules (recommended values, DESIGN.md §18):

* window: each model's last ``WINDOW`` plain attempts; at least ``MIN_SAMPLES`` to judge it.
* promote one tier when its success rate is at least ``PROMOTE_RATE`` and beats the tier above's
  average by ``PROMOTE_MARGIN`` (tier-above models with enough samples); with no such model above,
  at least ``PROMOTE_RATE_ALONE``.
* demote one tier (undo a promotion) when its success rate falls below ``DEMOTE_RATE``.

A lower tier is only asked after the tiers above failed, so its record is on harder requests.
That bias works against promotion, which keeps it conservative.
"""

from __future__ import annotations

import dataclasses
import threading
from typing import Any, Iterable, Mapping, Optional

from .attempts import PLAIN, Attempt
from .outcome import OK
from .tiers import Ladder

WINDOW = 20
MIN_SAMPLES = 8
PROMOTE_RATE = 0.6
PROMOTE_MARGIN = 0.15
PROMOTE_RATE_ALONE = 0.75
DEMOTE_RATE = 0.4
STATE_KEY = "promotions"


@dataclasses.dataclass(frozen=True)
class Record:
    samples: int
    ok: int

    @property
    def rate(self) -> float:
        return self.ok / self.samples if self.samples else 0.0


def records(attempts: Iterable[Attempt], window: int = WINDOW) -> dict[str, Record]:
    """Per model: its last ``window`` plain attempts (oldest-first input), budget abandonments left out."""
    recent: dict[str, list[bool]] = {}
    for a in attempts:
        if a.kind != PLAIN or "time budget" in (a.reason or ""):
            continue
        recent.setdefault(a.key, []).append(a.outcome == OK)
    return {key: Record(len(hits[-window:]), sum(hits[-window:])) for key, hits in recent.items()}


def effective(ladder: Ladder, promotions: Mapping[str, int]) -> Ladder:
    """The configured ladder with promoted models moved up (never below their configured tier)."""
    if not promotions or ladder.empty:
        return ladder
    placed: list[list] = [[] for _ in ladder.tiers]
    moved: list[list] = [[] for _ in ladder.tiers]
    for index, tier in enumerate(ladder.tiers):
        for cand in tier:
            up = max(0, int(promotions.get(cand.key, 0) or 0))
            target = max(0, index - up)
            (moved if target != index else placed)[target].append(cand)
    # A tier emptied by promotions stays (empty) so tier numbers keep matching the configuration.
    return Ladder(tuple(tuple(placed[i] + moved[i]) for i in range(len(ladder.tiers))), ladder.warnings)


def evaluate(ladder: Ladder, promotions: Mapping[str, int],
             attempts: Iterable[Attempt]) -> tuple[dict[str, int], list[str]]:
    """New promotions after looking at the attempt log, and a line per change. One step per model
    per evaluation."""
    stats = records(attempts)
    current = effective(ladder, promotions)
    configured_tier = {c.key: i for i, tier in enumerate(ladder.tiers) for c in tier}
    new = {k: int(v) for k, v in promotions.items() if k in configured_tier and int(v or 0) > 0}
    changes: list[str] = []
    for index, tier in enumerate(current.tiers):
        above = current.tiers[index - 1] if index > 0 else ()
        above_rates = [stats[c.key].rate for c in above if c.key in stats and stats[c.key].samples >= MIN_SAMPLES]
        for cand in tier:
            rec = stats.get(cand.key)
            if rec is None or rec.samples < MIN_SAMPLES:
                continue
            if index > 0:
                bar = (max(PROMOTE_RATE, sum(above_rates) / len(above_rates) + PROMOTE_MARGIN)
                       if above_rates else PROMOTE_RATE_ALONE)
                if rec.rate >= bar:
                    new[cand.key] = new.get(cand.key, 0) + 1
                    changes.append(f"{cand.key} ↑ tier {index + 1} → {index} "
                                   f"({rec.ok}/{rec.samples} ok, bar {bar:.0%})")
                    continue
            if new.get(cand.key, 0) > 0 and rec.rate < DEMOTE_RATE:
                new[cand.key] -= 1
                if new[cand.key] == 0:
                    del new[cand.key]
                changes.append(f"{cand.key} ↓ tier {index + 1} → {index + 2} "
                               f"({rec.ok}/{rec.samples} ok, below {DEMOTE_RATE:.0%})")
    return new, changes


class Promotions:
    """Promotions in the plugin's durable state. Thread-safe; state errors never raise."""

    def __init__(self, state: Any = None) -> None:
        self._state = state
        self._lock = threading.Lock()

    def get(self) -> dict[str, int]:
        if self._state is None:
            return {}
        try:
            value = self._state.get(STATE_KEY, default={})
        except Exception:
            return {}
        if not isinstance(value, Mapping):
            return {}
        return {str(k): int(v) for k, v in value.items() if isinstance(v, (int, float)) and int(v) > 0}

    def set(self, value: Mapping[str, int]) -> None:
        if self._state is None:
            return
        try:
            with self._lock:
                self._state.set(STATE_KEY, dict(value))
        except Exception:
            pass

    def clear(self) -> None:
        self.set({})


def maybe_evaluate(ladder: Ladder, store: Promotions, attempts: Iterable[Attempt]) -> Optional[list[str]]:
    """Evaluate and save when something changed. -> the change lines, or None if nothing moved."""
    old = store.get()
    new, changes = evaluate(ladder, old, attempts)
    if new == old:
        return None
    store.set(new)
    return changes
