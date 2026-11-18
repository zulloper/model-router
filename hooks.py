"""Hook callbacks: where the tier ladder meets the agent loop (0.4.0).

The profile's own model runs every turn unchanged. The plugin only steps in when a reply is
blocked:

1. ``pre_llm_call`` remembers the turn's request and recent conversation.
2. ``post_api_request`` / ``api_request_error`` follow the turn's provider attempts, so the ladder
   can tell a reply from an error notice (``transform_llm_output`` is not told).
3. ``transform_api_error_classification`` turns guardrail phrases in a provider error into a
   content-policy block, so the outcome is recorded as blocked rather than as a generic error.
4. ``transform_llm_output`` judges the reply and, when it is blocked, climbs the ladder
   (:mod:`ladder`). It runs with ``hold_stream`` when tiers are set, so it must never raise and never
   outlive the hook timeout (DESIGN.md invariants 4 and 5).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .attempts import AttemptLog
from .ladder import LadderRunner
from .outcome import TurnSignals
from .refusal import GUARDRAIL_DEFAULTS, matches_guardrail
from .settings import Settings, load_settings

logger = logging.getLogger(__name__)


def default_attempt_log(ctx: Any) -> Optional[AttemptLog]:
    """``attempts.jsonl`` in the plugin's profile data directory, when the host provides one."""
    try:
        data_dir = getattr(getattr(ctx, "state", None), "data_dir", None)
        return AttemptLog(data_dir / "attempts.jsonl") if data_dir is not None else None
    except Exception:
        return None


class RouterHooks:
    def __init__(self, ctx: Any, attempt_log: Optional[AttemptLog] = None):
        self.ctx = ctx
        self.signals = TurnSignals()
        self.ladder = LadderRunner(ctx, self.signals, attempt_log or default_attempt_log(ctx))

    def settings(self) -> Settings:
        return load_settings(self.ctx.get_config)

    def on_pre_llm_call(self, session_id: str = "", user_message: str = "", conversation_history: Any = None,
                        turn_id: str = "", task_id: str = "", **kwargs: Any) -> None:
        try:
            if self.settings().ladder_active:
                self.ladder.note_turn(turn_id or task_id, session_id, user_message, conversation_history)
        except Exception:
            logger.exception("model-router: could not record the turn")
        return None

    def on_post_api_request(self, turn_id: str = "", task_id: str = "", provider: str = "", model: str = "",
                            **kwargs: Any) -> None:
        self.signals.record_success(turn_id or task_id, provider, model)
        return None

    def on_api_request_error(self, turn_id: str = "", task_id: str = "", provider: str = "",
                             reason: str = "", retryable: Any = None, error: Any = None,
                             **kwargs: Any) -> None:
        self.signals.record_error(turn_id or task_id, reason=reason, retryable=retryable,
                                  status_code=kwargs.get("status_code"), error=error, provider=provider,
                                  model=kwargs.get("model", ""))
        return None

    def on_classify_error(self, error_message: str = "", error_body: Any = None, **kwargs: Any) -> Optional[dict]:
        s = self.settings()
        patterns = s.extra_guardrail_patterns + (GUARDRAIL_DEFAULTS if s.security_guardrail_defaults else ())
        if not patterns:
            return None
        text = f"{error_message or ''} {error_body if isinstance(error_body, str) else ''}"
        if matches_guardrail(text, patterns):
            return {"reason": "content_policy_blocked", "retryable": False, "should_fallback": True}
        return None

    def on_transform_llm_output(self, response_text: str = "", session_id: str = "", turn_id: str = "",
                                **kwargs: Any) -> Optional[str]:
        try:
            s = self.settings()
        except Exception:  # held replies: an exception here would withhold the reply (invariant 5)
            logger.exception("model-router: could not read settings; keeping the reply")
            return None
        if not s.ladder_active:
            return None
        return self.ladder.handle(response_text, turn_id=turn_id, session_id=session_id, settings=s,
                                  model_hint=str(kwargs.get("model") or ""))
