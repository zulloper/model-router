"""Opt-in (``cross_provider: true``): move the live session to another allowed provider.

The public plugin API can only change the model *within* the provider a request is already bound
to. Switching provider needs the running agent, which this module reaches through Hermes internals:

* ``agent.subagent_lifecycle.get_active_subagent_parent()`` (public module) returns the live agent
  while a turn runs, including inside ``pre_llm_call``.
* ``hermes_cli.model_switch.switch_model`` (private) resolves the target's credentials and endpoint
  the same way ``/model --provider`` does, and ``AIAgent.switch_model`` (private) swaps the agent.

Every step is guarded: a missing internal, ``plugins.isolation: host``, or a failed resolve logs one
warning and the turn continues on the current provider with public routing.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .scope import Candidate

logger = logging.getLogger(__name__)
_warned: set[str] = set()


def _warn_once(key: str, msg: str, *args: Any) -> None:
    if key not in _warned:
        _warned.add(key)
        logger.warning(msg, *args)


def live_agent() -> Any:
    try:
        from agent.subagent_lifecycle import get_active_subagent_parent
    except Exception as exc:
        _warn_once("import", "model-router: cross-provider switching unavailable (%s)", exc)
        return None
    try:
        return get_active_subagent_parent()
    except Exception as exc:
        _warn_once("parent", "model-router: no live agent for cross-provider switching (%s)", exc)
        return None


def current_route(agent: Any) -> tuple[str, str]:
    return (str(getattr(agent, "provider", "") or "").lower(), str(getattr(agent, "model", "") or ""))


def switch_to(agent: Any, cand: Candidate) -> Optional[str]:
    """Move ``agent`` to ``cand``. -> None on success, else why it did not happen."""
    if agent is None or not callable(getattr(agent, "switch_model", None)):
        return "no live agent (isolated plugin host or an older Hermes)"
    provider, model = current_route(agent)
    if provider == cand.provider and model == cand.model:
        return None
    try:
        from hermes_cli.config import load_config
        from hermes_cli.model_switch import switch_model as resolve_switch  # private
    except Exception as exc:
        _warn_once("resolve-import", "model-router: Hermes has no model_switch.switch_model (%s)", exc)
        return f"Hermes internals changed: {exc}"
    try:
        cfg = load_config() or {}
        user_providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else None
        custom = cfg.get("custom_providers") if isinstance(cfg.get("custom_providers"), list) else None
        result = resolve_switch(
            raw_input=cand.model, current_provider=provider, current_model=model,
            current_base_url=str(getattr(agent, "base_url", "") or ""), is_global=False,
            explicit_provider=cand.provider, user_providers=user_providers, custom_providers=custom)
    except Exception as exc:
        return f"could not resolve {cand.key}: {exc}"
    if not getattr(result, "success", False):
        return getattr(result, "error_message", "") or f"could not resolve {cand.key}"
    try:
        agent.switch_model(new_model=result.new_model, new_provider=result.target_provider,
                           api_key=result.api_key or "", base_url=result.base_url or "",
                           api_mode=result.api_mode or "")
    except Exception as exc:
        return f"switch to {cand.key} failed: {exc}"
    return None


def set_effort(agent: Any, effort: Optional[str]) -> bool:
    """Set the live agent's reasoning effort (read fresh on every request)."""
    if agent is None or not effort or not hasattr(agent, "reasoning_config"):
        return False
    try:
        agent.reasoning_config = ({"enabled": False} if effort == "none"
                                  else {"enabled": True, "effort": effort})
        return True
    except Exception as exc:
        _warn_once("effort", "model-router: could not set reasoning effort (%s)", exc)
        return False
