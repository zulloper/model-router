"""``hermes model-router setup | show``: put models on the tier ladder.

``setup`` asks, tier by tier, which models a blocked reply should try next (a checklist of the
providers' models that have credentials, or a typed ``provider/model``). Checking nothing ends the
ladder. ``show`` prints the current ladder and needs no terminal.

Menus use Hermes' curses components when available, else numbered prompts.
"""

from __future__ import annotations

import sys
from typing import Any, Callable, Iterable, Optional

from . import report
from .candidate import parse_candidate
from .settings import load_settings

MAX_WIZARD_TIERS = 4
_NO_TTY = ("`hermes model-router setup` needs an interactive terminal.\n"
           "Print the current ladder with `hermes model-router show`, or edit\n"
           "plugins.entries.model-router.settings.tiers in config.yaml.")


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
    current = load_settings(get).ladder
    inv = inventory(cfg)
    known = [f"{p}/{m}" for p, models in inv.items() for m in models]
    known += [c.key for c in current.models() if c.key not in known]
    if not known:
        print("Hermes has no providers with credentials yet. Run `hermes model` first.")
        return
    print("A blocked reply climbs the ladder: tier 1's models are asked first (in parallel), then tier 2, …\n"
          "Within a tier the order below is the priority. Check nothing to end the ladder.")
    tiers: list[list[str]] = []
    placed: set[str] = set()
    for number in range(1, MAX_WIZARD_TIERS + 1):
        free = [k for k in known if k not in placed]
        was = [c.key for c in current.tiers[number - 1]] if number <= len(current.tiers) else []
        items = free + ["+ type provider/model"]
        marks = _checklist(f"Tier {number} · models to ask {'first' if number == 1 else 'next'}", items,
                           {i for i, k in enumerate(free) if k in was})
        if marks is None:
            print("No change.")
            return
        chosen = [free[i] for i in sorted(marks) if i < len(free)]
        if len(free) in marks:
            typed = input("provider/model ids (comma-separated): ").strip()
            for raw in typed.split(","):
                cand = parse_candidate(raw)
                if cand is not None and cand.key not in placed and cand.key not in chosen:
                    chosen.append(cand.key)
        if not chosen:
            break
        tiers.append(chosen)
        placed.update(chosen)
    if not tiers:
        print("No model checked; nothing saved.")
        return
    set_("tiers", tiers)
    if str(get("mode", "auto")) not in ("auto", "off"):
        set_("mode", "auto")
    print("\nSaved. A running gateway reads tiers on the next turn; hold_stream (auto) changes on restart.\n")
    print(report.describe(load_settings(get), {}))


def setup_parser(parser) -> None:
    sub = parser.add_subparsers(dest="model_router_command", metavar="<command>")
    sub.add_parser("setup", help="Put models on the tier ladder")
    sub.add_parser("show", help="Print the tier ladder")


def _promotions(ctx: Any) -> dict:
    try:
        from .promote import Promotions
        return Promotions(getattr(ctx, "state", None)).get()
    except Exception:
        return {}


def make_handler(ctx: Any):
    def handle(args) -> int:
        cmd = getattr(args, "model_router_command", None)
        if cmd == "show":
            from .ladder import hook_timeout
            print(report.describe(load_settings(ctx.get_config), _promotions(ctx), hook_timeout=hook_timeout()))
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
