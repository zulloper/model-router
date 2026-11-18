"""Model Router for Hermes Agent.

Per turn: classify the task, pick a model + reasoning effort from the user's allowlist, move to the
next allowed model when one is stopped (guardrail, refusal, model error), and learn which model fits
each kind of task. See README.md.

Public plugin API throughout, except the opt-in ``cross_provider`` switch (``private_switch.py``).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    from . import cli, explore, slash
    from .hooks import RouterHooks

    hooks = RouterHooks(ctx)
    router, settings = hooks.router, hooks.settings

    ctx.register_hook("pre_llm_call", hooks.on_pre_llm_call)
    ctx.register_hook("post_llm_call", hooks.on_post_llm_call)
    ctx.register_hook("api_request_error", hooks.on_api_request_error)
    ctx.register_hook("transform_api_error_classification", hooks.on_classify_error)
    hold = False
    try:
        hold = bool(ctx.get_config("hold_stream", False))
    except Exception:
        pass
    ctx.register_hook("transform_llm_output", hooks.on_transform_llm_output, hold_stream=hold)
    ctx.register_middleware("llm_request", hooks.on_llm_request)

    ctx.register_tool(name="route_explore", toolset="model_router", schema=explore.SCHEMA,
                      handler=explore.make_handler(ctx, router, settings))
    ctx.register_command("route", slash.make_handler(ctx, router, settings),
                         description="Model router: status, auto/off, pin a model, stats",
                         args_hint="[status|auto|off|pin <provider/model@effort>|unpin|stats|reset]")
    ctx.register_cli_command(
        name="model-router", help="Model router: allowlist and routes",
        setup_fn=cli.setup_parser, handler_fn=cli.make_handler(ctx),
        description="Check off the providers/models routing may use and the model per task class: "
                    "`hermes model-router setup` (or `show`).")
