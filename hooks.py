"""Hook and middleware callbacks: where the router meets the agent loop.

Turn flow:

1. ``pre_llm_call`` classifies the request and plans an ordered list of allowed candidates. With
   ``cross_provider`` on, it also moves the session to the first candidate's provider.
2. ``llm_request`` middleware (every provider request of the turn) sets the request's model and
   reasoning effort to the turn's current candidate on that request's provider.
3. ``api_request_error`` marks the current candidate failed when the error is the model's fault
   (guardrail, refusal, unknown model, entitlement...), so Hermes' retry/fallback request goes to the
   next allowed candidate. ``transform_api_error_classification`` turns user-listed guardrail phrases
   into a content-policy block so Hermes falls back at all.
4. ``transform_llm_output`` catches a soft refusal and re-answers it on another allowed model.
5. ``post_llm_call`` records the success.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from . import private_switch
from .classify import classify
from .refusal import looks_like_refusal, matches_guardrail
from .router import Router, Settings, TurnPlan, load_settings
from .scope import Candidate

logger = logging.getLogger(__name__)

# FailoverReason values that say "this model can't do this request" (vs. transient trouble on the
# route, where retrying the same model is right).
MODEL_FAILURES = frozenset({
    "content_policy_blocked", "model_not_found", "model_entitlement", "data_policy_denied",
    "provider_policy_blocked", "incomplete_response", "upstream_blocked", "format_error",
    "reasoning_mandatory", "long_context_tier",
})
_FOOTER = "\n\n— model-router: {bad} declined this, so {good} answered instead (without tools)."


def _reanswer_timeout() -> float:
    """``transform_llm_output`` runs under ``plugins.hook_callback_timeout`` (default 30s; 0 = off):
    a re-answer that outlives it is abandoned, so finish a few seconds inside it."""
    try:
        from hermes_cli.config import load_config
        limit = float(((load_config() or {}).get("plugins") or {}).get("hook_callback_timeout", 30))
    except Exception:
        limit = 30.0
    return 180.0 if limit <= 0 else max(5.0, min(180.0, limit - 4.0))


def _recent_context(history: Any, limit: int = 6) -> list[dict[str, str]]:
    out = []
    for msg in (history or [])[-limit:]:
        if isinstance(msg, dict) and msg.get("role") in ("user", "assistant"):
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                out.append({"role": msg["role"], "content": content[:4000]})
    return out


class RouterHooks:
    def __init__(self, ctx: Any, router: Optional[Router] = None):
        self.ctx = ctx
        self.router = router or Router(getattr(ctx, "state", None))
        self._home_route: dict[str, tuple[str, str]] = {}  # session -> route before we moved it

    def settings(self) -> Settings:
        return load_settings(self.ctx.get_config)

    # 1. plan ---------------------------------------------------------------------------------
    def on_pre_llm_call(self, session_id: str = "", user_message: str = "", conversation_history: Any = None,
                        turn_id: str = "", task_id: str = "", **kwargs: Any) -> None:
        s = self.settings()
        if not s.active:
            if s.mode == "off" and session_id in self._home_route:
                self._restore_home(session_id)
            return None
        llm_call = None
        if s.classifier != "rules":
            llm_call = getattr(getattr(self.ctx, "llm", None), "complete_structured", None)
        task_class, why = classify(user_message, conversation_history, s.classifier, llm_call)
        plan = self.router.plan(s, turn_id=turn_id or task_id or session_id, session_id=session_id,
                                task_class=task_class, why=why, user_message=user_message or "",
                                context=_recent_context(conversation_history))
        plan.note(f"{task_class} ({why})")
        if s.cross_provider and plan.order:
            self._cross_provider(plan, session_id)
        return None

    def _cross_provider(self, plan: TurnPlan, session_id: str) -> None:
        agent = private_switch.live_agent()
        if agent is None:
            plan.note("cross_provider: no live agent; staying on the current provider")
            return
        provider, model = private_switch.current_route(agent)
        target = plan.order[0]
        if target.provider != provider:
            self._home_route.setdefault(session_id, (provider, model))
            err = private_switch.switch_to(agent, target)
            if err:
                plan.note(f"cross_provider: stayed on {provider} ({err})")
                logger.warning("model-router: %s", err)
                return
            plan.switched_provider = target.provider
            plan.note(f"cross_provider: session moved {provider} → {target.provider}")
        private_switch.set_effort(agent, target.effort)

    def _restore_home(self, session_id: str) -> None:
        agent = private_switch.live_agent()
        provider, model = self._home_route.get(session_id, ("", ""))
        if agent is None or not provider:
            return
        if private_switch.switch_to(agent, Candidate(provider, model)) is None:
            self._home_route.pop(session_id, None)

    # 2. route each provider request ----------------------------------------------------------
    def on_llm_request(self, request: Any = None, turn_id: str = "", task_id: str = "", provider: str = "",
                       api_mode: str = "", **kwargs: Any) -> Optional[dict]:
        if not isinstance(request, dict):
            return None
        plan = self.router.turn(turn_id) or self.router.turn(task_id)
        if plan is None:
            return None
        cand = self.router.pick(plan, provider)
        if cand is None:  # no allowed model on this provider: leave Hermes' choice alone
            return None
        from .reasoning import apply_effort, set_model
        new = dict(request)
        changed = set_model(new, cand.model)
        effort_note = apply_effort(new, api_mode, cand.effort)
        if not changed and not effort_note:
            return None
        return {"request": new, "source": "model-router", "name": "route",
                "reason": f"{plan.task_class} → {cand}" + (f" ({effort_note})" if effort_note else "")}

    # 3. failures -----------------------------------------------------------------------------
    def on_api_request_error(self, turn_id: str = "", task_id: str = "", provider: str = "",
                             reason: str = "", retryable: Any = None, error: Any = None,
                             **kwargs: Any) -> None:
        plan = self.router.turn(turn_id) or self.router.turn(task_id)
        if plan is None or plan.current is None:
            return None
        reason = str(reason or "")
        model_fault = reason in MODEL_FAILURES or (reason == "unknown" and retryable is False)
        if not model_fault:
            return None
        if plan.current.provider != (provider or "").lower():
            return None
        msg = ""
        if isinstance(error, dict):
            msg = str(error.get("message") or "")[:160]
        self.router.mark_failed(plan, plan.current, f"{reason}{': ' + msg if msg else ''}")
        return None

    def on_classify_error(self, error_message: str = "", error_body: Any = None, **kwargs: Any) -> Optional[dict]:
        s = self.settings()
        if not s.extra_guardrail_patterns:
            return None
        text = f"{error_message or ''} {error_body if isinstance(error_body, str) else ''}"
        if matches_guardrail(text, s.extra_guardrail_patterns):
            return {"reason": "content_policy_blocked", "retryable": False, "should_fallback": True}
        return None

    # 4. soft refusals ------------------------------------------------------------------------
    def on_transform_llm_output(self, response_text: str = "", session_id: str = "", turn_id: str = "",
                                **kwargs: Any) -> Optional[str]:
        if not looks_like_refusal(response_text):
            return None
        s = self.settings()
        if not s.active or not s.soft_refusal_retry:
            return None
        plan = self.router.turn(turn_id) or self.router.last_turn(session_id)
        if plan is None:
            return None
        bad = plan.current
        if bad is not None:
            self.router.mark_failed(plan, bad, "soft refusal")
            self.router.escalate_next(session_id, bad.key)
        alt = self.router.next_alternative(plan, s)
        llm = getattr(self.ctx, "llm", None)
        if alt is None or llm is None:
            return None
        messages = list(plan.context)
        if not messages or messages[-1].get("content") != plan.user_message:
            messages.append({"role": "user", "content": plan.user_message})
        try:
            res = llm.complete(messages, provider=alt.provider, model=alt.model, timeout=_reanswer_timeout(),
                               purpose="model-router: re-answer a soft refusal")
        except Exception as exc:
            logger.warning("model-router: re-answer on %s failed: %s", alt.key, exc)
            plan.note(f"re-answer on {alt.key} failed: {exc}")
            return None
        text = str(getattr(res, "text", "") or "").strip()
        if not text or looks_like_refusal(text):
            self.router.mark_failed(plan, alt, "soft refusal (re-answer)")
            return None
        plan.answered_by = alt
        self.router.mark_ok(alt)
        plan.note(f"re-answered by {alt.key}")
        return text + _FOOTER.format(bad=bad.key if bad else "the model", good=alt.key)

    # 5. success ------------------------------------------------------------------------------
    def on_post_llm_call(self, session_id: str = "", turn_id: str = "", **kwargs: Any) -> None:
        plan = self.router.turn(turn_id) or self.router.last_turn(session_id)
        if plan is None or plan.answered_by is not None:
            return None
        if plan.current is not None and plan.current.key not in plan.failed:
            self.router.mark_ok(plan.current)
        return None
