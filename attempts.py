"""Attempt records (0.4.0, DESIGN.md §6 and §6.1): one line per attempt, for reversibility and stats.

An attempt is one answer to one blocked turn: the profile model's own reply (``origin``), or a
ladder retry with the request as written (``plain``) or enriched (``enrich``). Each record keeps
what was actually sent, so a reviewer can rebuild why an answer was delivered (invariant 3), and
the fields promotion needs (§6.1): outcomes kept apart, and ``delivered`` so a late result from
abandoned work never counts.

Records go to ``attempts.jsonl`` in the plugin's profile data directory, append-only and rotated by
size. Prompts carry user code and host names, so they are capped per record and the file is rotated
with a fixed number of backups.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from .outcome import OUTCOMES

logger = logging.getLogger(__name__)

ORIGIN, PLAIN, ENRICH = "origin", "plain", "enrich"
KINDS = (ORIGIN, PLAIN, ENRICH)
SCHEMA_VERSION = 1


@dataclasses.dataclass
class Attempt:
    turn_id: str
    session_id: str
    kind: str                       # origin | plain | enrich
    tier: Optional[int]             # 0-based ladder tier; None for origin
    index: int                      # order within the turn; origin is 0
    provider: str
    model: str
    outcome: str                    # ok | blocked | degraded | error
    reason: str = ""                # what decided the outcome (error reason, matched text)
    prompt_hash: str = ""
    glossary_hash: str = ""
    prompt: Optional[list] = None   # messages actually sent (capped); None for origin
    facts_expected: list = dataclasses.field(default_factory=list)
    facts_missing: list = dataclasses.field(default_factory=list)
    won_synthesis: bool = False
    delivered: bool = False         # this answer (or a synthesis using it) reached the user
    latency_ms: Optional[int] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    started_at: float = dataclasses.field(default_factory=time.time)
    v: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"attempt kind must be one of {KINDS}, not {self.kind!r}")
        if self.outcome not in OUTCOMES:
            raise ValueError(f"attempt outcome must be one of {OUTCOMES}, not {self.outcome!r}")

    @property
    def key(self) -> str:
        return f"{self.provider}/{self.model}"

    def to_json(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Optional["Attempt"]:
        """Tolerant read: unknown keys are ignored (newer writers), a record that no longer validates
        is skipped (None) instead of breaking a stats read."""
        names = {f.name for f in dataclasses.fields(cls)}
        try:
            return cls(**{k: v for k, v in data.items() if k in names})
        except (TypeError, ValueError):
            return None


def prompt_hash(messages: Any) -> str:
    """Stable id of what was sent: same messages -> same hash, whatever the dict key order."""
    blob = json.dumps(messages, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def cap_prompt(messages: Iterable[Any], max_chars: int) -> list:
    """Messages with each string content cut to ``max_chars`` (a marker shows what was cut)."""
    out = []
    for msg in messages or []:
        if isinstance(msg, Mapping) and isinstance(msg.get("content"), str) and len(msg["content"]) > max_chars:
            cut = len(msg["content"]) - max_chars
            msg = {**msg, "content": msg["content"][:max_chars] + f"…[+{cut} chars]"}
        out.append(dict(msg) if isinstance(msg, Mapping) else msg)
    return out


class AttemptLog:
    """Append-only JSON-lines log with size rotation (``attempts.jsonl``, ``.1`` … ``.N``)."""

    def __init__(self, path: Path, *, max_bytes: int = 5 * 1024 * 1024, backups: int = 3,
                 max_prompt_chars: int = 16000) -> None:
        self.path = Path(path)
        self.max_bytes, self.backups, self.max_prompt_chars = max_bytes, max(0, backups), max_prompt_chars
        self._lock = threading.Lock()

    def append(self, attempt: Attempt) -> bool:
        """Write one record. Never raises: a log that can't be written must not cost the turn."""
        record = attempt.to_json()
        if record.get("prompt") is not None:
            record["prompt"] = cap_prompt(record["prompt"], self.max_prompt_chars)
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._rotate_if_needed(len(line.encode("utf-8")))
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line)
            return True
        except Exception as exc:
            logger.warning("model-router: could not write the attempt log: %s", exc)
            return False

    def _rotate_if_needed(self, incoming: int) -> None:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return
        if size + incoming <= self.max_bytes:
            return
        if self.backups == 0:
            self.path.unlink(missing_ok=True)
            return
        oldest = self._backup(self.backups)
        oldest.unlink(missing_ok=True)
        for n in range(self.backups - 1, 0, -1):
            src = self._backup(n)
            if src.exists():
                os.replace(src, self._backup(n + 1))
        os.replace(self.path, self._backup(1))

    def _backup(self, n: int) -> Path:
        return self.path.with_name(f"{self.path.name}.{n}")

    def recent(self, limit: int = 500) -> list[Attempt]:
        """Up to ``limit`` most recent records, oldest first. Reads backups only as needed."""
        files = [self.path] + [self._backup(n) for n in range(1, self.backups + 1)]
        chunks: list[list[Attempt]] = []
        total = 0
        with self._lock:
            for file in files:
                if total >= limit:
                    break
                try:
                    lines = file.read_text(encoding="utf-8").splitlines()
                except FileNotFoundError:
                    continue
                except Exception as exc:
                    logger.warning("model-router: could not read %s: %s", file, exc)
                    continue
                parsed = []
                for line in lines:
                    try:
                        attempt = Attempt.from_json(json.loads(line))
                    except ValueError:
                        attempt = None
                    if attempt is not None:
                        parsed.append(attempt)
                chunks.append(parsed)
                total += len(parsed)
        merged = [a for chunk in reversed(chunks) for a in chunk]
        return merged[-limit:]
