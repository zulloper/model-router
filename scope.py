"""The allowlist (``scope``) and the routes built on it.

``scope`` maps up to :data:`MAX_PROVIDERS` provider slugs to the models routing may use on each.
Every candidate the router ever picks comes from here: a route entry, a pin or an explore winner
outside the allowlist is dropped, never used.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any, Iterable, Mapping, Optional

logger = logging.getLogger(__name__)

MAX_PROVIDERS = 3
TASK_CLASSES = ("quick", "standard", "code", "deep")
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
DEFAULT_EFFORT = {"quick": "low", "standard": "medium", "code": "high", "deep": "high"}


@dataclasses.dataclass(frozen=True)
class Candidate:
    provider: str
    model: str
    effort: Optional[str] = None

    @property
    def key(self) -> str:
        """Identity for stats and preferences: provider/model (effort is a setting, not a model)."""
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


@dataclasses.dataclass
class Scope:
    providers: dict[str, list[str]]  # provider slug -> allowed models, in the user's order
    warnings: list[str] = dataclasses.field(default_factory=list)

    def allows(self, provider: str, model: str) -> bool:
        return model in self.providers.get(_norm(provider), ())

    def allows_candidate(self, cand: Candidate) -> bool:
        return self.allows(cand.provider, cand.model)

    def all_models(self) -> list[Candidate]:
        return [Candidate(p, m) for p, models in self.providers.items() for m in models]

    @property
    def empty(self) -> bool:
        return not any(self.providers.values())


def parse_scope(raw: Any) -> Scope:
    """Read the ``scope`` setting. Keeps the first :data:`MAX_PROVIDERS` providers and says so."""
    warnings: list[str] = []
    providers: dict[str, list[str]] = {}
    items: Iterable[tuple[Any, Any]]
    if isinstance(raw, Mapping):
        items = raw.items()
    elif isinstance(raw, list):  # [{provider: x, models: [...]}, ...]
        items = [((e or {}).get("provider"), (e or {}).get("models")) for e in raw if isinstance(e, Mapping)]
    else:
        items = []
        if raw not in (None, "", {}):
            warnings.append("scope must be a mapping of provider -> [models]; ignoring it")
    for provider, models in items:
        slug = _norm(provider)
        if not slug:
            continue
        if isinstance(models, str):
            models = [models]
        clean = []
        for m in models or []:
            m = str(m or "").strip()
            if m and m not in clean:
                clean.append(m)
        if not clean:
            continue
        if slug in providers:
            providers[slug].extend(m for m in clean if m not in providers[slug])
            continue
        if len(providers) >= MAX_PROVIDERS:
            warnings.append(f"scope allows at most {MAX_PROVIDERS} providers; ignoring '{slug}'")
            continue
        providers[slug] = clean
    return Scope(providers, warnings)


def build_routes(raw_routes: Any, scope: Scope) -> tuple[dict[str, list[Candidate]], list[str]]:
    """Routes per task class, filtered to the allowlist: the class's own entries first, then every
    other allowed model as a backup. Routing never leaves scope and never has nothing to pick."""
    warnings: list[str] = []
    routes: dict[str, list[Candidate]] = {}
    raw_routes = raw_routes if isinstance(raw_routes, Mapping) else {}
    for cls in TASK_CLASSES:
        entries = raw_routes.get(cls) or []
        if isinstance(entries, (str, Mapping)):
            entries = [entries]
        chosen: list[Candidate] = []
        for entry in entries:
            cand = parse_candidate(entry)
            if cand is None:
                warnings.append(f"routes.{cls}: can't read {entry!r} (want provider/model@effort)")
                continue
            if not scope.allows_candidate(cand):
                warnings.append(f"routes.{cls}: {cand.key} is outside the allowlist; skipped")
                continue
            if all(c.key != cand.key for c in chosen):
                chosen.append(cand)
        # The rest of the allowlist follows as backups, so a stopped model always has somewhere
        # allowed to go; backups run at the effort of the class's first entry.
        effort = chosen[0].effort if chosen and chosen[0].effort else DEFAULT_EFFORT[cls]
        chosen += [dataclasses.replace(c, effort=effort) for c in scope.all_models()
                   if all(x.key != c.key for x in chosen)]
        routes[cls] = chosen
    for cls in raw_routes:
        if cls not in TASK_CLASSES:
            warnings.append(f"routes.{cls}: unknown task class (use {', '.join(TASK_CLASSES)})")
    return routes, warnings


def fallback_warnings(config: Mapping[str, Any], scope: Scope) -> list[str]:
    """Hermes' own ``fallback_providers`` chain runs outside this plugin; flag entries it may pick
    that are outside the allowlist."""
    out = []
    chain = config.get("fallback_providers") or config.get("fallback_model") or []
    if isinstance(chain, Mapping):
        chain = [chain]
    for entry in chain if isinstance(chain, list) else []:
        if not isinstance(entry, Mapping):
            continue
        provider, model = _norm(entry.get("provider")), str(entry.get("model") or "")
        if provider and model and not scope.allows(provider, model):
            out.append(f"fallback_providers has {provider}/{model}, which is outside the allowlist; "
                       "Hermes may still fall back to it when no allowed model is left on that provider")
    return out
