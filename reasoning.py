"""Put a reasoning effort on an already-built provider request (``llm_request`` middleware).

Each wire format carries effort under a different key, and only models that support reasoning get
one at all. So this only ever *changes an effort the request already carries*; it never adds a
reasoning block Hermes chose not to send (that could 400 a model without reasoning). The request's
existing block tells us the model accepts one.
"""

from __future__ import annotations

from typing import Any, Optional

# Anthropic adaptive thinking accepts low/medium/high/xhigh/max.
_ANTHROPIC = {"minimal": "low", "low": "low", "medium": "medium", "high": "high",
              "xhigh": "xhigh", "max": "max", "ultra": "max"}
_ANTHROPIC_BUDGET = {"minimal": 2000, "low": 4000, "medium": 8000, "high": 16000,
                     "xhigh": 32000, "max": 48000, "ultra": 64000}


def apply_effort(request: dict, api_mode: str, effort: Optional[str]) -> Optional[str]:
    """Rewrite ``request`` in place. -> a short note of what changed, or None when nothing did."""
    if not effort:
        return None
    mode = (api_mode or "").lower()
    reasoning = request.get("reasoning")
    if mode == "codex_responses" or (isinstance(reasoning, dict) and "effort" in reasoning
                                     and "enabled" not in reasoning):
        if isinstance(reasoning, dict) and "effort" in reasoning:
            if reasoning.get("effort") == effort:
                return None
            request["reasoning"] = {**reasoning, "effort": effort}
            return f"reasoning.effort={effort}"
        return None
    if mode == "anthropic_messages":
        out = request.get("output_config")
        if isinstance(out, dict) and "effort" in out and effort in _ANTHROPIC:
            request["output_config"] = {**out, "effort": _ANTHROPIC[effort]}
            return f"output_config.effort={_ANTHROPIC[effort]}"
        thinking = request.get("thinking")
        if isinstance(thinking, dict) and thinking.get("type") == "enabled" and effort in _ANTHROPIC_BUDGET:
            budget = _ANTHROPIC_BUDGET[effort]
            request["thinking"] = {**thinking, "budget_tokens": budget}
            if isinstance(request.get("max_tokens"), int):
                request["max_tokens"] = max(request["max_tokens"], budget + 4096)
            return f"thinking.budget_tokens={budget}"
        return None
    # chat_completions and friends
    if "reasoning_effort" in request:
        request["reasoning_effort"] = effort
        return f"reasoning_effort={effort}"
    extra = request.get("extra_body")
    if isinstance(extra, dict) and isinstance(extra.get("reasoning"), dict):
        off = effort == "none"
        extra = dict(extra)
        extra["reasoning"] = {**extra["reasoning"], "enabled": not off, "effort": effort}
        request["extra_body"] = extra
        return f"extra_body.reasoning.effort={effort}"
    return None


def set_model(request: dict, model: str) -> bool:
    if not model or request.get("model") == model:
        return False
    request["model"] = model
    return True


def current_effort(request: Any) -> Optional[str]:
    if not isinstance(request, dict):
        return None
    for path in (("reasoning", "effort"), ("output_config", "effort"), ("extra_body", "reasoning", "effort")):
        node: Any = request
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, str):
            return node
    value = request.get("reasoning_effort")
    return value if isinstance(value, str) else None
