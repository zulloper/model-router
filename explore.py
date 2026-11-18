"""``route_explore`` tool: run one task on several allowed models as subagents, score, remember.

Runs during a turn (the agent calls it), because subagents need a live parent session. Each allowed
model gets one subagent through the public ``ctx.subagent_lifecycle``; a judge call
(``ctx.llm.complete_structured``) scores the answers, and the winner becomes the learned preference
for the task class, so later turns of that kind start on it.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

from .router import Router, Settings
from .scope import TASK_CLASSES, Candidate

logger = logging.getLogger(__name__)

SCHEMA = {
    "name": "route_explore",
    "description": (
        "Run the same task on several allowed models in parallel (one subagent each), score the "
        "answers and remember the best model for this kind of task. Use it when the user asks to "
        "compare or test models, or to find which model suits a task. Only models in the user's "
        "model-router allowlist are used. Slow and costly: one full subagent run per model."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "The task to give every model, self-contained."},
            "task_class": {"type": "string", "enum": list(TASK_CLASSES),
                           "description": "Kind of task, for remembering the winner. Default: standard."},
            "models": {"type": "array", "items": {"type": "string"},
                       "description": "Optional provider/model ids to try (must be in the allowlist)."},
            "save": {"type": "boolean", "description": "Remember the winner for this task class. Default true."},
            "timeout_seconds": {"type": "integer", "description": "Per-model time limit. Default 600."},
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


def pick_candidates(settings: Settings, router: Router, task_class: str,
                    requested: Optional[list[str]]) -> tuple[list[Candidate], list[str]]:
    skipped: list[str] = []
    route = router.order_for(settings, task_class)
    by_key = {c.key: c for c in route}
    for c in settings.scope.all_models():
        by_key.setdefault(c.key, c)
    if requested:
        chosen = []
        for raw in requested:
            key = str(raw).split("@", 1)[0].strip()
            if key in by_key:
                chosen.append(by_key[key])
            else:
                skipped.append(f"{raw}: not in the allowlist")
    else:
        chosen = list(by_key.values())
    unique: list[Candidate] = []
    for c in chosen:
        if all(u.key != c.key for u in unique):
            unique.append(c)
    if len(unique) > settings.max_explore:
        skipped += [f"{c.key}: over max_explore={settings.max_explore}" for c in unique[settings.max_explore:]]
        unique = unique[:settings.max_explore]
    return unique, skipped


def _launch(lifecycle: Any, cand: Candidate, task: str, timeout: float, tag: str) -> tuple[Any, Optional[str]]:
    """Launch on the calling thread: the live parent is a context variable a pool thread won't see.
    Children then run concurrently on Hermes' own lifecycle pool."""
    from agent.subagent_lifecycle import SubagentLaunchRequest
    try:
        return lifecycle.launch(SubagentLaunchRequest(
            goal=task, model=cand.model, provider=cand.provider, correlation_id=f"{tag}-{cand.key}",
            metadata={"model_router": "explore"}, timeout_seconds=timeout)), None
    except Exception as exc:
        return None, str(exc)


def _collect(lifecycle: Any, cand: Candidate, handle: Any, launch_error: Optional[str], timeout: float) -> dict:
    out: dict[str, Any] = {"id": cand.key, "model": cand.model, "provider": cand.provider}
    if handle is None:
        out.update(ok=False, error=launch_error or "launch failed")
        return out
    try:
        lifecycle.wait(handle, timeout_seconds=timeout + 30)
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
    "Score each answer from 0 to 10 for how well it accomplishes the task: "
    "correctness first, then completeness, then clarity. Ignore which model wrote "
    "it. Return every id with a score and the id of the best one as winner."
)
_JUDGE_INSTRUCTIONS_SEC = (
    "You are scoring answers to a security task (CVE analysis, exploit/PoC, reverse engineering). "
    "Score 0-10 on: technical correctness, whether the requested code/commands are complete and "
    "runnable, exactness of versions/offsets/symbols, and evidence the author verified rather than "
    "guessed. Penalise answers that are only a conceptual overview when code was asked for, and "
    "answers padded with disclaimers. Ignore which model wrote it. Return every id with a score and "
    "the id of the best one as winner."
)


def _judge(llm: Any, task: str, results: list[dict], task_class: str = "standard") -> tuple[Optional[str], dict[str, Any]]:
    answers = [{"id": r["id"], "answer": (r.get("answer") or "")[:6000]} for r in results if r.get("ok")]
    if not answers:
        return None, {}
    if len(answers) == 1:
        return answers[0]["id"], {}
    try:
        res = llm.complete_structured(
            instructions=(_JUDGE_INSTRUCTIONS_SEC if task_class == "sec" else _JUDGE_INSTRUCTIONS),
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


def make_handler(ctx: Any, router: Router, settings_fn):
    def handle(params: dict, **kwargs: Any) -> str:
        task = str((params or {}).get("task") or "").strip()
        if not task:
            return json.dumps({"success": False, "error": "task is required"})
        s = settings_fn()
        if s.scope.empty:
            return json.dumps({"success": False, "error": "No allowlist yet: run `hermes model-router setup`."})
        task_class = params.get("task_class") if params.get("task_class") in TASK_CLASSES else "standard"
        cands, skipped = pick_candidates(s, router, task_class, params.get("models"))
        if not cands:
            return json.dumps({"success": False, "error": "No allowed model to try", "skipped": skipped})
        timeout = float(params.get("timeout_seconds") or 600)
        lifecycle = ctx.subagent_lifecycle
        tag = f"mr-{uuid.uuid4().hex[:8]}"
        launched = [(c, *_launch(lifecycle, c, task, timeout, tag)) for c in cands]
        results = [_collect(lifecycle, c, h, err, timeout) for c, h, err in launched]
        winner_id, scores = _judge(ctx.llm, task, results, task_class)
        winner = next((c for c in cands if c.key == winner_id), None)
        if winner is not None and params.get("save", True):
            router.prefer(task_class, winner)
        for r in results:
            if r["id"] in scores:
                r["score"] = scores[r["id"]].get("score")
                r["judge_note"] = scores[r["id"]].get("note")
            r["answer"] = (r.get("answer") or "")[:1500]
        return json.dumps({
            "success": winner is not None, "task_class": task_class,
            "winner": winner.key if winner else None,
            "saved_as_preferred": bool(winner and params.get("save", True)),
            "results": results, "skipped": skipped,
            "note": "Subagents run at delegation.reasoning_effort (per-model effort isn't settable here).",
        }, ensure_ascii=False)
    return handle
