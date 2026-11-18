"""Subagent retries (subagent_retry): a blocked turn whose model used tools is retried by Hermes
subagents with tools; everything else keeps the direct call."""

import types

import pytest

pytest.importorskip("agent.subagent_lifecycle")

import model_router_plugin as plugin  # noqa: E402
import model_router_plugin.ladder as ladder_mod  # noqa: E402
from model_router_plugin.attempts import AttemptLog  # noqa: E402
from model_router_plugin.ladder import LadderRunner  # noqa: E402
from model_router_plugin.outcome import TurnSignals  # noqa: E402
from model_router_plugin.settings import load_settings  # noqa: E402
from tests.conftest import FakeCtx  # noqa: E402

REFUSAL = "I'm sorry, but I can't help with that."
TIER = [["anthropic/claude-opus-4-8", "xai/grok-5"]]


class FakeLifecycle:
    """Applies Hermes' real launch-request rejections, like the gateway would."""

    def __init__(self, answers, hang=(), failed=()):
        self.answers, self.hang, self.failed = answers, set(hang), set(failed)
        self.launched, self.cancelled = [], []

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
        ok = handle not in self.failed
        return types.SimpleNamespace(terminal_state=types.SimpleNamespace(value="SUCCEEDED" if ok else "FAILED"),
                                     summary=self.answers.get(handle) if ok else None,
                                     error_message=None if ok else "child crashed", error_classification=None)


class Llm:
    def __init__(self, text="LLM: def parse(s): ..."):
        self.text, self.calls = text, []

    def complete(self, messages, *, provider=None, model=None, **kw):
        self.calls.append((model, messages[-1]["content"]))
        return types.SimpleNamespace(text=self.text, usage=None)


def setup(tmp_path, lifecycle, *, tools_used=True, timeout=120.0, **cfg):
    ctx = FakeCtx()
    ctx.llm = Llm()
    ctx.subagent_lifecycle = lifecycle
    signals = TurnSignals()
    log = AttemptLog(tmp_path / "data" / "attempts.jsonl")
    r = LadderRunner(ctx, signals, log, timeout_fn=lambda: timeout, evaluate_async=False)
    r.note_turn("t1", "s1", "Fix the failing test in parser.py.",
                [{"role": "user", "content": "the build is red"}, {"role": "assistant", "content": "looking"}])
    signals.record_success("t1", "openai-codex", "gpt-6.1-sol-900k", tool_calls=3 if tools_used else 0)
    settings = load_settings(lambda key, default=None: {"tiers": TIER, "subagent_retry": True, **cfg}.get(key, default))
    return r, ctx, log, settings


def run(r, s):
    return r.handle(REFUSAL, turn_id="t1", session_id="s1", settings=s)


def test_tool_using_turn_is_retried_by_subagents(tmp_path):
    lc = FakeLifecycle({"claude-opus-4-8": "Fixed: patched parser.py", "grok-5": "Also fixed"})
    r, ctx, log, s = setup(tmp_path, lc, synthesize=False)
    out = run(r, s)
    assert out.startswith("Fixed: patched parser.py")
    assert "(tier 1, with tools, as a subagent)" in out
    req = lc.launched[0]
    assert req.allowed_toolsets == ("web",)                       # the safe default
    assert req.goal.startswith("Fix the failing test in parser.py.")
    assert "delivered to the user as the answer" in req.goal
    assert "user: the build is red" in req.context and req.timeout_seconds is None
    assert ctx.llm.calls == []                                     # no direct calls this turn
    assert {a.transport for a in log.recent() if a.kind == "plain"} == {"subagent"}


@pytest.mark.parametrize("choice,toolsets", [("web", ("web",)), ("web+file", ("web", "file")), ("inherit", None)])
def test_toolsets_follow_the_setting(tmp_path, choice, toolsets):
    lc = FakeLifecycle({"claude-opus-4-8": "ok answer"})
    r, _, _, s = setup(tmp_path, lc, subagent_toolsets=choice, synthesize=False)
    run(r, s)
    assert lc.launched[0].allowed_toolsets == toolsets


