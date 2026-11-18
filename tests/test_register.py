"""The plugin's surface: registration, the hold_stream rule, /route, the CLI's show, the manifest."""

import io
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path

import pytest
import yaml

import model_router_plugin as plugin
from model_router_plugin import cli
from model_router_plugin.attempts import Attempt, AttemptLog
from model_router_plugin.settings import LEGACY_KEYS, load_settings
from tests.conftest import FakeCtx

ROOT = Path(__file__).resolve().parents[1]
TIERS = [["openai-codex/gpt-6.2-sol"], ["anthropic/claude-opus-4-8"]]


def test_register_surface(ctx):
    plugin.register(ctx)
    assert set(ctx.hooks) == {"pre_llm_call", "post_api_request", "api_request_error",
                              "transform_api_error_classification", "transform_llm_output"}
    assert ctx.middleware == {}  # 0.4.0 never rewrites the profile model's requests
    assert set(ctx.tools) == {"route_explore", "cve_lookup"}
    assert "route" in ctx.commands and "model-router" in ctx.cli


@pytest.mark.parametrize("settings,held", [
    ({"tiers": TIERS}, True),                          # auto + tiers
    ({}, False),                                       # auto, no tiers
    ({"tiers": TIERS, "hold_stream": "off"}, False),
    ({"hold_stream": "on"}, True),
    ({"tiers": TIERS, "hold_stream": False}, False),   # 0.3.x bool still read
    ({"hold_stream": True}, True),
])
def test_hold_stream_rule(settings, held):
    ctx = FakeCtx(settings)
    plugin.register(ctx)
    assert ctx.hooks["transform_llm_output"][1] is held


def test_guardrail_phrases_become_a_policy_block(ctx):
    plugin.register(ctx)
    classify = ctx.hooks["transform_api_error_classification"][0]
    assert classify(error_message="Output blocked by the content filter")["reason"] == "content_policy_blocked"
    assert classify(error_message="rate limited") is None


def test_route_status_shows_the_ladder_and_off_switch(ctx):
    plugin.register(ctx)
    route = ctx.commands["route"]
    status = route("")
    assert "model-router: auto" in status
    assert "tier 1: openai-codex/gpt-6.2-sol, openai-codex/gpt-mini" in status
    assert "tier 2: anthropic/claude-opus-4-8" in status
    assert "hold_stream on" in status and "Time: hook timeout" in status
    assert route("off").startswith("model-router: off") and ctx.settings["mode"] == "off"
    assert route("nonsense").startswith("Usage")


def test_route_stats_and_reset(ctx, tmp_path):
    from model_router_plugin.hooks import RouterHooks
    hooks = RouterHooks(ctx, AttemptLog(tmp_path / "data" / "attempts.jsonl"))
    for outcome in ("ok", "blocked", "ok"):
        hooks.ladder.log.append(Attempt(turn_id="t", session_id="s", kind="plain", tier=0, index=1,
                                        provider="anthropic", model="claude-opus-4-8", outcome=outcome,
                                        reason="refusal: no" if outcome != "ok" else ""))
    from model_router_plugin import slash
    route = slash.make_handler(ctx, hooks)
    stats = route("stats")
    assert "anthropic/claude-opus-4-8: 2/3 ok, 1 blocked" in stats and "last failure: refusal: no" in stats
    hooks.ladder.promotions.set({"anthropic/claude-opus-4-8": 1})
    assert "↑1" in route("")
    assert "Forgot learned promotions" in route("reset") and hooks.ladder.promotions.get() == {}


def test_no_tiers_means_nothing_to_do(make_ctx):
    ctx = make_ctx({})
    plugin.register(ctx)
    assert "no tiers yet" in ctx.commands["route"]("")
    assert ctx.hooks["transform_llm_output"][0](response_text="I can't help with that.", turn_id="t") is None


def test_legacy_settings_are_flagged_not_silently_ignored():
    cfg = {"scope": {"openai-codex": ["gpt-6.2-sol"]}, "routes": {"code": ["x/y"]}, "classifier": "rules"}
    s = load_settings(lambda key, default=None: cfg.get(key, default))
    assert any("0.3.x settings no longer used: scope, routes, classifier" in w for w in s.warnings)


def test_cli_show_works_without_a_gateway():
    values = {"mode": "off", "tiers": TIERS}
    ctx = Namespace(get_config=lambda key, default=None: values.get(key, default), state=None)
    out = io.StringIO()
    with redirect_stdout(out):
        assert cli.make_handler(ctx)(Namespace(model_router_command="show")) == 0
    text = out.getvalue()
    assert "model-router: off" in text and "tier 2: anthropic/claude-opus-4-8" in text


# --- manifest ---------------------------------------------------------------------------------

MANIFEST = yaml.safe_load((ROOT / "plugin.yaml").read_text())


def test_enum_choices_are_strings():
    for key in ("mode", "hold_stream"):
        choices = MANIFEST["config_schema"][key]["choices"]
        assert all(isinstance(c, str) for c in choices), key  # YAML must not turn "on"/"off" into bools
    assert MANIFEST["config_schema"]["hold_stream"]["choices"] == ["auto", "on", "off"]


def test_manifest_declares_exactly_what_is_registered(ctx):
    plugin.register(ctx)
    assert set(MANIFEST["provides_hooks"]) == set(ctx.hooks)
    assert set(MANIFEST["provides_tools"]) == set(ctx.tools)
    assert "provides_middleware" not in MANIFEST


def test_schema_has_no_legacy_keys_and_settings_reads_every_key():
    schema = set(MANIFEST["config_schema"])
    assert not schema & set(LEGACY_KEYS)
    read = set()
    load_settings(lambda key, default=None: read.add(key) or default)
    assert schema - read == {"hold_stream"}  # read at registration (__init__._hold_stream)
