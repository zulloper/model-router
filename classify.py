"""Task classification: which task class (quick / standard / code / deep) a user turn is.

``rules`` is free and instant: keyword and length signals in Korean and English. ``hybrid`` asks a
model only when the rules are unsure; ``llm`` always asks. A model call that fails or times out falls
back to the rules' answer, so classification never blocks a turn.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Optional

from .scope import TASK_CLASSES

logger = logging.getLogger(__name__)

_DEEP = (
    "설계", "아키텍처", "리팩터", "리팩토링", "근본 원인", "원인 분석", "분석해", "최적화", "증명", "알고리즘",
    "마이그레이션", "전략", "트레이드오프", "비교 분석", "심층", "깊게", "꼼꼼", "단계별", "계획 세워", "보안 검토",
    "architecture", "design a", "refactor", "root cause", "optimi", "prove", "algorithm", "migrat",
    "trade-off", "tradeoff", "in depth", "in-depth", "step by step", "thorough", "security review",
)
_CODE = (
    "코드", "코딩", "함수", "버그", "디버그", "디버깅", "에러", "오류", "테스트", "빌드", "컴파일", "스크립트",
    "구현", "커밋", "리뷰", "api", "sql", "regex", "정규식", "파이썬", "python", "typescript", "javascript",
    "code", "function", "bug", "debug", "error", "stack trace", "traceback", "test", "build", "compile",
    "script", "implement", "commit", "pull request", "endpoint", "class ", "def ", "import ",
)
_QUICK = (
    "번역", "요약", "맞춤법", "한 줄", "한줄", "간단히", "짧게", "뭐야", "뜻이", "알려줘", "몇 시", "날씨",
    "translate", "summarize", "summary", "tl;dr", "tldr", "spell", "one line", "briefly", "what is", "define",
)
_CODE_FENCE = re.compile(r"```|^\s{4,}\S|\b\w+\(\)|\w+\.\w+\(", re.M)


def _hits(text: str, words: tuple[str, ...]) -> int:
    return sum(1 for w in words if w in text)


def classify_rules(message: str) -> tuple[str, float, str]:
    """-> (task class, confidence 0..1, why)."""
    text = (message or "").lower()
    n = len(text)
    deep, code, quick = _hits(text, _DEEP), _hits(text, _CODE), _hits(text, _QUICK)
    fenced = bool(_CODE_FENCE.search(message or ""))
    if deep >= 2 or (deep and (n > 400 or code)) or n > 1500:
        return "deep", 0.8 if deep >= 2 or n > 1500 else 0.65, f"deep signals={deep}, length={n}"
    if code or fenced:
        conf = 0.8 if code >= 2 or fenced else 0.6
        return "code", conf, f"code signals={code}, code-like text={fenced}"
    if deep:
        return "deep", 0.55, f"deep signals={deep}"
    if quick and n < 300:
        return "quick", 0.8, f"quick signals={quick}, length={n}"
    if n < 40:
        return "quick", 0.6, f"short message ({n} chars)"
    return "standard", 0.5, "no strong signal"


_SCHEMA = {
    "type": "object",
    "properties": {
        "task_class": {"type": "string", "enum": list(TASK_CLASSES)},
        "reason": {"type": "string"},
    },
    "required": ["task_class"],
    "additionalProperties": False,
}
_INSTRUCTIONS = (
    "Classify the user's request for routing to an AI model. Answer with one task_class:\n"
    "- quick: short lookups, translation, summaries, small edits; little reasoning needed.\n"
    "- standard: ordinary writing, explanation or planning of moderate size.\n"
    "- code: writing, fixing, reviewing or running code.\n"
    "- deep: hard multi-step reasoning, architecture, research, root-cause analysis, long tasks.\n"
    "Use the recent context only to understand what the request refers to."
)


def classify(message: str, history: Any, method: str,
             llm_call: Optional[Callable[..., Any]] = None) -> tuple[str, str]:
    """-> (task class, why). ``llm_call`` is ``ctx.llm.complete_structured``."""
    cls, conf, why = classify_rules(message)
    if method == "rules" or llm_call is None or (method == "hybrid" and conf >= 0.65):
        return cls, f"rules: {why}"
    recent = []
    for msg in (history or [])[-4:]:
        if isinstance(msg, dict) and msg.get("role") in ("user", "assistant") and isinstance(msg.get("content"), str):
            recent.append(f"{msg['role']}: {msg['content'][:400]}")
    try:
        res = llm_call(instructions=_INSTRUCTIONS,
                       input={"request": (message or "")[:4000], "recent_context": recent},
                       json_schema=_SCHEMA, schema_name="task_class", max_tokens=200, timeout=15,
                       purpose="model-router task classification")
        parsed = getattr(res, "parsed", None) or {}
        got = parsed.get("task_class") if isinstance(parsed, dict) else None
        if got in TASK_CLASSES:
            return got, f"llm: {parsed.get('reason') or got}"
    except Exception as exc:  # classification must never fail a turn
        logger.debug("model-router: LLM classification failed: %s", exc)
    return cls, f"rules (llm unavailable): {why}"
