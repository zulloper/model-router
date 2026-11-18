"""A model on the ladder: ``provider/model`` (an ``@effort`` suffix is read and ignored).

The model may itself contain ``/`` (OpenRouter-style ids): only the first ``/`` splits.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Mapping, Optional

EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")


@dataclasses.dataclass(frozen=True)
class Candidate:
    provider: str
    model: str
    effort: Optional[str] = None

    @property
    def key(self) -> str:
        """Identity for stats and promotions: provider/model (effort is a setting, not a model)."""
        return f"{self.provider}/{self.model}"

    def __str__(self) -> str:
        return f"{self.key}@{self.effort}" if self.effort else self.key


def _norm(provider: Any) -> str:
    return str(provider or "").strip().lower()


def parse_candidate(text: Any) -> Optional[Candidate]:
    """``provider/model[@effort]`` -> Candidate. The model may itself contain ``/`` (OpenRouter ids)."""
    if isinstance(text, Mapping):
        provider, model = _norm(text.get("provider")), str(text.get("model") or "").strip()
        effort = _norm(text.get("effort") or text.get("reasoning_effort")) or None
    elif isinstance(text, str):
        body, effort = text.strip(), None
        if "@" in body:
            head, _, tail = body.rpartition("@")
            if _norm(tail) in EFFORTS:
                body, effort = head, _norm(tail)
        provider, _, model = body.partition("/")
        provider, model = _norm(provider), model.strip()
    else:
        return None
    if not provider or not model:
        return None
    if effort is not None and effort not in EFFORTS:
        effort = None
    return Candidate(provider, model, effort)
