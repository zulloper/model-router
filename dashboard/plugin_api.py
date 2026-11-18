"""Model Router backend for the Desktop settings pages, mounted at ``/api/plugins/model-router/``.

The web server scopes every request to the profile the Desktop has active (``HERMES_HOME``), so
each route reads and writes *that* profile's settings and state — the same plugin-data folder the
gateway's ``ctx.state`` uses, so promotions and the attempt log are shared.

Routes:
    GET  /state      settings, the ladder as used (with promotions), model inventory, per-model
                     stats from the attempt log, time budget, warnings
    PUT  /settings   {"values": {...}} — validated, then written with Hermes' plugin-settings writer
    POST /reset      forget learned promotions (the attempt log is kept)
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

_log = logging.getLogger(__name__)
router = APIRouter()

PLUGIN_ID = "model-router"
_ROOT = Path(__file__).resolve().parents[1]
_PKG = "hermes_dashboard_plugin_model_router"
# Shape of GET /state. The Desktop page checks it: Hermes loads this file once per plugin host
# process, so after an update the page can meet the previous backend until the gateway restarts.
STATE_SCHEMA = 3
_BOOL_KEYS = ("parallel", "synthesize", "enrich_on_retry", "auto_promote", "security_guardrail_defaults",
              "subagent_retry")
_SETTING_KEYS = ("mode", "tiers", "hold_stream", "retry_budget_seconds", "security_roe", "extra_guardrail_patterns",
                 "max_explore", "subagent_toolsets") + _BOOL_KEYS


def _mod(name: str):
    """A module of the plugin package, loaded by path: the web server imports this file on its own,
    so relative imports are not available here."""
    if _PKG not in sys.modules:
        spec = importlib.util.spec_from_file_location(_PKG, _ROOT / "__init__.py",
                                                      submodule_search_locations=[str(_ROOT)])
        mod = importlib.util.module_from_spec(spec)
        sys.modules[_PKG] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception:
            sys.modules.pop(_PKG, None)
            raise
    return importlib.import_module(f"{_PKG}.{name}")


def _config() -> dict:
    from hermes_cli.config import load_config_readonly
    return load_config_readonly() or {}


def _settings_raw(cfg: dict) -> dict:
    entry = ((cfg.get("plugins") or {}).get("entries") or {}).get(PLUGIN_ID) or {}
    settings = entry.get("settings") if isinstance(entry, dict) else None
    return dict(settings) if isinstance(settings, dict) else {}


def _plugin_state():
    from hermes_cli.plugins_state import PluginState
    return PluginState(PLUGIN_ID)


def _enabled(cfg: dict) -> bool:
    plugins = cfg.get("plugins") or {}
    enabled = plugins.get("enabled") or []
    return PLUGIN_ID in enabled and PLUGIN_ID not in (plugins.get("disabled") or [])


def _hook_timeout(cfg: dict) -> float:
    raw = (cfg.get("plugins") or {}).get("hook_callback_timeout")
    try:
        value = 30.0 if raw is None else float(raw)
    except (TypeError, ValueError):
        value = 30.0
    return 30.0 if value < 0 else min(value, 600.0)


def build_state(cfg: dict, state: Any, inventory: dict[str, list[str]], attempts: list) -> dict:
    settings_mod, report, promote, ladder_mod = (_mod("settings"), _mod("report"), _mod("promote"),
                                                 _mod("ladder"))
    raw = _settings_raw(cfg)
    s = settings_mod.load_settings(lambda key, default=None: raw.get(key, default))
    promotions = promote.Promotions(state).get()
    hold = settings_mod.hold_stream_setting(raw.get("hold_stream"))
    timeout = _hook_timeout(cfg)
    model = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    return {
        "schema": STATE_SCHEMA,
        "plugin_enabled": _enabled(cfg),
        "current": {"provider": str(model.get("provider") or ""), "model": str(model.get("default") or "")},
        "settings": {
            "mode": s.mode,
            "tiers": [[c.key for c in tier] for tier in s.ladder.tiers],
            "parallel": s.parallel, "synthesize": s.synthesize, "enrich_on_retry": s.enrich_on_retry,
            "auto_promote": s.auto_promote, "security_roe": s.security_roe, "hold_stream": hold,
            "retry_budget_seconds": str(s.retry_budget_seconds or "auto"),
            "security_guardrail_defaults": s.security_guardrail_defaults,
            "extra_guardrail_patterns": list(s.extra_guardrail_patterns), "max_explore": s.max_explore,
            "subagent_retry": s.subagent_retry, "subagent_toolsets": s.subagent_toolsets,
        },
        "ladder": report.ladder_rows(s, promotions),
        "promotions": promotions,
        "hold_effective": hold == "on" or (hold == "auto" and not s.ladder.empty),
        "time": {"hook_timeout": timeout, "budget": ladder_mod.ladder_budget(timeout, s.retry_budget_seconds)},
        "inventory": inventory,
        "stats": report.model_stats(attempts),
        "warnings": s.warnings,
        "limits": {"tier_size_hint": _mod("tiers").TIER_SIZE_HINT},
    }


def clean_values(values: Any) -> dict:
    """Validate a settings write. Raises ValueError with a message the page shows."""
    if not isinstance(values, dict):
        raise ValueError("values must be an object")
    out: dict[str, Any] = {}
    for key, value in values.items():
        if key not in _SETTING_KEYS:
            raise ValueError(f"unknown setting {key!r}")
        out[key] = value
    if "tiers" in out:
        tiers = out["tiers"]
        if not isinstance(tiers, list):
            raise ValueError("tiers must be a list of tiers")
        candidate = _mod("candidate")
        seen: set[str] = set()
        cleaned: list[list[str]] = []
        for number, tier in enumerate(tiers, start=1):
            if not isinstance(tier, list):
                raise ValueError(f"tier {number} must be a list of provider/model")
            keep = []
            for entry in tier:
                cand = candidate.parse_candidate(entry)
                if cand is None:
                    raise ValueError(f"tier {number}: can't read {entry!r} (want provider/model)")
                if cand.key in seen:
                    raise ValueError(f"tier {number}: {cand.key} is already on the ladder")
                seen.add(cand.key)
                keep.append(cand.key)
            if keep:
                cleaned.append(keep)
        out["tiers"] = cleaned
    if "mode" in out and out["mode"] not in ("auto", "off"):
        raise ValueError("mode must be auto or off")
    if "subagent_toolsets" in out and out["subagent_toolsets"] not in ("web", "web+file", "inherit"):
        raise ValueError("subagent_toolsets must be web, web+file or inherit")
    if "hold_stream" in out and out["hold_stream"] not in ("auto", "on", "off"):
        raise ValueError("hold_stream must be auto, on or off")
    for key in _BOOL_KEYS:
        if key in out and not isinstance(out[key], bool):
            raise ValueError(f"{key} must be true or false")
    if "retry_budget_seconds" in out:
        text = str(out["retry_budget_seconds"] or "auto").strip().lower()
        if text != "auto":
            try:
                number = float(text)
            except ValueError:
                raise ValueError("retry_budget_seconds must be auto or a number of seconds") from None
            if number <= 0:
                raise ValueError("retry_budget_seconds must be positive")
            text = f"{number:g}"
        out["retry_budget_seconds"] = text
    if "security_roe" in out:
        out["security_roe"] = str(out["security_roe"] or "")[:8000]
    if "extra_guardrail_patterns" in out:
        patterns = out["extra_guardrail_patterns"]
        if isinstance(patterns, str):
            patterns = patterns.splitlines()
        if not isinstance(patterns, list):
            raise ValueError("extra_guardrail_patterns must be a list of phrases")
        out["extra_guardrail_patterns"] = [str(p).strip().lower() for p in patterns if str(p).strip()]
    if "max_explore" in out:
        try:
            out["max_explore"] = max(1, min(8, int(out["max_explore"])))
        except (TypeError, ValueError):
            raise ValueError("max_explore must be a number") from None
    return out


def _save(values: dict) -> None:
    from hermes_cli.plugins_settings import save_plugin_settings  # the writer Desktop's own form uses
    save_plugin_settings(PLUGIN_ID, _ROOT, values)


def _inventory(cfg: dict) -> dict[str, list[str]]:
    try:
        return _mod("cli").inventory(cfg)
    except Exception as exc:
        _log.warning("model-router: provider inventory failed: %s", exc)
        return {}


def _attempts(state: Any) -> list:
    try:
        return _mod("attempts").AttemptLog(Path(state.data_dir) / "attempts.jsonl").recent(2000)
    except Exception as exc:
        _log.warning("model-router: could not read the attempt log: %s", exc)
        return []


@router.get("/state")
def get_state() -> dict:
    cfg = _config()
    state = _plugin_state()
    return build_state(cfg, state, _inventory(cfg), _attempts(state))


@router.put("/settings")
def put_settings(body: dict) -> dict:
    try:
        values = clean_values((body or {}).get("values"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        _save(values)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return get_state()


@router.post("/reset")
def post_reset() -> dict:
    _mod("promote").Promotions(_plugin_state()).clear()
    return get_state()
