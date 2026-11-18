"""``route_explore`` tool: run one task on several ladder models as subagents and score them.

Runs during a turn (the agent calls it), because subagents need a live parent session. Each model
gets one subagent through the public ``ctx.subagent_lifecycle``; a judge call
(``ctx.llm.complete_structured``) scores the answers. The result is a report for deciding the tier
order; nothing is saved (promotion learns from real blocked turns instead, :mod:`promote`).
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
import uuid
from typing import Any, Optional

from .candidate import Candidate, parse_candidate
from .refusal import wants_code
from .settings import Settings

logger = logging.getLogger(__name__)

SCHEMA = {
    "name": "route_explore",
    "description": (
        "Run the same task on several models from the user's model-router tier ladder in parallel "
        "(one subagent each) and score the answers. Use it when the user asks to compare or test "
        "models, or which model suits a task, e.g. to decide the tier order. Slow and costly: one "
        "full subagent run per model. Nothing is saved."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "The task to give every model, self-contained."},
            "models": {"type": "array", "items": {"type": "string"},
                       "description": "Optional provider/model ids to try (must be on the ladder). "
                                      "Default: the ladder's models in tier order, up to max_explore."},
            "timeout_seconds": {"type": "integer", "description": "Time limit in seconds for the whole run "
                                "(models run in parallel); a model still running then is cancelled. Default 600."},
        },
        "required": ["task"],
    },
}

_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "score": {"type": "number"},
                           "note": {"type": "string"}},
            "required": ["id", "score"]}},
        "winner": {"type": "string"},
    },
    "required": ["scores", "winner"],
}


def pick_candidates(settings: Settings, requested: Optional[list[str]]) -> tuple[list[Candidate], list[str]]:
    skipped: list[str] = []
    on_ladder = {c.key: c for c in settings.ladder.models()}
    if requested:
        chosen = []
        for raw in requested:
            cand = parse_candidate(raw)
            if cand is not None and cand.key in on_ladder:
                chosen.append(on_ladder[cand.key])
            else:
                skipped.append(f"{raw}: not on the tier ladder")
    else:
        chosen = list(on_ladder.values())
    unique: list[Candidate] = []
    for c in chosen:
        if all(u.key != c.key for u in unique):
            unique.append(c)
    if len(unique) > settings.max_explore:
        skipped += [f"{c.key}: over max_explore={settings.max_explore}" for c in unique[settings.max_explore:]]
        unique = unique[:settings.max_explore]
    return unique, skipped


def _launch(lifecycle: Any, cand: Candidate, task: str, tag: str) -> tuple[Any, Optional[str]]:
    """Launch on the calling thread: the live parent is a context variable a pool thread won't see.
    Children then run concurrently on Hermes' own lifecycle pool. The lifecycle API rejects a
    per-launch ``timeout_seconds``, so the time limit is enforced in :func:`_collect` instead."""
    from agent.subagent_lifecycle import SubagentLaunchRequest
    try:
        return lifecycle.launch(SubagentLaunchRequest(
            goal=task, model=cand.model, provider=cand.provider, correlation_id=f"{tag}-{cand.key}",
            metadata={"model_router": "explore"})), None
    except Exception as exc:
        return None, str(exc)


def _collect(lifecycle: Any, cand: Candidate, handle: Any, launch_error: Optional[str],
             deadline: float) -> dict:
    """Wait for one child until the shared ``deadline`` (children run in parallel, so the limit is
    one wall-clock window for all of them). A child still running then is cancelled: ``wait`` only
    stops waiting, it does not stop the child, which would otherwise keep spending tokens."""
    out: dict[str, Any] = {"id": cand.key, "model": cand.model, "provider": cand.provider}
    if handle is None:
        out.update(ok=False, error=launch_error or "launch failed")
        return out
    try:
        waited = lifecycle.wait(handle, timeout_seconds=max(1.0, deadline - time.monotonic()))
        if getattr(waited, "timed_out", False):
            with contextlib.suppress(Exception):
                lifecycle.cancel(handle, reason="model-router: route_explore time limit reached")
            out.update(ok=False, state="TIMED_OUT", error="time limit reached; subagent cancelled")
            return out
        res = lifecycle.result(handle)
    except Exception as exc:
        out.update(ok=False, error=str(exc))
        return out
    state = str(getattr(getattr(res, "terminal_state", None), "value", getattr(res, "terminal_state", "")))
    summary = getattr(res, "summary", None) or ""
    out.update(ok=state == "SUCCEEDED" and bool(summary.strip()), state=state, answer=summary,
               error=getattr(res, "error_message", None) or getattr(res, "error_classification", None))
    usage = getattr(res, "usage_metadata", None)
    if usage:
        out["usage"] = dict(usage)
    return out


_JUDGE_INSTRUCTIONS = (
    "Score each answer from 0 to 10 for how well it accomplishes the task: correctness first, then "
    "completeness, then clarity. When the task asks for code, an answer without complete, runnable "
    "code scores low. Ignore which model wrote it. Return every id with a score and the id of the "
    "best one as winner."
)


def _judge(llm: Any, task: str, results: list[dict]) -> tuple[Optional[str], dict[str, Any]]:
    answers = [{"id": r["id"], "answer": (r.get("answer") or "")[:6000]} for r in results if r.get("ok")]
    if not answers:
        return None, {}
    if len(answers) == 1:
        return answers[0]["id"], {}
    try:
        res = llm.complete_structured(
            instructions=_JUDGE_INSTRUCTIONS,
            input={"task": task[:6000], "answers": answers}, json_schema=_JUDGE_SCHEMA,
            schema_name="model_scores", max_tokens=1200, timeout=120,
            purpose="model-router: judge explore results")
        parsed = getattr(res, "parsed", None) or {}
        scores = {s["id"]: s for s in parsed.get("scores", []) if isinstance(s, dict) and "id" in s}
        winner = parsed.get("winner")
        if winner not in {a["id"] for a in answers}:
            winner = max(scores, key=lambda k: scores[k].get("score", 0)) if scores else answers[0]["id"]
        return winner, scores
    except Exception as exc:
        logger.warning("model-router: judge failed, picking the first successful model: %s", exc)
        return answers[0]["id"], {}


def make_handler(ctx: Any, settings_fn):
    def handle(params: dict, **kwargs: Any) -> str:
        task = str((params or {}).get("task") or "").strip()
        if not task:
            return json.dumps({"success": False, "error": "task is required"})
        s = settings_fn()
        if s.ladder.empty:
            return json.dumps({"success": False, "error": "No tier ladder yet: run `hermes model-router setup`."})
        cands, skipped = pick_candidates(s, params.get("models"))
        if not cands:
            return json.dumps({"success": False, "error": "No ladder model to try", "skipped": skipped})
        timeout = float(params.get("timeout_seconds") or 600)
        lifecycle = ctx.subagent_lifecycle
        tag = f"mr-{uuid.uuid4().hex[:8]}"
        launched = [(c, *_launch(lifecycle, c, task, tag)) for c in cands]
        deadline = time.monotonic() + timeout
        results = [_collect(lifecycle, c, h, err, deadline) for c, h, err in launched]
        winner_id, scores = _judge(ctx.llm, task, results)
        for r in results:
            if r["id"] in scores:
                r["score"] = scores[r["id"]].get("score")
                r["judge_note"] = scores[r["id"]].get("note")
            r["answer"] = (r.get("answer") or "")[:1500]
        return json.dumps({
            "success": winner_id is not None, "winner": winner_id, "results": results, "skipped": skipped,
            "code_task": wants_code(task),
            "note": "Subagents run at delegation.reasoning_effort. Nothing was saved; reorder tiers to act on this.",
        }, ensure_ascii=False)
    return handle
