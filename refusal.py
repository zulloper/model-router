"""Soft-refusal detection: a normal reply whose text declines the task.

Hermes already catches hard refusals (``finish_reason: content_filter``, Anthropic ``refusal``,
Bedrock guardrails). This catches the polite kind. It is deliberately conservative: only short
replies whose opening declines count, so a long answer that mentions "I can't" in passing does not.

``task_class == "sec"`` widens the net, because security answers decline differently: they are
longer, and they often promise a "conceptual overview" or declare the code proprietary/under NDA
instead of saying "I can't". ``looks_like_degraded`` is the other half for that class: an answer
that was asked for a PoC and contains no code at all is a soft failure even when it is polite.
"""

from __future__ import annotations

import re
from typing import Optional

_MAX_LEN = 900
_SEC_MAX_LEN = 2400
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
_SEC_PATTERNS = tuple(re.compile(p, re.I) for p in (
    r"\b(?:full |complete |working )?(?:exploit|poc|proof[- ]of[- ]concept)(?: code)? (?:is |remains )?"
    r"(?:not |un)?(?:publicly )?(?:available|disclosed|documented|released)",
    r"\bproprietary\b", r"\bunder nda\b", r"\bnda\b",
    r"(?:개념|개요|원리)(?:만| 위주로| 수준으로| 정도로).{0,40}(?:설명|정리|다루)",
    r"(?:전체|상세한|완전한|실제).{0,16}(?:코드|익스플로잇|공격 코드|poc).{0,24}(?:생략|제외|다루지|설명만|제공하지)",
    r"(?:공격|익스플로잇|poc)\s*코드(?:는|를)?.{0,24}(?:제공|작성|수록)(?:하기|해 ?드리기|할)? ?(?:어렵|불가|생략)",
    r"(?:코드|페이로드|쉘코드)(?:는|를)?.{0,20}(?:개념|의사 ?코드|슈도|pseudo|간략)[^.]{0,20}(?:설명|정리|제시)",
))

# Request-side: the user asked for code or another runnable artifact. A code noun plus a verb that
# asks to produce it; "작성해줘" alone (an email, a plan) or "코드 설명해줘" (explain) does not count.
_CODE_NOUN = (r"(?:\bpoc\b|exploit|payload|shellcode|익스플로잇|공격 ?코드|쉘코드|페이로드|코드|스크립트|"
              r"명령어|함수|\bscript\b|\bcode\b|\bprogram\b|\bfunction\b)")
_WANT_CODE = re.compile(
    rf"{_CODE_NOUN}[^.\n]{{0,12}}?(?:작성|짜|만들|구현|써|보여|생성|뽑아|주세요)"
    r"|\b(?:write|give|show|generate|implement|create|build|make)\b(?: me)?"
    r"(?:\s+(?:a|an|the|full|complete|working|runnable|some|python|bash|c|go|rust))*"
    r"\s+(?:code|script|poc|exploit|payload|shellcode|program|function)"
    r"|\b(?:poc|exploit|payload|shellcode)s?\s+(?:for|that|to)\b"
    r"|\bimplement\b|\bcode (?:it|this)\b", re.I)
_CODE_EVIDENCE = re.compile(r"```|\n\s*\$\s|\n\s*0x[0-9a-f]+:|\n\s*#{1,3}\s*(?:PoC|Exploit)|#!/|\.(?:py|c|sh|rb|js)\b|\b(?:pwntools|gdb|gef)\b", re.I)
_HEDGES = tuple(re.compile(p, re.I) for p in (
    r"버전에 따라", r"환경에 따라", r"일반적으로", r"참고로", r"간략히", r"개념적으로", r"의사 ?코드",
    r"예를 들면", r"다를 수 있", r"생략", r"may vary", r"depending on", r"for example", r"note that",
    r"generally", r"outline", r"skeleton", r"pseudo",
))


def wants_code(request: str) -> bool:
    """The request asks for code or another runnable artifact (not just mentions code)."""
    return bool(_WANT_CODE.search(request or ""))


def refusal_match(text: str, security: bool = False, request: Optional[str] = None) -> Optional[str]:
    """The text that made this reply a soft refusal (for the attempt log), or None.

    ``security`` adds the security-style declines ("proprietary", "NDA", "concept only"). Those are
    only refusals of a code request, so when ``request`` is given they count only if it asks for
    code and the reply contains none — "this NDA covers…" summarising a contract is an answer."""
    body = (text or "").strip()
    limit = _SEC_MAX_LEN if security else _MAX_LEN
    if not body or len(body) > limit:
        return None
    head = body[:_HEAD]
    for pattern in _PATTERNS:
        match = pattern.search(head)
        if match:
            return match.group(0)[:120]
    if not security:
        return None
    if request is not None and (not wants_code(request) or _CODE_EVIDENCE.search(body)):
        return None
    for pattern in _SEC_PATTERNS:
        match = pattern.search(head)
        if match:
            return match.group(0)[:120]
    return None


def looks_like_refusal(text: str, task_class: str = "") -> bool:
    return refusal_match(text, task_class == "sec") is not None


def degraded_reason(text: str, request: str, security: bool = False) -> Optional[str]:
    """Why a reply that was asked for code/a PoC counts as prose only (or hedge-heavy), or None.
    Security turns only: elsewhere a prose answer is usually fine."""
    if not security:
        return None
    body = (text or "").strip()
    if len(body) < 120 or len(body) > 12000:
        return None
    if not wants_code(request) or _CODE_EVIDENCE.search(body):
        return None
    hedges = sum(1 for p in _HEDGES if p.search(body))
    if hedges >= 2:
        return f"code was asked for; the reply has no code and {hedges} hedges"
    if len(body) < 4000:
        return "code was asked for; the reply has no code"
    return None


def looks_like_degraded(text: str, request: str, task_class: str = "") -> bool:
    return degraded_reason(text, request, task_class == "sec") is not None


def matches_guardrail(message: str, extra_patterns: tuple[str, ...]) -> bool:
    text = (message or "").lower()
    return any(p and p in text for p in extra_patterns)