def test_turn_without_tools_keeps_the_direct_call(tmp_path):
    lc = FakeLifecycle({"claude-opus-4-8": "unused"})
    r, ctx, log, s = setup(tmp_path, lc, tools_used=False, synthesize=False)
    assert "without tools" in run(r, s)
    assert lc.launched == [] and ctx.llm.calls


def test_setting_off_keeps_the_direct_call(tmp_path):
    lc = FakeLifecycle({"claude-opus-4-8": "unused"})
    r, ctx, _, s = setup(tmp_path, lc, subagent_retry=False, synthesize=False)
    run(r, s)
    assert lc.launched == [] and ctx.llm.calls


def test_subagent_past_the_deadline_is_cancelled(tmp_path):
    lc = FakeLifecycle({"claude-opus-4-8": "late", "grok-5": "late"}, hang={"claude-opus-4-8", "grok-5"})
    r, _, log, s = setup(tmp_path, lc)
    assert run(r, s) is None
    assert sorted(lc.cancelled) == ["claude-opus-4-8", "grok-5"]
    assert all("subagent cancelled" in a.reason for a in log.recent() if a.kind == "plain")


def test_failed_subagent_is_an_error_and_the_other_answers(tmp_path):
    lc = FakeLifecycle({"grok-5": "grok fixed it"}, failed={"claude-opus-4-8"})
    r, _, log, s = setup(tmp_path, lc)
    assert run(r, s).startswith("grok fixed it")
    failed = next(a for a in log.recent() if a.model == "claude-opus-4-8")
    assert failed.outcome == "error" and "child crashed" in failed.reason


def test_two_subagent_answers_are_combined_by_a_direct_call(tmp_path):
    lc = FakeLifecycle({"claude-opus-4-8": "answer A", "grok-5": "answer B"})
    r, ctx, log, s = setup(tmp_path, lc)
    out = run(r, s)
    assert out.startswith("LLM:") and "were combined by anthropic/claude-opus-4-8" in out
    assert "with tools, as a subagent" in out
    assert [m for m, _ in ctx.llm.calls] == ["claude-opus-4-8"]          # only the synthesis
    synth = log.recent()[-1]
    assert synth.kind == "synthesis" and synth.transport == "llm"


def test_a_clipped_answer_says_so(tmp_path):
    lc = FakeLifecycle({"claude-opus-4-8": "x" * ladder_mod.SUBAGENT_RESULT_CHARS})
    r, _, _, s = setup(tmp_path, lc, synthesize=False)
    assert "[answer clipped at 32,000 characters by Hermes]" in run(r, s)


def test_long_requests_fit_hermes_launch_limits(tmp_path):
    lc = FakeLifecycle({"claude-opus-4-8": "fine"})
    r, ctx, _, s = setup(tmp_path, lc, synthesize=False)
    r.note_turn("t1", "s1", "y" * 50000, [{"role": "user", "content": "z" * 50000}])
    assert run(r, s).startswith("fine")      # a rejected launch would have been an error instead
    assert len(lc.launched[0].goal) <= 16000 and len(lc.launched[0].context) <= 32000


def test_hooks_learn_that_the_turn_used_tools(tmp_path):
    ctx = FakeCtx({"tiers": TIER, "subagent_retry": True, "synthesize": False})
    ctx.llm = Llm()
    ctx.subagent_lifecycle = FakeLifecycle({"claude-opus-4-8": "subagent answer"})
    plugin.register(ctx)
    ctx.hooks["pre_llm_call"][0](session_id="s1", user_message="Fix it.", conversation_history=[], turn_id="t1")
    ctx.hooks["post_api_request"][0](turn_id="t1", provider="openai-codex", model="m", assistant_tool_call_count=2)
    ctx.hooks["post_api_request"][0](turn_id="t1", provider="openai-codex", model="m", assistant_tool_call_count=0)
    out = ctx.hooks["transform_llm_output"][0](response_text=REFUSAL, session_id="s1", turn_id="t1")
    assert out.startswith("subagent answer")


def test_route_status_mentions_subagent_retry(make_ctx):
    ctx = make_ctx({"tiers": TIER, "subagent_retry": True, "subagent_toolsets": "inherit"})
    plugin.register(ctx)
    assert "subagent retry for tool-using turns (tools: inherit)" in ctx.commands["route"]("")
