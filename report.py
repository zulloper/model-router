"""What the ladder looks like and how its models have done — shared by ``/route``, the CLI and the
Desktop backend so all three say the same thing.

Stats come from the attempt log (:mod:`attempts`), per model, over the same recent window that
promotion uses (:mod:`promote`).
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from . import promote
from .attempts import ENRICH, PLAIN, SYNTH, Attempt
from .outcome import OUTCOMES
from .settings import Settings

STATS_WINDOW = promote.WINDOW


def model_stats(attempts: Iterable[Attempt], window: int = STATS_WINDOW) -> dict[str, dict[str, Any]]:
    """Per model: outcome counts of its last ``window`` plain and enriched attempts, synthesis
    calls, and the reason of its latest failure."""
    out: dict[str, dict[str, Any]] = {}
    seen: dict[tuple[str, str], list[str]] = {}
    for a in attempts:
        if a.kind not in (PLAIN, ENRICH, SYNTH):
            continue
        row = out.setdefault(a.key, {"plain": {}, "enrich": {}, "synthesis": 0, "last_failure": ""})
        if a.kind == SYNTH:
            row["synthesis"] += 1
        else:
            seen.setdefault((a.key, a.kind), []).append(a.outcome)
        if a.outcome != "ok" and a.reason:
            row["last_failure"] = a.reason[:200]
    for (key, kind), outcomes in seen.items():
        recent = outcomes[-window:]
        out[key][kind] = {o: recent.count(o) for o in OUTCOMES} | {"samples": len(recent)}
    return out


def ladder_rows(settings: Settings, promotions: Mapping[str, int]) -> list[list[dict[str, Any]]]:
    """The ladder as it will be used (with promotions when auto-promote is on)."""
    promoted = dict(promotions) if settings.auto_promote else {}
    ladder = promote.effective(settings.ladder, promoted)
    return [[{"key": c.key, "promoted": int(promoted.get(c.key, 0) or 0)} for c in tier] for tier in ladder.tiers]


def _counts(row: Mapping[str, Any]) -> str:
    if not row:
        return "—"
    return f"{row.get('ok', 0)}/{row.get('samples', 0)} ok" + "".join(
        f", {row[o]} {o}" for o in ("blocked", "degraded", "error") if row.get(o))


def describe(settings: Settings, promotions: Mapping[str, int], *, hold_stream: Optional[bool] = None,
             hook_timeout: Optional[float] = None) -> str:
    lines = [f"model-router: {settings.mode}"
             + ("" if settings.ladder_active or settings.mode == "off" else " (no tiers yet — nothing to do)")]
    if settings.ladder.empty:
        lines.append("  Set tiers with `hermes model-router setup`, or in the Desktop: Settings ▸ Plugins ▸ Model Router.")
    else:
        lines += ["", "Tier ladder" + (" (auto-promote on)" if settings.auto_promote else "") + ":"]
        for index, tier in enumerate(ladder_rows(settings, promotions), start=1):
            names = [f"{m['key']} ↑{m['promoted']}" if m["promoted"] else m["key"] for m in tier]
            lines.append(f"  tier {index}: {', '.join(names) or '(empty — its models were promoted)'}")
    flags = [f"parallel {'on' if settings.parallel else 'off'}", f"synthesis {'on' if settings.synthesize else 'off'}",
             f"enriched retry {'on' if settings.enrich_on_retry else 'off'}"]
    if hold_stream is not None:
        flags.append(f"hold_stream {'on' if hold_stream else 'off'}")
    lines += ["", "Retries: " + ", ".join(flags)]
    if hook_timeout is not None:
        from .ladder import ladder_budget
        budget = ladder_budget(hook_timeout, settings.retry_budget_seconds)
        note = " (180-300 recommended)" if 0 < hook_timeout < 120 else ""
        lines.append(f"Time: hook timeout {hook_timeout:g}s{note}, ladder budget {budget:g}s")
    if settings.security_roe.strip():
        lines.append("Rules of engagement: set (quoted in enriched retries)")
    if settings.warnings:
        lines += ["", "Warnings:"] + [f"  ⚠ {w}" for w in settings.warnings]
    return "\n".join(lines)


def describe_stats(stats: Mapping[str, Mapping[str, Any]], promotions: Mapping[str, int]) -> str:
    if not stats:
        return "No ladder attempts recorded yet."
    lines = [f"Last {STATS_WINDOW} attempts per model (as written | enriched):"]
    for key in sorted(stats):
        row = stats[key]
        up = f"  ↑{promotions[key]}" if promotions.get(key) else ""
        synth = f", combined {row['synthesis']}x" if row.get("synthesis") else ""
        lines.append(f"  {key}{up}: {_counts(row['plain'])} | {_counts(row['enrich'])}{synth}")
        if row.get("last_failure"):
            lines.append(f"      last failure: {row['last_failure']}")
    return "\n".join(lines)
