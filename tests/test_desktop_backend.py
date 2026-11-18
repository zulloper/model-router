"""dashboard/plugin_api.py: the backend behind the Desktop settings pages (needs fastapi)."""

import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("hermes_cli.config")

_API = Path(__file__).resolve().parents[1] / "dashboard" / "plugin_api.py"


@pytest.fixture
def api():
    spec = importlib.util.spec_from_file_location("model_router_dashboard_api", _API)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    for name in [n for n in sys.modules if n.startswith("hermes_dashboard_plugin_model_router")]:
        sys.modules.pop(name, None)


class State:
    def __init__(self, data=None, data_dir=None):
        self.data = dict(data or {})
        self.data_dir = data_dir

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


CFG = {
    "model": {"provider": "openai-codex", "default": "gpt-6.1-sol-900k"},
    "plugins": {"enabled": ["model-router"], "hook_callback_timeout": 180, "entries": {"model-router": {"settings": {
        "tiers": [["openai-codex/gpt-6.2-sol"], ["anthropic/claude-opus-4-8", "xai/grok-5"]],
        "scope": {"openai-codex": ["gpt-6.2-sol"]},          # 0.3.x leftover -> warning
    }}}},
}


def _attempts(api, tmp_path):
    attempts = api._mod("attempts")
    log = attempts.AttemptLog(tmp_path / "attempts.jsonl")
    for outcome in ("ok", "blocked", "ok"):
        log.append(attempts.Attempt(turn_id="t", session_id="s", kind="plain", tier=1, index=1,
                                    provider="anthropic", model="claude-opus-4-8", outcome=outcome))
    return log.recent()


def test_build_state(api, tmp_path):
    state = State({"promotions": {"xai/grok-5": 1}})
    out = api.build_state(CFG, state, {"openai-codex": ["gpt-6.2-sol"]}, _attempts(api, tmp_path))
    assert out["schema"] == api.STATE_SCHEMA  # the Desktop page refuses any other shape
    assert out["plugin_enabled"] is True
    assert out["settings"]["tiers"] == [["openai-codex/gpt-6.2-sol"], ["anthropic/claude-opus-4-8", "xai/grok-5"]]
    assert out["ladder"] == [[{"key": "openai-codex/gpt-6.2-sol", "promoted": 0}, {"key": "xai/grok-5", "promoted": 1}],
                             [{"key": "anthropic/claude-opus-4-8", "promoted": 0}]]
    assert out["settings"]["hold_stream"] == "auto" and out["hold_effective"] is True
    assert out["time"] == {"hook_timeout": 180.0, "budget": 176.0}
    assert out["stats"]["anthropic/claude-opus-4-8"]["plain"]["ok"] == 2
    assert any("0.3.x settings no longer used: scope" in w for w in out["warnings"])


def test_clean_values(api):
    out = api.clean_values({"tiers": [["openai-codex/gpt-6.2-sol@high"], [], ["anthropic/claude-opus-4-8"]],
                            "hold_stream": "on", "retry_budget_seconds": " 120 ", "max_explore": "20",
                            "security_roe": "x" * 9000, "extra_guardrail_patterns": [" Corp-Guard ", ""],
                            "parallel": False})
    assert out["tiers"] == [["openai-codex/gpt-6.2-sol"], ["anthropic/claude-opus-4-8"]]  # effort dropped, empty tier gone
    assert out["retry_budget_seconds"] == "120" and out["max_explore"] == 8
    assert len(out["security_roe"]) == 8000 and out["extra_guardrail_patterns"] == ["corp-guard"]
    for bad, msg in (({"evil": 1}, "unknown setting"), ({"mode": "sometimes"}, "mode"),
                     ({"hold_stream": True}, "hold_stream"), ({"parallel": "yes"}, "parallel"),
                     ({"retry_budget_seconds": "soon"}, "retry_budget_seconds"),
                     ({"retry_budget_seconds": "-5"}, "positive"),
                     ({"tiers": [["a/b"], ["a/b"]]}, "already on the ladder"),
                     ({"tiers": [["no-slash"]]}, "can't read"), ({"tiers": "a/b"}, "list of tiers"),
                     ({"scope": {}}, "unknown setting")):
        with pytest.raises(ValueError, match=msg):
            api.clean_values(bad)


def test_routes_over_http(api, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    state, saved = State({"promotions": {"xai/grok-5": 1}}, data_dir=tmp_path), {}
    monkeypatch.setattr(api, "_config", lambda: CFG)
    monkeypatch.setattr(api, "_plugin_state", lambda: state)
    monkeypatch.setattr(api, "_inventory", lambda cfg: {"openai-codex": ["gpt-6.2-sol"]})
    monkeypatch.setattr(api, "_save", lambda values: saved.update(values))
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)

    body = client.get("/state").json()
    assert body["inventory"] == {"openai-codex": ["gpt-6.2-sol"]} and body["stats"] == {}
    r = client.put("/settings", json={"values": {"tiers": [["openai-codex/gpt-6.2-sol"]], "mode": "off"}})
    assert r.status_code == 200 and saved == {"tiers": [["openai-codex/gpt-6.2-sol"]], "mode": "off"}
    assert client.put("/settings", json={"values": {"mode": "x"}}).status_code == 400
    assert client.post("/prefer", json={}).status_code in (404, 405)  # 0.3.x route is gone
    client.post("/reset")
    assert state.data["promotions"] == {}


def test_desktop_and_gateway_share_promotions(api, ctx):
    """The Desktop writes plugin state through PluginState(id); the gateway reads ctx.state. Both
    resolve to the same plugin-data folder, so a reset on the page reaches the ladder."""
    import model_router_plugin as plugin
    from model_router_plugin.hooks import RouterHooks
    hooks = RouterHooks(ctx)
    ctx.state.set("promotions", {"anthropic/claude-opus-4-8": 1})
    assert hooks.ladder.promotions.get() == {"anthropic/claude-opus-4-8": 1}
    api._mod("promote").Promotions(ctx.state).clear()          # what POST /reset does
    assert hooks.ladder.promotions.get() == {}
    assert plugin  # imported for its side effect of loading the package


def test_desktop_page_expects_the_backends_schema(api):
    page = (Path(__file__).resolve().parents[1] / "desktop" / "plugin.js").read_text()
    assert f"const STATE_SCHEMA = {api.STATE_SCHEMA}" in page
