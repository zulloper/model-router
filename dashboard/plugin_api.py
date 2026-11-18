"""Model Router backend for the Desktop settings pages, mounted at ``/api/plugins/model-router/``.

The web server scopes every request to the profile the Desktop has active (``HERMES_HOME``), so
each route reads and writes *that* profile's settings and state.

Routes:
    GET  /state      settings, provider/model inventory, routes as the router sees them, warnings,
                     per-model health and learned preferences
    PUT  /settings   {"values": {...}} — validated, then written with Hermes' plugin-settings writer
    POST /prefer     {"task_class": "deep", "candidate": "provider/model@effort" | null}
    POST /reset      forget per-model stats and learned preferences
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

_log = logging.getLogger(__name__)
router = APIRouter()

PLUGIN_ID = "model-router"
_ROOT = Path(__file__).resolve().parents[1]
_PKG = "hermes_dashboard_plugin_model_router"
_SETTING_KEYS = ("mode", "scope", "routes", "cross_provider", "classifier", "soft_refusal_retry",
                 "cooldown_minutes", "max_explore")


def _pkg():
    """The plugin package itself (scope/router/cli), loaded by path: the web server imports this file
    on its own, so relative imports are not available here."""
    mod = sys.modules.get(_PKG)
    if mod is None:
        spec = importlib.util.spec_from_file_location(_PKG, _ROOT / "__init__.py",
                                                      submodule_search_locations=[str(_ROOT)])
        mod = importlib.util.module_from_spec(spec)
        sys.modules[_PKG] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception:
            sys.modules.pop(_PKG, None)
            raise
    return (importlib.import_module(f"{_PKG}.scope"), importlib.import_module(f"{_PKG}.router"),
            importlib.import_module(f"{_PKG}.cli"))


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


def build_state(cfg: dict, state: Any, inventory: dict[str, list[str]]) -> dict:
    scope_mod, router_mod, _ = _pkg()
    raw = _settings_raw(cfg)
    settings = router_mod.load_settings(lambda key, default=None: raw.get(key, default))
    health = state.get("health", default={}) or {}
    preferred = state.get("preferred", default={}) or {}
    now = time.time()
    cooling = {}
    for key, h in health.items():
        streak, last = int(h.get("streak", 0)), float(h.get("last_fail", 0.0))
        left = settings.cooldown_minutes * 60 - (now - last)
        if streak >= 2 and left > 0:
            cooling[key] = int(left // 60) + 1
    model = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    return {
        "plugin_enabled": _enabled(cfg),
        "current": {"provider": str(model.get("provider") or ""), "model": str(model.get("default") or "")},
        "settings": {
            "mode": settings.mode, "cross_provider": settings.cross_provider,
            "classifier": settings.classifier, "soft_refusal_retry": settings.soft_refusal_retry,
            "cooldown_minutes": settings.cooldown_minutes, "max_explore": settings.max_explore,
            "scope": settings.scope.providers,
            "routes": {cls: [str(c) for c in raw_routes]
                       for cls, raw_routes in (raw.get("routes") or {}).items()
                       if isinstance(raw_routes, list)},
        },
        "effective_routes": {cls: [str(c) for c in cands] for cls, cands in settings.routes.items()},
        "inventory": inventory,
        "warnings": settings.warnings + scope_mod.fallback_warnings(cfg, settings.scope),
        "health": health, "preferred": preferred, "cooling": cooling,
        "limits": {"max_providers": scope_mod.MAX_PROVIDERS, "task_classes": list(scope_mod.TASK_CLASSES),
                   "efforts": [e for e in scope_mod.EFFORTS if e != "none"],
                   "default_effort": scope_mod.DEFAULT_EFFORT},
    }


def clean_values(values: Any) -> dict:
    """Validate a settings write. Raises ValueError with a message the page shows."""
    scope_mod, _, _ = _pkg()
    if not isinstance(values, dict):
        raise ValueError("values must be an object")
    out: dict[str, Any] = {}
    for key, value in values.items():
        if key not in _SETTING_KEYS:
            raise ValueError(f"unknown setting {key!r}")
        out[key] = value
    if "scope" in out:
        parsed = scope_mod.parse_scope(out["scope"])
        if parsed.warnings:
            raise ValueError("; ".join(parsed.warnings))
        out["scope"] = parsed.providers
    if "routes" in out:
        routes = out["routes"]
        if not isinstance(routes, dict):
            raise ValueError("routes must be an object")
        scope = scope_mod.parse_scope(out.get("scope", _settings_raw(_config()).get("scope", {})))
        cleaned = {}
        for cls, entries in routes.items():
            if cls not in scope_mod.TASK_CLASSES:
                raise ValueError(f"unknown task class {cls!r}")
            keep = []
            for entry in entries or []:
                cand = scope_mod.parse_candidate(entry)
                if cand is None:
                    raise ValueError(f"routes.{cls}: can't read {entry!r}")
                if not scope.allows_candidate(cand):
                    raise ValueError(f"routes.{cls}: {cand.key} is not in the allowlist")
                keep.append(str(cand))
            if keep:
                cleaned[cls] = keep
        out["routes"] = cleaned
    if "mode" in out and out["mode"] not in ("auto", "off"):
        raise ValueError("mode must be auto or off")
    if "classifier" in out and out["classifier"] not in ("rules", "hybrid", "llm"):
        raise ValueError("classifier must be rules, hybrid or llm")
    for key in ("cross_provider", "soft_refusal_retry"):
        if key in out and not isinstance(out[key], bool):
            raise ValueError(f"{key} must be true or false")
    for key, low, high in (("cooldown_minutes", 0, 1440), ("max_explore", 1, 8)):
        if key in out:
            try:
                out[key] = max(low, min(high, int(out[key])))
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a number") from None
    return out


def _save(values: dict) -> None:
    from hermes_cli.plugins_settings import save_plugin_settings  # the writer Desktop's own form uses
    save_plugin_settings(PLUGIN_ID, _ROOT, values)


def _inventory(cfg: dict) -> dict[str, list[str]]:
    _, _, cli = _pkg()
    try:
        return cli.inventory(cfg)
    except Exception as exc:
        _log.warning("model-router: provider inventory failed: %s", exc)
        return {}


@router.get("/state")
def get_state() -> dict:
    cfg = _config()
    return build_state(cfg, _plugin_state(), _inventory(cfg))


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


@router.post("/prefer")
def post_prefer(body: dict) -> dict:
    scope_mod, router_mod, _ = _pkg()
    cls = (body or {}).get("task_class")
    if cls not in scope_mod.TASK_CLASSES:
        raise HTTPException(status_code=400, detail="unknown task class")
    state = _plugin_state()
    preferred = dict(state.get("preferred", default={}) or {})
    raw = (body or {}).get("candidate")
    if raw:
        cand = scope_mod.parse_candidate(raw)
        scope = scope_mod.parse_scope(_settings_raw(_config()).get("scope", {}))
        if cand is None or not scope.allows_candidate(cand):
            raise HTTPException(status_code=400, detail=f"{raw} is not in the allowlist")
        preferred[cls] = str(cand)
    else:
        preferred.pop(cls, None)
    state.set("preferred", preferred)
    return get_state()


@router.post("/reset")
def post_reset() -> dict:
    state = _plugin_state()
    state.set("health", {})
    state.set("preferred", {})
    return get_state()
