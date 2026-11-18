import ast
from pathlib import Path

import pytest

from model_router_plugin.settings import load_settings
from model_router_plugin.tiers import TIER_SIZE_HINT, parse_tiers


def keys(ladder):
    return [[c.key for c in tier] for tier in ladder.tiers]


def test_tiers_keep_order_within_and_across_tiers():
    ladder = parse_tiers([["openai-codex/gpt-6.2-sol"],
                          ["Anthropic/claude-opus-4-8", "openai-codex/gpt-6.1-sol-900k"]])
    assert keys(ladder) == [["openai-codex/gpt-6.2-sol"],
                            ["anthropic/claude-opus-4-8", "openai-codex/gpt-6.1-sol-900k"]]
    assert [c.key for c in ladder.models()] == ["openai-codex/gpt-6.2-sol", "anthropic/claude-opus-4-8",
                                                "openai-codex/gpt-6.1-sol-900k"]
    assert ladder.warnings == ()


def test_ladder_is_the_allowlist():
    ladder = parse_tiers([["openai-codex/gpt-6.2-sol"], ["anthropic/claude-opus-4-8"]])
    assert ladder.allows("OpenAI-Codex", "gpt-6.2-sol")
    assert not ladder.allows("openai-codex", "gpt-6.1-sol")
    assert ladder.tier_of("anthropic/claude-opus-4-8") == 1
    assert ladder.tier_of("nope/x") is None


def test_single_string_tier_and_openrouter_style_model_ids():
    ladder = parse_tiers(["openrouter/meta-llama/llama-4", ["anthropic/claude-opus-4-8"]])
    assert keys(ladder) == [["openrouter/meta-llama/llama-4"], ["anthropic/claude-opus-4-8"]]


def test_unreadable_entries_are_dropped_with_a_warning():
    ladder = parse_tiers([["openai-codex/gpt-6.2-sol", "no-slash", 42], 7])
    assert keys(ladder) == [["openai-codex/gpt-6.2-sol"]]
    assert any("tiers[1]" in w and "'no-slash'" in w for w in ladder.warnings)
    assert any("tiers[2]" in w for w in ladder.warnings)


def test_a_model_appears_on_one_tier_only():
    ladder = parse_tiers([["openai-codex/gpt-6.2-sol"], ["openai-codex/gpt-6.2-sol", "anthropic/claude-opus-4-8"]])
    assert keys(ladder) == [["openai-codex/gpt-6.2-sol"], ["anthropic/claude-opus-4-8"]]
    assert any("already in tier 1" in w for w in ladder.warnings)


def test_empty_tier_is_skipped():
    ladder = parse_tiers([[], ["bad"], ["anthropic/claude-opus-4-8"]])
    assert keys(ladder) == [["anthropic/claude-opus-4-8"]]
    assert sum("no usable models" in w for w in ladder.warnings) == 2


def test_effort_suffix_is_ignored_with_one_warning():
    ladder = parse_tiers([["openai-codex/gpt-6.2-sol@high", "anthropic/claude-opus-4-8@xhigh"]])
    assert all(c.effort is None for c in ladder.models())
    assert sum("ignored" in w for w in ladder.warnings) == 1


def test_large_tier_is_flagged_but_kept():
    models = [f"p/m{i}" for i in range(TIER_SIZE_HINT + 1)]
    ladder = parse_tiers([models])
    assert len(ladder.tiers[0]) == TIER_SIZE_HINT + 1
    assert any("in parallel" in w for w in ladder.warnings)


@pytest.mark.parametrize("raw", [None, "", [], {}])
def test_unset_tiers_is_an_empty_ladder_without_warnings(raw):
    ladder = parse_tiers(raw)
    assert ladder.empty and ladder.warnings == ()


def test_non_list_tiers_is_ignored_with_a_warning():
    ladder = parse_tiers({"1": ["openai-codex/gpt-6.2-sol"]})
    assert ladder.empty and len(ladder.warnings) == 1


def test_settings_carry_the_ladder_and_its_warnings():
    cfg = {"tiers": [["openai-codex/gpt-6.2-sol"], ["bad"]]}
    s = load_settings(lambda key, default=None: cfg.get(key, default))
    assert keys(s.ladder) == [["openai-codex/gpt-6.2-sol"]]
    assert any("tiers[2]" in w for w in s.warnings)


def test_settings_without_tiers_still_load():
    s = load_settings(lambda key, default=None: default)
    assert s.ladder.empty


def test_tiers_module_does_not_depend_on_task_classification():
    """classify.py is removed in P7; the ladder must not grow a dependency on it."""
    tree = ast.parse((Path(__file__).resolve().parents[1] / "tiers.py").read_text())
    imported = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imported |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert not any("classify" in name for name in imported)
