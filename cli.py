"""``hermes model-router setup | show``: check off the allowlist and build the routes.

``setup`` walks three steps in the terminal:

1. Providers: a checklist of providers that have credentials (plus the ones config.yaml names);
   at most 3 may be checked.
2. Models: for each checked provider, a checklist of its models; a model can also be typed in.
3. Routes: for each task class, the model to try first and its reasoning effort. The other allowed
   models follow as backups.

Menus use Hermes' curses components when available, else numbered prompts. ``show`` prints the
current setup and needs no terminal.
"""

from __future__ import annotations

import sys
from typing import Any, Callable, Iterable, Optional

from .router import load_settings
from .scope import DEFAULT_EFFORT, EFFORTS, MAX_PROVIDERS, TASK_CLASSES, parse_scope
from .slash import describe

_NO_TTY = ("`hermes model-router setup` needs an interactive terminal.\n"
           "Print the current setup with `hermes model-router show`, or edit\n"
           "plugins.entries.model-router.settings.scope / routes in config.yaml.")
_CLASS_HELP = {
    "quick": "short lookups, translation, summaries",
    "standard": "everyday writing and explanations",
    "code": "writing, fixing and reviewing code",
    "deep": "hard reasoning, design, analysis, long tasks",
}


# --- menus ------------------------------------------------------------------------------------

def _checklist(title: str, items: list[str], selected: set[int]) -> Optional[set[int]]:
    try:
        from hermes_cli.curses_ui import curses_checklist
        return set(curses_checklist(title, items, selected, cancel_returns=None))  # type: ignore[arg-type]
    except Exception:
        pass
    print(f"\n{title}")
    for i, item in enumerate(items, 1):
        print(f"  {i:>2}. [{'x' if i - 1 in selected else ' '}] {item}")
    raw = input("Numbers to check, comma-separated (Enter keeps the marks, q cancels): ").strip()
    if raw.lower() == "q":
        return None
    if not raw:
        return set(selected)
    out = set()
    for part in raw.replace(" ", "").split(","):
        if part.isdigit() and 1 <= int(part) <= len(items):
            out.add(int(part) - 1)
    return out


def _radio(title: str, items: list[str], selected: int = 0) -> int:
    try:
        from hermes_cli.curses_ui import curses_radiolist
        return int(curses_radiolist(title, items, selected=selected, cancel_returns=-1))
    except Exception:
        pass
    print(f"\n{title}")
    for i, item in enumerate(items, 1):
        print(f"  {i:>2}. {'(•)' if i - 1 == selected else '( )'} {item}")
    raw = input(f"Number (Enter = {selected + 1}, q cancels): ").strip()
    if raw.lower() == "q":
        return -1
    if not raw:
        return selected
    return int(raw) - 1 if raw.isdigit() and 1 <= int(raw) <= len(items) else selected


# --- inventory --------------------------------------------------------------------------------

def _config() -> dict:
    try:
        from hermes_cli.config import load_config
        return load_config() or {}
    except Exception:
        return {}


def inventory(cfg: dict) -> dict[str, list[str]]:
    """provider slug -> known models: providers with credentials (Hermes' own picker inventory,
    when this Hermes exposes it) plus every provider/model config.yaml mentions."""
    inv: dict[str, list[str]] = {}

    def add(provider: Any, models: Iterable[Any]) -> None:
        slug = str(provider or "").strip().lower()
        if not slug:
            return
        bucket = inv.setdefault(slug, [])
        for m in models:
            m = str(m or "").strip()
            if m and m not in bucket:
                bucket.append(m)

    model = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    add(model.get("provider"), [model.get("default") or model.get("model")])
    chain = cfg.get("fallback_providers") or []
    for entry in chain if isinstance(chain, list) else []:
        if isinstance(entry, dict):
            add(entry.get("provider"), [entry.get("model")])
    providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
    for slug, entry in providers.items():
        if isinstance(entry, dict):
            models = entry.get("models") or []
            add(slug, list(models.keys()) if isinstance(models, dict) else models)
            add(slug, [entry.get("default_model")])
    try:
        from hermes_cli.model_switch_providers import list_authenticated_providers  # private, optional
        for row in list_authenticated_providers(
                current_provider=str(model.get("provider") or ""), user_providers=providers or None,
                max_models=40, for_picker=True, probe_custom_providers=False, non_blocking_catalogs=True):
            add(row.get("slug"), row.get("models") or [])
    except Exception:
        pass
    return inv


# --- the wizard -------------------------------------------------------------------------------

