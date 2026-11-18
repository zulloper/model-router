"""route_explore against a fake lifecycle service (needs Hermes core for SubagentLaunchRequest)."""

import json
import types

import pytest

pytest.importorskip("agent.subagent_lifecycle")

import model_router_plugin as plugin  # noqa: E402


class FakeLifecycle:
    """Applies Hermes' real launch-request rejections, so a request the live service would refuse
    (e.g. a per-launch timeout_seconds) fails here too instead of passing silently."""

    def __init__(self, answers, hang=()):
        self.answers, self.launched, self.hang, self.cancelled = answers, [], set(hang), []

    def launch(self, req):
        from agent.subagent_lifecycle import _REQUEST_REJECTIONS, SubagentLifecycleError
        for rejected, message in _REQUEST_REJECTIONS:
            if rejected(req):
                raise SubagentLifecycleError(message)
        self.launched.append(req)
        return req.model

    def wait(self, handle, timeout_seconds=None):
        return types.SimpleNamespace(timed_out=handle in self.hang)

    def cancel(self, handle, reason=""):
        self.cancelled.append(handle)

    def result(self, handle):
        answer = self.answers.get(handle)
        state = types.SimpleNamespace(value="SUCCEEDED" if answer else "FAILED")
        return types.SimpleNamespace(terminal_state=state, summary=answer, error_message=None if answer else "boom",
                                     error_classification=None, usage_metadata={"tokens": 1})


def test_explore_runs_ladder_models_in_tier_order_and_saves_nothing(ctx, fake_llm):
    ctx.subagent_lifecycle = FakeLifecycle({"gpt-6.2-sol": "good", "gpt-mini": "ok", "claude-opus-4-8": None})
    ctx.llm = fake_llm(parsed={"scores": [{"id": "openai-codex/gpt-6.2-sol", "score": 9},
                                          {"id": "openai-codex/gpt-mini", "score": 5}],
                               "winner": "openai-codex/gpt-6.2-sol"})
    plugin.register(ctx)
    out = json.loads(ctx.tools["route_explore"][1]({"task": "write a parser"}))
    assert out["winner"] == "openai-codex/gpt-6.2-sol"
    assert [r.model for r in ctx.subagent_lifecycle.launched] == ["gpt-6.2-sol", "gpt-mini", "claude-opus-4-8"]
    assert ctx.state.data == {}  # nothing learned or saved
    failed = next(r for r in out["results"] if r["id"] == "anthropic/claude-opus-4-8")
    assert failed["ok"] is False


def test_explore_only_runs_models_on_the_ladder(ctx, fake_llm):
    ctx.subagent_lifecycle = FakeLifecycle({"gpt-mini": "fine"})
    plugin.register(ctx)
    out = json.loads(ctx.tools["route_explore"][1](
        {"task": "t", "models": ["openai-codex/gpt-mini", "evil/model"]}))
    assert out["winner"] == "openai-codex/gpt-mini"
    assert any("evil/model" in s for s in out["skipped"])
    assert [r.model for r in ctx.subagent_lifecycle.launched] == ["gpt-mini"]


def test_explore_without_a_ladder_says_so(make_ctx):
    ctx = make_ctx({})
    plugin.register(ctx)
    out = json.loads(ctx.tools["route_explore"][1]({"task": "t"}))
    assert out["success"] is False and "tier ladder" in out["error"]


def test_explore_judge_penalises_missing_code(pkg):
    from model_router_plugin import explore

    class Llm:
        calls = []

        def complete_structured(self, **kw):
            self.calls.append(kw)
            return type("R", (), {"parsed": {"scores": [{"id": "a/x", "score": 9}], "winner": "a/x"}})()

    llm = Llm()
    explore._judge(llm, "task", [{"id": "a/x", "ok": True, "answer": "x"}, {"id": "b/y", "ok": True, "answer": "y"}])
    assert "runnable" in llm.calls[0]["instructions"]


def test_explore_requests_pass_hermes_validation(ctx, fake_llm):
    """Regression: a per-launch timeout_seconds made the real lifecycle reject every launch."""
    ctx.subagent_lifecycle = FakeLifecycle({"gpt-6.2-sol": "good"})
    plugin.register(ctx)
    out = json.loads(ctx.tools["route_explore"][1](
        {"task": "t", "models": ["openai-codex/gpt-6.2-sol"], "timeout_seconds": 30}))
    assert out["winner"] == "openai-codex/gpt-6.2-sol"
    assert all(r.timeout_seconds is None for r in ctx.subagent_lifecycle.launched)


def test_explore_cancels_a_model_past_the_time_limit(ctx, fake_llm):
    ctx.subagent_lifecycle = FakeLifecycle({"gpt-6.2-sol": "good", "gpt-mini": "late"}, hang={"gpt-mini"})
    plugin.register(ctx)
    out = json.loads(ctx.tools["route_explore"][1](
        {"task": "t", "models": ["openai-codex/gpt-6.2-sol", "openai-codex/gpt-mini"]}))
    late = next(r for r in out["results"] if r["id"] == "openai-codex/gpt-mini")
    assert late["ok"] is False and late["state"] == "TIMED_OUT"
    assert ctx.subagent_lifecycle.cancelled == ["gpt-mini"]
    assert out["winner"] == "openai-codex/gpt-6.2-sol"
