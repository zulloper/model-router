"""Plugin settings (``plugins.entries.model-router.settings``), read fresh on every turn.

0.4.0 has one routing setting, the tier ladder (``tiers``). The rest tune how a blocked reply climbs
it. Keys from 0.3.x that no longer do anything are reported as warnings, not silently ignored.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Callable

from .tiers import Ladder, parse_tiers

# 0.3.x keys that 0.4.0 no longer reads (README: migrating from 0.3.x).
LEGACY_KEYS = (
    "scope", "routes", "cross_provider", "classifier", "soft_refusal_retry", "degraded_retry",
    "cooldown_minutes", "security_preamble", "security_preamble_extra", "security_sanitize",
    "security_sanitize_max_level", "security_sanitize_llm", "security_sanitize_retry_same_model",
    "security_fact_lock",
)
HOLD_CHOICES = ("auto", "on", "off")


@dataclasses.dataclass
class Settings:
    mode: str = "auto"
    ladder: Ladder = dataclasses.field(default_factory=Ladder)
    retry_budget_seconds: Any = "auto"
    parallel: bool = True
    synthesize: bool = True
    enrich_on_retry: bool = True
    auto_promote: bool = True
    security_roe: str = ""
    security_guardrail_defaults: bool = True
    extra_guardrail_patterns: tuple[str, ...] = ()
    max_explore: int = 3
    warnings: list[str] = dataclasses.field(default_factory=list)

    @property
    def ladder_active(self) -> bool:
        """A blocked reply climbs the tiers."""
        return self.mode == "auto" and not self.ladder.empty


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def hold_stream_setting(value: Any) -> str:
    """``hold_stream`` as auto/on/off; 0.3.x wrote a bool."""
    if value is True:
        return "on"
    if value is False:
        return "off"
    text = str(value or "auto").strip().lower()
    return text if text in HOLD_CHOICES else "auto"


def load_settings(get: Callable[[str, Any], Any]) -> Settings:
    """``get`` is ``ctx.get_config`` (or a stand-in with the same signature)."""
    ladder = parse_tiers(get("tiers", []))
    mode = str(get("mode", "auto") or "auto").lower()
    patterns = get("extra_guardrail_patterns", []) or []
    warnings = list(ladder.warnings)
    legacy = [key for key in LEGACY_KEYS if get(key, None) not in (None, "", [], {})]
    if legacy:
        warnings.append("0.3.x settings no longer used: " + ", ".join(legacy)
                        + " — set tiers instead (README: migrating from 0.3.x)")
    return Settings(
        mode=mode if mode in ("auto", "off") else "auto",
        ladder=ladder,
        retry_budget_seconds=get("retry_budget_seconds", "auto"),
        parallel=bool(get("parallel", True)),
        synthesize=bool(get("synthesize", True)),
        enrich_on_retry=bool(get("enrich_on_retry", True)),
        auto_promote=bool(get("auto_promote", True)),
        security_roe=str(get("security_roe", "") or ""),
        security_guardrail_defaults=bool(get("security_guardrail_defaults", True)),
        extra_guardrail_patterns=tuple(str(p).lower() for p in patterns if str(p).strip()),
        max_explore=max(1, min(8, _as_int(get("max_explore", 3), 3))),
        warnings=warnings,
    )
