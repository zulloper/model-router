"""The tier ladder (0.4.0, DESIGN.md §2): ordered tiers of models a blocked reply may climb.

``tiers`` is a list of tiers; each tier is an ordered list of ``provider/model`` entries::

    tiers:
      - [openai-codex/gpt-6.2-sol]
      - [anthropic/claude-opus-4-8, openai-codex/gpt-6.1-sol-900k]

The ladder is also the allowlist: nothing outside it is ever called. Parsing never raises — an
entry that can't be read is dropped with a warning, so a typo costs one model, not the turn.

This module must not depend on task classification (``classify.py``): the ladder is one ladder for
every request, and ``classify.py`` goes away in P7.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Iterable, Mapping, Optional

from .scope import Candidate, parse_candidate

# A larger tier still works, but every model in it is called in parallel inside one hook's time
# budget (DESIGN.md §3.1), so it is flagged.
TIER_SIZE_HINT = 4


@dataclasses.dataclass(frozen=True)
class Ladder:
    tiers: tuple[tuple[Candidate, ...], ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.tiers

    def models(self) -> list[Candidate]:
        """Every model on the ladder, tier by tier, in order."""
        return [c for tier in self.tiers for c in tier]

    def tier_of(self, key: str) -> Optional[int]:
        """0-based tier index of a ``provider/model`` key, or None when it is not on the ladder."""
        for index, tier in enumerate(self.tiers):
            if any(c.key == key for c in tier):
                return index
        return None

    def allows(self, provider: str, model: str) -> bool:
        return self.tier_of(f"{(provider or '').strip().lower()}/{(model or '').strip()}") is not None


def _entries(tier: Any) -> Optional[Iterable[Any]]:
    if isinstance(tier, (str, Mapping)):
        return [tier]
    if isinstance(tier, (list, tuple)):
        return tier
    return None


def parse_tiers(raw: Any) -> Ladder:
    """Read the ``tiers`` setting. Tier numbers in warnings are 1-based, as a user counts them."""
    if raw in (None, "", [], {}):
        return Ladder()
    if not isinstance(raw, (list, tuple)):
        return Ladder(warnings=("tiers must be a list of tiers, each a list of provider/model; ignoring it",))
    warnings: list[str] = []
    seen: dict[str, int] = {}
    tiers: list[tuple[Candidate, ...]] = []
    effort_noted = False
    for number, tier in enumerate(raw, start=1):
        entries = _entries(tier)
        if entries is None:
            warnings.append(f"tiers[{number}]: can't read {tier!r} (want a list of provider/model); skipped")
            continue
        chosen: list[Candidate] = []
        for entry in entries:
            cand = parse_candidate(entry)
            if cand is None:
                warnings.append(f"tiers[{number}]: can't read {entry!r} (want provider/model); skipped")
                continue
            if cand.key in seen:
                warnings.append(f"tiers[{number}]: {cand.key} is already in tier {seen[cand.key]}; "
                                "kept there only")
                continue
            if cand.effort and not effort_noted:
                effort_noted = True
                warnings.append(f"tiers: '@{cand.effort}' on {cand.key} is ignored — retries run at "
                                "the model's default reasoning effort")
            seen[cand.key] = number
            chosen.append(dataclasses.replace(cand, effort=None))
        if not chosen:
            warnings.append(f"tiers[{number}]: no usable models; skipped")
            continue
        if len(chosen) > TIER_SIZE_HINT:
            warnings.append(f"tiers[{number}]: {len(chosen)} models run in parallel within one time "
                            f"budget; {TIER_SIZE_HINT} or fewer is recommended")
        tiers.append(tuple(chosen))
    return Ladder(tuple(tiers), tuple(warnings))
