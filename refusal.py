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
    r"\b(?:not|isn't|is not) (?:publicly )?(?:available|disclosed|documented)",
    r"\bproprietary\b", r"\bunder nda\b", r"\bnda\b",
    r"(?:개념|개요|원리)(?:만| 위주로| 수준으로| 정도로).{0,40}(?:설명|정리|다루)",
    r"(?:전체|상세한|완전한|실제).{0,16}(?:코드|익스플로잇|공격 코드|poc).{0,24}(?:생략|제외|다루지|설명만|제공하지)",
    r"(?:공격|익스플로잇|poc)\s*코드(?:는|를)?.{0,24}(?:제공|작성|수록)(?:하기|해 ?드리기|할)? ?(?:어렵|불가|생략)",
    r"(?:코드|페이로드|쉘코드)(?:는|를)?.{0,20}(?:개념|의사 ?코드|슈도|pseudo|간략)[^.]{0,20}(?:설명|정리|제시)",
))

# Answer-side signals that a concrete artifact was expected but not delivered.
_WANT_CODE = re.compile(
    r"(?:\bpoc\b|exploit|payload|shellcode|익스플로잇|공격 ?코드|쉘코드|페이로드|코드|명령어|작성|써 ?줘|짜 ?줘|"
    r"implement|write (?:me )?(?:a |the )?(?:code|script|poc)|code (?:it|this))", re.I)
_CODE_EVIDENCE = re.compile(r"```|\n\s*\$\s|\n\s*0x[0-9a-f]+:|\n\s*#{1,3}\s*(?:PoC|Exploit)|#!/|\.(?:py|c|sh|rb|js)\b|\b(?:pwntools|gdb|gef)\b", re.I)
_HEDGES = tuple(re.compile(p, re.I) for p in (
    r"버전에 따라", r"환경에 따라", r"일반적으로", r"참고로", r"간략히", r"개념적으로", r"의사 ?코드",
    r"예를 들면", r"다를 수 있", r"생략", r"may vary", r"depending on", r"for example", r"note that",
    r"generally", r"outline", r"skeleton", r"pseudo",
))


def looks_like_refusal(text: str, task_class: str = "") -> bool:
    body = (text or "").strip()
    limit = _SEC_MAX_LEN if task_class == "sec" else _MAX_LEN
    if not body or len(body) > limit:
        return False
    head = body[:_HEAD]
    if any(p.search(head) for p in _PATTERNS):
        return True
    return task_class == "sec" and any(p.search(head) for p in _SEC_PATTERNS)


def looks_like_degraded(text: str, request: str, task_class: str = "") -> bool:
    """The task asked for code/a PoC but the reply is prose only (or hedge-heavy). Security turns
    only: elsewhere a prose answer is usually fine."""
    if task_class != "sec":
        return False
    body = (text or "").strip()
    if len(body) < 120 or len(body) > 12000:
        return False
    if not _WANT_CODE.search(request or "") or _CODE_EVIDENCE.search(body):
        return False
    hedges = sum(1 for p in _HEDGES if p.search(body))
    return hedges >= 2 or len(body) < 4000


def matches_guardrail(message: str, extra_patterns: tuple[str, ...]) -> bool:
    text = (message or "").lower()
    return any(p and p in text for p in extra_patterns)
