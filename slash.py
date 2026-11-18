"""``/route`` in a chat: see what the router does and steer it.

    /route                  status: mode, allowlist, routes, this chat's last decision
    /route auto | off       turn routing on or off (all sessions)
    /route pin <p/m@effort> use one allowed model for this chat;  /route unpin
    /route prefer <class> <p/m> | clear <class>   set or clear a learned preference
    /route sec on | off     force security mode for this chat (sec class + preamble)
    /route stats            per-model successes, failures, cool-downs
    /route reset            forget stats and learned preferences
"""

from __future__ import annotations

import time
from typing import Any

from .router import Router, Settings
from .scope import TASK_CLASSES, fallback_warnings, parse_candidate

USAGE = ("Usage: /route [status|auto|off|pin <provider/model[@effort]>|unpin|"
         "prefer <class> <provider/model>|clear <class>|sec on|off|stats|reset]")


def describe(s: Settings, router: Router, session_id: str = "", config: Any = None) -> str:
    lines = [f"model-router: {s.mode}" + ("" if s.active or s.mode == "off" else " (no allowlist yet)"),
             f"cross-provider switching: {'on (Hermes internals)' if s.cross_provider else 'off (public API)'}",
             f"classifier: {s.classifier}",
             f"security mode: {'on (sec class + preamble)' if router.sec_on(session_id) else 'off'}",
             f"guardrail round-trip: {'on' if s.security_sanitize else 'off'}"
             f"{f', starting L{router.level_for(session_id)}' if router.level_for(session_id) else ''}",
             "", "Allowlist:"]
    if s.scope.empty:
        lines.append("  (empty) — run `hermes model-router setup` in a terminal")
    for provider, models in s.scope.providers.items():
        lines.append(f"  {provider}: {', '.join(models)}")
    lines += ["", "Routes:"]
    prefs = router.preferences()
    for cls in TASK_CLASSES:
        order = router.order_for(s, cls, session_id=session_id)
        pref = f"  ★ learned: {prefs[cls]}" if cls in prefs else ""
        lines.append(f"  {cls:<8} {' → '.join(str(c) for c in order) or '-'}{pref}")
    pin = router.pinned(session_id)
    if pin is not None:
        lines += ["", f"Pinned in this chat: {pin}"]
    last = router.last_turn(session_id)
    if last is not None:
        lines += ["", f"Last turn: {last.task_class}, on {last.current or '-'}"]
        lines += [f"  {e}" for e in last.events[-6:]]
    warnings = list(s.warnings)
    if isinstance(config, dict):
        warnings += fallback_warnings(config, s.scope)
    if warnings:
        lines += ["", "Warnings:"] + [f"  ⚠ {w}" for w in warnings]
    return "\n".join(lines)


def stats(router: Router, s: Settings) -> str:
    health = router.health()
    if not health:
        return "No outcomes recorded yet."
    now = time.time()
    lines = ["model                                     ok  fail  streak  status"]
    for key, h in sorted(health.items()):
        cand = parse_candidate(key)
        cooling = cand is not None and router.cooling(cand, s)
        left = int((s.cooldown_minutes * 60 - (now - float(h.get("last_fail", 0)))) / 60) if cooling else 0
        status = f"cooling down ~{left}m" if cooling else "ok"
        lines.append(f"{key:<40} {h.get('ok', 0):>3} {h.get('fail', 0):>5} {h.get('streak', 0):>7}  {status}")
        if h.get("last_reason"):
            lines.append(f"{'':<42}last failure: {h['last_reason']}")
    return "\n".join(lines)


def make_handler(ctx: Any, router: Router, settings_fn):
    def handle(raw_args: str = "", *, session_id: str = "", **kwargs: Any) -> str:
        args = (raw_args or "").split()
        cmd = args[0].lower() if args else "status"
        s: Settings = settings_fn()
        if cmd in ("status", "show"):
            try:
                from hermes_cli.config import load_config
                config = load_config() or {}
            except Exception:
                config = None
            return describe(s, router, session_id, config)
        if cmd in ("auto", "off"):
            ctx.set_config("mode", cmd)
            return f"model-router: {cmd}" + (" — models stay as they are." if cmd == "off" else "")
        if cmd == "pin":
            cand = parse_candidate(args[1]) if len(args) > 1 else None
            if cand is None:
                return "Usage: /route pin <provider/model[@effort]>"
            if not s.scope.allows_candidate(cand):
                return f"{cand.key} is not in the allowlist. Allowed: " + ", ".join(
                    c.key for c in s.scope.all_models())
            router.pin(session_id, cand)
            return f"This chat now uses {cand} for every turn (/route unpin to go back to auto)."
        if cmd == "unpin":
            router.pin(session_id, None)
            return "Unpinned: routing picks per turn again."
        if cmd == "prefer":
            if len(args) < 3 or args[1] not in TASK_CLASSES:
                return f"Usage: /route prefer <{'|'.join(TASK_CLASSES)}> <provider/model[@effort]>"
            cand = parse_candidate(args[2])
            if cand is None or not s.scope.allows_candidate(cand):
                return f"{args[2]} is not in the allowlist."
            router.prefer(args[1], cand)
            return f"{args[1]} tasks now start on {cand}."
        if cmd == "clear":
            if len(args) < 2 or args[1] not in TASK_CLASSES:
                return f"Usage: /route clear <{'|'.join(TASK_CLASSES)}>"
            router.prefer(args[1], None)
            return f"Cleared the learned preference for {args[1]}."
        if cmd == "sec":
            want = args[1].lower() if len(args) > 1 else "on"
            if want not in ("on", "off"):
                return "Usage: /route sec on|off"
            router.set_sec(session_id, want == "on")
            return ("Security mode on for this chat: every turn is classified as sec (security "
                    "preamble, code-required answer checks). /route sec off to stop."
                    if want == "on" else "Security mode off for this chat.")
        if cmd == "stats":
            return stats(router, s)
        if cmd == "reset":
            router.reset_stats()
            return "Forgot all model stats and learned preferences."
        if cmd == "explore":
            return ("Exploring runs inside a turn. Ask the agent, e.g. \"여러 모델로 이 작업을 비교해서 "
                    "가장 잘 맞는 모델을 찾아줘: <task>\" — it calls the route_explore tool.")
        return USAGE
    return handle
