"""dashboard/plugin_api.py: the backend behind the Desktop settings pages."""

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
    sys.modules.pop("hermes_dashboard_plugin_model_router", None)


class State:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


CFG = {
    "model": {"provider": "openai-codex", "default": "gpt-6.2-sol"},
    "fallback_providers": [{"provider": "anthropic", "model": "claude-opus-4-8"}],
    "plugins": {"enabled": ["model-router"], "entries": {"model-router": {"settings": {
        "scope": {"openai-codex": ["gpt-6.2-sol", "gpt-mini"]},
        "routes": {"deep": ["openai-codex/gpt-6.2-sol@xhigh"]},
        "classifier": "hybrid",
    }}}},
}


def test_build_state(api):
    state = State({"health": {"openai-codex/gpt-mini": {"ok": 1, "fail": 2, "streak": 2, "last_fail": 9e12}},
                   "preferred": {"quick": "openai-codex/gpt-mini@low"}})
    out = api.build_state(CFG, state, {"openai-codex": ["gpt-6.2-sol", "gpt-x"], "anthropic": ["claude-opus-4-8"]})
    assert out["plugin_enabled"] is True
    assert out["settings"]["classifier"] == "hybrid"
    assert out["settings"]["scope"] == {"openai-codex": ["gpt-6.2-sol", "gpt-mini"]}
    assert out["effective_routes"]["deep"] == ["openai-codex/gpt-6.2-sol@xhigh", "openai-codex/gpt-mini@xhigh"]
    assert out["limits"]["max_providers"] == 3 and "none" not in out["limits"]["efforts"]
    assert "openai-codex/gpt-mini" in out["cooling"]
    assert any("anthropic/claude-opus-4-8" in w for w in out["warnings"])  # fallback outside scope


def test_clean_values_enforces_limits(api, monkeypatch):
    monkeypatch.setattr(api, "_config", lambda: CFG)
    with pytest.raises(ValueError, match="at most 3"):
        api.clean_values({"scope": {"a": ["1"], "b": ["2"], "c": ["3"], "d": ["4"]}})
    with pytest.raises(ValueError, match="not in the allowlist"):
        api.clean_values({"routes": {"deep": ["anthropic/claude-opus-4-8@high"]}})
    with pytest.raises(ValueError, match="unknown setting"):
        api.clean_values({"evil": 1})
    with pytest.raises(ValueError, match="mode"):
        api.clean_values({"mode": "sometimes"})
    out = api.clean_values({"scope": {"anthropic": ["claude-opus-4-8"]},
                            "routes": {"deep": ["anthropic/claude-opus-4-8@high"]},
                            "cooldown_minutes": "5000", "cross_provider": True})
    assert out["routes"] == {"deep": ["anthropic/claude-opus-4-8@high"]}
    assert out["cooldown_minutes"] == 1440 and out["cross_provider"] is True


def test_clean_values_security_settings(api, monkeypatch):
    monkeypatch.setattr(api, "_config", lambda: CFG)
    out = api.clean_values({"degraded_retry": True, "security_roe": "x" * 9000,
                            "security_preamble_extra": "y" * 5000,
                            "security_sanitize_max_level": "9",
                            "extra_guardrail_patterns": [" Corp-Guard ", ""]})
    assert out["degraded_retry"] is True
    assert len(out["security_roe"]) == 8000 and len(out["security_preamble_extra"]) == 4000
    assert out["security_sanitize_max_level"] == 3
    assert out["extra_guardrail_patterns"] == ["corp-guard"]
    with pytest.raises(ValueError, match="degraded_retry"):
        api.clean_values({"degraded_retry": "yes"})


def test_routes_over_http(api, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    state, saved = State(), {}
    monkeypatch.setattr(api, "_config", lambda: CFG)
    monkeypatch.setattr(api, "_plugin_state", lambda: state)
    monkeypatch.setattr(api, "_inventory", lambda cfg: {"openai-codex": ["gpt-6.2-sol"]})
    monkeypatch.setattr(api, "_save", lambda values: saved.update(values))
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)

    assert client.get("/state").json()["inventory"] == {"openai-codex": ["gpt-6.2-sol"]}
    r = client.put("/settings", json={"values": {"scope": {"openai-codex": ["gpt-mini"]}, "mode": "off"}})
    assert r.status_code == 200 and saved == {"scope": {"openai-codex": ["gpt-mini"]}, "mode": "off"}
    assert client.put("/settings", json={"values": {"mode": "x"}}).status_code == 400
    assert client.post("/prefer", json={"task_class": "deep", "candidate": "evil/m"}).status_code == 400
    assert client.post("/prefer", json={"task_class": "deep", "candidate": "openai-codex/gpt-mini@low"}).status_code == 200
    assert state.data["preferred"] == {"deep": "openai-codex/gpt-mini@low"}
    client.post("/reset")
    assert state.data == {"health": {}, "preferred": {}}


def test_gateway_router_sees_desktop_reset(ctx):
    import model_router_plugin as plugin
    plugin.register(ctx)
    hooks = ctx.hooks
    hooks["pre_llm_call"][0](session_id="s", user_message="번역해줘: hi", turn_id="t1")
    ctx.middleware["llm_request"](request={"model": "x"}, turn_id="t1", provider="openai-codex", api_mode="")
    hooks["post_llm_call"][0](session_id="s", turn_id="t1")
    assert ctx.state.data["health"]
    ctx.state.data["preferred"] = {"quick": "openai-codex/gpt-6.2-sol@low"}  # written by the Desktop page
    hooks["pre_llm_call"][0](session_id="s", user_message="번역해줘: hi", turn_id="t2")
    out = ctx.middleware["llm_request"](request={"model": "x"}, turn_id="t2", provider="openai-codex", api_mode="")
    assert out["request"]["model"] == "gpt-6.2-sol"
