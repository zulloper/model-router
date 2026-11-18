"""Soft-refusal detection: a normal reply whose text declines the task.

Hermes already catches hard refusals (``finish_reason: content_filter``, Anthropic ``refusal``,
Bedrock guardrails). This catches the polite kind. It is deliberately conservative: only short
replies whose opening declines count, so a long answer that mentions "I can't" in passing does not.
"""

from __future__ import annotations

import re

_MAX_LEN = 900
_HEAD = 320
_PATTERNS = tuple(re.compile(p, re.I) for p in (
    r"\bI(?:'m| am)? (?:sorry,? (?:but )?)?(?:can(?:no|')t|cannot|won't|will not|am not able to|'m not able to|am unable to|'m unable to) "
    r"(?:help|assist|comply|provide|do that|fulfil|fulfill|complete|support|continue|create|write|generate)",
    r"\bI(?:'m| am) not (?:able|allowed|permitted) to (?:help|assist|provide|do)",
    r"\b(?:this|that|your) request (?:violates|goes against|is against)",
    r"\bI must (?:decline|refuse)",
    r"죄송(?:하지만|합니다만|하지만요).{0,40}(?:수 없|어렵|불가)",
    r"(?:도와|도움을 )?(?:드릴|드리기) 수 없",
    r"(?:제공|응답|작성|생성|수행|진행|답변)(?:해 드릴|해드릴|할|하기|드릴)? ?수 없(?:습니다|어요|음)",
    r"(?:정책|가이드라인|안전).{0,20}(?:위반|때문에).{0,30}(?:수 없|어렵)",
))


def looks_like_refusal(text: str) -> bool:
    body = (text or "").strip()
    if not body or len(body) > _MAX_LEN:
        return False
    head = body[:_HEAD]
    return any(p.search(head) for p in _PATTERNS)


def matches_guardrail(message: str, extra_patterns: tuple[str, ...]) -> bool:
    text = (message or "").lower()
    return any(p and p in text for p in extra_patterns)
