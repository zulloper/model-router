"""``/route`` in a chat: see the tier ladder and how it has done, turn it on or off.

    /route                  status: the ladder (with promotions), retry options, time budget, warnings
    /route auto | off       climb the ladder on blocked replies, or don't
    /route stats            per model: recent outcomes as written and enriched, last failure
    /route reset            forget learned promotions (the attempt log is kept)
"""

from __future__ import annotations

from typing import Any

from . import report

USAGE = "Usage: /route [status|auto|off|stats|reset]"


def make_handler(ctx: Any, hooks: Any):
    def handle(raw_args: str = "", *, session_id: str = "", **kwargs: Any) -> str:
        args = (raw_args or "").split()
        cmd = args[0].lower() if args else "status"
        ladder = hooks.ladder
        if cmd in ("status", "show"):
            from . import _hold_stream
            from .ladder import hook_timeout
            return report.describe(hooks.settings(), ladder.promotions.get(),
                                   hold_stream=_hold_stream(ctx), hook_timeout=hook_timeout())
        if cmd in ("auto", "off"):
            ctx.set_config("mode", cmd)
            return f"model-router: {cmd}" + (" — blocked replies stay as they are." if cmd == "off" else "")
        if cmd == "stats":
            attempts = ladder.log.recent(2000) if ladder.log is not None else []
            return report.describe_stats(report.model_stats(attempts), ladder.promotions.get())
        if cmd == "reset":
            ladder.promotions.clear()
            return "Forgot learned promotions; the ladder is back to your tiers. (The attempt log is kept.)"
        return USAGE
    return handle
