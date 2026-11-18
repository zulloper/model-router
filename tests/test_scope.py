from model_router_plugin.scope import (
    MAX_PROVIDERS, Candidate, build_routes, fallback_warnings, parse_candidate, parse_scope)


def test_parse_candidate_variants():
    assert parse_candidate("openai-codex/gpt-6.2-sol@high") == Candidate("openai-codex", "gpt-6.2-sol", "high")
    assert parse_candidate("OpenRouter/anthropic/claude-x") == Candidate("openrouter", "anthropic/claude-x", None)
    # "@" that isn't an effort stays part of the model id
    assert parse_candidate("custom/model@v2") == Candidate("custom", "model@v2", None)
    assert parse_candidate({"provider": "anthropic", "model": "c", "effort": "low"}) == Candidate("anthropic", "c", "low")
    assert parse_candidate("no-model") is None
    assert parse_candidate(42) is None


def test_scope_caps_providers_and_dedupes():
    raw = {"a": ["m1", "m1", "m2"], "b": "m3", "c": ["m4"], "d": ["m5"], "e": []}
    scope = parse_scope(raw)
    assert list(scope.providers) == ["a", "b", "c"]
    assert scope.providers["a"] == ["m1", "m2"]
    assert len(scope.providers) == MAX_PROVIDERS
    assert any("'d'" in w for w in scope.warnings)
    assert scope.allows("A", "m1") and not scope.allows("d", "m5")


def test_scope_list_form():
    scope = parse_scope([{"provider": "x", "models": ["y"]}])
    assert scope.providers == {"x": ["y"]}


def test_routes_filter_to_allowlist_and_fill_defaults():
    scope = parse_scope({"p": ["m1", "m2"]})
    routes, warnings = build_routes({"deep": ["p/m2@xhigh", "q/zzz@high", "garbage"], "weird": []}, scope)
    assert [str(c) for c in routes["deep"]] == ["p/m2@xhigh", "p/m1@xhigh"]
    assert [str(c) for c in routes["quick"]] == ["p/m1@low", "p/m2@low"]
    assert any("outside the allowlist" in w for w in warnings)
    assert any("can't read" in w for w in warnings)
    assert any("unknown task class" in w for w in warnings)


def test_fallback_warnings():
    scope = parse_scope({"openai-codex": ["gpt-6.2-sol"]})
    cfg = {"fallback_providers": [{"provider": "openai-codex", "model": "gpt-6.2-sol"},
                                  {"provider": "anthropic", "model": "claude-opus-4-8"}]}
    warnings = fallback_warnings(cfg, scope)
    assert len(warnings) == 1 and "anthropic/claude-opus-4-8" in warnings[0]