def run(get: Callable[[str, Any], Any], set_: Callable[[str, Any], None]) -> None:
    cfg = _config()
    current = parse_scope(get("scope", {}))
    inv = inventory(cfg)
    for slug, models in current.providers.items():
        inv.setdefault(slug, [])
        inv[slug] += [m for m in models if m not in inv[slug]]
    if not inv:
        print("Hermes has no providers with credentials yet. Run `hermes model` first.")
        return
    slugs = list(inv)
    picked = _checklist(f"Step 1/3 · Providers routing may use (at most {MAX_PROVIDERS})",
                        [f"{s}  ({len(inv[s])} models)" for s in slugs],
                        {i for i, s in enumerate(slugs) if s in current.providers})
    if picked is None:
        print("No change.")
        return
    chosen = [slugs[i] for i in sorted(picked)]
    if not chosen:
        print("No provider checked; nothing saved.")
        return
    if len(chosen) > MAX_PROVIDERS:
        print(f"Only {MAX_PROVIDERS} providers are allowed; keeping {', '.join(chosen[:MAX_PROVIDERS])}.")
        chosen = chosen[:MAX_PROVIDERS]

    scope: dict[str, list[str]] = {}
    for slug in chosen:
        models = list(inv[slug])
        already = set(current.providers.get(slug, []))
        marks = _checklist(f"Step 2/3 · {slug}: models routing may use", models + ["+ type a model id"],
                           {i for i, m in enumerate(models) if m in already})
        if marks is None:
            print("No change.")
            return
        sel = [models[i] for i in sorted(marks) if i < len(models)]
        if len(models) in marks:
            typed = input(f"Model ids for {slug} (comma-separated): ").strip()
            sel += [m.strip() for m in typed.split(",") if m.strip() and m.strip() not in sel]
        if sel:
            scope[slug] = sel
    if not scope:
        print("No model checked; nothing saved.")
        return

    allowed = [f"{p}/{m}" for p, models in scope.items() for m in models]
    routes: dict[str, list[str]] = {}
    old_routes = get("routes", {}) or {}
    for cls in TASK_CLASSES:
        first_default = 0
        old = (old_routes.get(cls) or [None])[0] if isinstance(old_routes, dict) else None
        if isinstance(old, str) and old.split("@")[0] in allowed:
            first_default = allowed.index(old.split("@")[0])
        idx = _radio(f"Step 3/3 · {cls} tasks ({_CLASS_HELP[cls]}): try which model first?", allowed,
                     first_default)
        if idx < 0:
            print("No change.")
            return
        efforts = [e for e in EFFORTS if e != "none"]
        e_idx = _radio(f"{cls}: reasoning effort for {allowed[idx]}", efforts,
                       efforts.index(DEFAULT_EFFORT[cls]))
        effort = efforts[e_idx] if e_idx >= 0 else DEFAULT_EFFORT[cls]
        others = [a for a in allowed if a != allowed[idx]]
        routes[cls] = [f"{allowed[idx]}@{effort}"] + [f"{a}@{effort}" for a in others]

    set_("scope", scope)
    set_("routes", routes)
    if str(get("mode", "auto")) not in ("auto", "off"):
        set_("mode", "auto")
    print("\nSaved. New sessions pick this up; a running gateway reads it on the next turn.\n")
    print(describe(load_settings(get), _NullRouter(), "", cfg))


class _NullRouter:
    """``describe`` without live state (the CLI runs outside the gateway)."""

    def preferences(self) -> dict:
        return {}

    def order_for(self, s, cls, session_id: str = "", **_: Any):
        return list(s.routes.get(cls) or [])

    def pinned(self, session_id: str):
        return None

    def last_turn(self, session_id: str):
        return None


def setup_parser(parser) -> None:
    sub = parser.add_subparsers(dest="model_router_command", metavar="<command>")
    sub.add_parser("setup", help="Check off allowed providers/models and pick the route per task class")
    sub.add_parser("show", help="Print the allowlist and routes")


def make_handler(ctx: Any):
    def handle(args) -> int:
        cmd = getattr(args, "model_router_command", None)
        if cmd == "show":
            print(describe(load_settings(ctx.get_config), _NullRouter(), "", _config()))
            return 0
        if cmd != "setup":
            print("usage: hermes model-router {setup,show}")
            return 0
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            print(_NO_TTY, file=sys.stderr)
            return 1
        try:
            run(ctx.get_config, ctx.set_config)
        except (KeyboardInterrupt, EOFError):
            print("\nNo change.")
            return 1
        return 0
    return handle
