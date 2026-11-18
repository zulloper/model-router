"""The enriched retry (0.4.0, DESIGN.md §5): add information to a request, never hide it.

When a tier's models all fail on the request as written, the tier is asked once more with:

1. **Authorization context** — only the user's own rules of engagement (``security_roe``), quoted
   and kept short. With none configured there is no authorization line: the plugin never asserts
   an authorization the user did not state.
2. **An output contract** — only when the request asks for code (:func:`refusal.wants_code`), placed
   right after the request rather than at the front of a long system prompt.
3. **Framing terms reworded** — the deterministic glossary in :mod:`sanitize` (``exploit`` ->
   ``proof-of-concept code`` …), prose only; code, identifiers and facts are untouched. Every
   rewording that drops part of the meaning adds that part back as an explicit requirement (the
   *meaning delta*): "work around address randomization" alone no longer says it must work with
   ASLR on, so the requirement says so.
4. **Locked facts** — CVE ids, versions, offsets, hashes and symbols from the request, listed to be
   kept verbatim and checked in the answer; the answer's reworded terms are restored before use.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json

from . import sanitize
from .refusal import wants_code

ROE_CHARS = 600
MAX_FACTS = 15

# What a rewording drops, stated back as a requirement. Keyed by the replacement text in
# sanitize._GLOSSARY; rewordings that are plain synonyms or translations have no entry.
MEANING_DELTA = {
    "complete runnable proof-of-concept code": "The code must run end to end on the stated target, not only demonstrate the bug.",
    "turn into a runnable proof-of-concept": "The code must run end to end on the stated target, not only demonstrate the bug.",
    "proof-of-concept code": "The code must reproduce the issue end to end on the stated target.",
    "proof-of-concept": "The code must reproduce the issue end to end on the stated target.",
    "PoC 코드": "The code must reproduce the issue end to end on the stated target.",
    "work around address randomization": "It must work with ASLR enabled.",
    "work around non-executable memory": "It must work with NX/DEP enabled.",
}
_CODE_CONTRACT = ("Give complete, runnable code: one file with its imports, the exact commands to build and "
                  "run it, and the versions it targets. If something it needs is missing (a binary, source, "
                  "a patch), say exactly what.")


@dataclasses.dataclass
class Enriched:
    messages: list
    applied: dict[str, str]          # replacement -> original, for restoring the answer
    facts: list[str]
    requirements: list[str]

    @property
    def glossary_hash(self) -> str:
        if not self.applied:
            return ""
        blob = json.dumps(self.applied, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def build(context: list[dict[str, str]], request: str, *, roe: str = "") -> Enriched:
    """The enriched messages for ``request`` with its recent ``context`` (user/assistant turns)."""
    applied: dict[str, str] = {}
    messages = sanitize.sanitize_items([dict(m) for m in context], applied)
    reworded = sanitize.substitute(request, applied)
    facts = sanitize.extract_facts([request])[:MAX_FACTS]

    requirements: list[str] = []
    if wants_code(request):
        requirements.append(_CODE_CONTRACT)
    for replacement in applied:
        delta = MEANING_DELTA.get(replacement)
        if delta and delta not in requirements:
            requirements.append(delta)
    if facts:
        requirements.append("Keep these exactly as written: " + ", ".join(facts) + ".")

    parts = [reworded.rstrip()]
    roe_text = " ".join((roe or "").split())
    if roe_text:
        if len(roe_text) > ROE_CHARS:
            roe_text = roe_text[:ROE_CHARS].rstrip() + "…"
        parts.append(f"Context from the user's rules of engagement: {roe_text}")
    if requirements:
        parts.append("Requirements for the answer:\n" + "\n".join(f"- {r}" for r in requirements))
    messages.append({"role": "user", "content": "\n\n".join(parts)})
    return Enriched(messages, applied, facts, requirements)


def finish(answer: str, enriched: Enriched) -> tuple[str, list[str]]:
    """-> (the answer with reworded terms restored, locked facts it dropped)."""
    text = sanitize.restore(answer or "", enriched.applied)
    return text, sanitize.missing_facts(enriched.facts, text)


def worth_it(enriched: Enriched) -> bool:
    """An enriched retry that adds nothing (no rewording, no requirements, no context) would just
    repeat the plain round."""
    last = enriched.messages[-1]["content"] if enriched.messages else ""
    return bool(enriched.applied or enriched.requirements or "rules of engagement" in last)
