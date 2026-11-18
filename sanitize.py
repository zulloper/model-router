"""Framing glossary, locked facts and restore — the text tools behind :mod:`enrich`.

``substitute`` rewords framing terms in prose only (``exploit`` -> ``proof-of-concept code`` …);
code blocks, inline code, identifiers, versions and offsets are left alone. Every substitution is
recorded so ``restore`` can put the user's terms back in the answer. Facts (CVE ids, versions,
offsets, hashes, symbols) are never substituted; ``extract_facts`` finds them and ``missing_facts``
checks the answer for them.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

_L = r"(?<![A-Za-z0-9_])"  # ASCII boundary: Korean particles after a term must not block it
_R = r"(?![A-Za-z0-9_])"

# Only prose/framing terms. Identifiers, versions and offsets are left alone.
_GLOSSARY: tuple[tuple[re.Pattern, str], ...] = tuple(
    (re.compile(rx, re.I), repl) for rx, repl in (
        (rf"{_L}full[- ]working exploit{_R}", "complete runnable proof-of-concept code"),
        (rf"{_L}weaponiz(e|ed|ing){_R}", "turn into a runnable proof-of-concept"),
        (rf"{_L}exploits?{_R}", "proof-of-concept code"),
        (rf"{_L}reverse shell{_R}", "TCP callback shell"),
        (rf"{_L}bind shell{_R}", "TCP listener shell"),
        (rf"{_L}bypass(?:ing)? ASLR{_R}", "work around address randomization"),
        (rf"{_L}bypass(?:ing)? (?:NX|DEP){_R}", "work around non-executable memory"),
        (rf"{_L}0[- ]day{_R}", "unpatched vulnerability"),
        (rf"{_L}red ?team(?:ing)?{_R}", "security assessment"),
        (rf"{_L}pwning{_R}", "exploitation"),
        (r"익스플로잇", "proof-of-concept"),
        (r"공격\s?코드", "PoC 코드"),
        (r"공격\s?시나리오", "재현 시나리오"),
        (r"권한\s?상승", "privilege escalation"),
    ))
_CODE_SPLIT = re.compile(r"(```.*?(?:```|$)|`[^`\n]*`)", re.S)

_FACT_PATTERNS = tuple(re.compile(p) for p in (
    rf"{_L}CVE-\d{{4}}-\d{{4,}}{_R}", rf"{_L}CWE-\d+{_R}", rf"{_L}GHSA-[a-z0-9-]{{10,}}{_R}",
    rf"{_L}\d+\.\d+\.\d+[a-z0-9]*{_R}", rf"{_L}\d+\.\d+[a-z]\d*{_R}",
    rf"{_L}0x[0-9a-fA-F]+{_R}", rf"{_L}[a-f0-9]{{32,64}}{_R}", rf"{_L}__\w+{_R}",
    r"`[^`\n]{2,48}`",
))

def _is_code(segment: str) -> bool:
    return segment.startswith("`")


def map_prose(text: str, fn) -> str:
    """Apply ``fn`` to prose only; fenced blocks and inline code are untouched."""
    parts = _CODE_SPLIT.split(text or "")
    return "".join(p if _is_code(p) else fn(p) for p in parts)


def _case_like(sample: str, word: str) -> str:
    if sample.isupper():
        return word.upper()
    if sample[:1].isupper():
        return word[:1].upper() + word[1:]
    return word


def substitute(text: str, applied: dict[str, str]) -> str:
    """L1 text pass. ``applied`` records replacement -> original for restore()."""
    def fn(segment: str) -> str:
        for pattern, replacement in _GLOSSARY:
            def cb(match: re.Match, repl: str = replacement) -> str:
                original = match.group(0)
                applied.setdefault(repl, original)
                return _case_like(original, repl)
            segment = pattern.sub(cb, segment)
        return segment
    return map_prose(text, fn)


def restore(text: str, applied: dict[str, str]) -> str:
    if not applied:
        return text
    pairs = sorted(applied.items(), key=lambda kv: -len(kv[0]))

    def fn(segment: str) -> str:
        for replacement, original in pairs:
            segment = re.sub(rf"{_L}{re.escape(replacement)}{_R}",
                             lambda m, o=original: _case_like(m.group(0), o), segment, flags=re.I)
        return segment
    return map_prose(text, fn)


def extract_facts(texts: Iterable[str]) -> list[str]:
    facts: list[str] = []
    for text in texts:
        for pattern in _FACT_PATTERNS:
            for match in pattern.findall(text or ""):
                value = match.strip("`")
                if value and value.lower() not in (f.lower() for f in facts):
                    facts.append(value)
                    if len(facts) >= 30:
                        return facts
    return facts


def missing_facts(facts: list[str], answer: str) -> list[str]:
    low = (answer or "").lower()
    return [f for f in facts if f.lower() not in low]


def sanitize_items(items: list, applied: dict[str, str]) -> list:
    """Copy of a messages/input list with prose substituted in user/assistant strings."""
    out = []
    for item in items:
        if not isinstance(item, dict):
            out.append(item)
            continue
        roles = item.get("role")
        content = item.get("content")
        if roles in ("system", "developer"):
            out.append(item)
        elif isinstance(content, str):
            out.append({**item, "content": substitute(content, applied)})
        elif isinstance(content, list):
            out.append({**item, "content": [
                ({**block, "text": substitute(block.get("text", ""), applied)}
                 if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
                 else block)
                for block in content]})
        else:
            out.append(item)
    return out
