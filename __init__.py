"""Model Router for Hermes Agent (0.4.3): a tier ladder for blocked replies.

The profile's own model answers every turn. When a reply is blocked — a guardrail, a refusal, an
answer without the code that was asked for, a model error — the ladder's models are asked the same
request, tier by tier: as written, then enriched; in parallel within a tier, good answers combined.
The first usable answer replaces the blocked one. Models that keep winning move up. See README.md
and DESIGN.md.

Public plugin API only.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    from . import cli, explore, nvd, slash
    from .hooks import RouterHooks

    hooks = RouterHooks(ctx)

    ctx.register_hook("pre_llm_call", hooks.on_pre_llm_call)
    ctx.register_hook("post_api_request", hooks.on_post_api_request)
    ctx.register_hook("api_request_error", hooks.on_api_request_error)
    ctx.register_hook("transform_api_error_classification", hooks.on_classify_error)
    ctx.register_hook("transform_llm_output", hooks.on_transform_llm_output, hold_stream=_hold_stream(ctx))

    ctx.register_tool(name="route_explore", toolset="model_router", schema=explore.SCHEMA,
                      handler=explore.make_handler(ctx, hooks.settings))
    ctx.register_tool(name="cve_lookup", toolset="model_router", schema=nvd.SCHEMA,
                      handler=nvd.make_handler())
    ctx.register_command("route", slash.make_handler(ctx, hooks),
                         description="Model router: the tier ladder, stats, on/off",
                         args_hint="[status|auto|off|stats|reset]")
    ctx.register_cli_command(
        name="model-router", help="Model router: the tier ladder",
        setup_fn=cli.setup_parser, handler_fn=cli.make_handler(ctx),
        description="Put models on the tier ladder a blocked reply climbs: "
                    "`hermes model-router setup` (or `show`).")


def _hold_stream(ctx) -> bool:
    """``hold_stream``: auto (the default) is on exactly when a tier ladder is configured (DESIGN D4) —
    replies that end on a provider error reach ``transform_llm_output`` only when held."""
    try:
        from .settings import hold_stream_setting
        choice = hold_stream_setting(ctx.get_config("hold_stream", None))
        if choice == "auto":
            from .tiers import parse_tiers
            return not parse_tiers(ctx.get_config("tiers", [])).empty
        return choice == "on"
    except Exception:
        return False
