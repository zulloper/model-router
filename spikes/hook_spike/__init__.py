"""Spike plugin for DESIGN.md §11 S1-S3. Inert unless the user message carries a marker:

    [mr-spike replace]      S1/S2: in transform_llm_output, call ctx.llm.complete on the active model
                            and return a replacement reply; logs the call's latency.
    [mr-spike sleep=N]      S3: sleep N seconds in transform_llm_output, then return a replacement
                            (does it outlive plugins.hook_callback_timeout?).
    [mr-spike apierror]     S2c: send this turn's provider call to a model that does not exist, so
                            the turn ends on a real provider error; transform then tries a
                            replacement (does it reach the user?).

Turns run in the gateway process, so hold_stream is switched with a flag file: create
~/.hermes/logs/mr-spike.hold (or set MR_SPIKE_HOLD=1 for that process) and reload the plugin. Every event is appended as one JSON line to ~/.hermes/logs/mr-spike.jsonl.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

_MARK = re.compile(r"\[mr-spike\s+([a-z]+)(?:=(\d+))?\]")
_LOG = Path(os.path.expanduser(os.environ.get("MR_SPIKE_LOG", "~/.hermes/logs/mr-spike.jsonl")))
_LOCK = threading.Lock()
_TURNS: "OrderedDict[str, tuple[str, int]]" = OrderedDict()  # turn/session -> (mode, arg)


def _log(event: str, **fields: Any) -> None:
    row = {"t": round(time.time(), 3), "event": event, "pid": os.getpid(),
           "thread": threading.current_thread().name, **fields}
    try:
        with _LOCK, _LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def _remember(keys: list[str], mode: str, arg: int) -> None:
    with _LOCK:
        for key in keys:
            if key:
                _TURNS[key] = (mode, arg)
                _TURNS.move_to_end(key)
        while len(_TURNS) > 64:
            _TURNS.popitem(last=False)


def _lookup(*keys: str) -> tuple[str, int] | None:
    with _LOCK:
        for key in keys:
            if key and key in _TURNS:
                return _TURNS[key]
    return None


def _parent_visible() -> bool | str:
    try:
        from agent.subagent_lifecycle import get_active_subagent_parent
        return get_active_subagent_parent() is not None
    except Exception as exc:
        return f"error: {exc}"


def register(ctx) -> None:
    hold = (os.environ.get("MR_SPIKE_HOLD") == "1"
            or Path(os.path.expanduser("~/.hermes/logs/mr-spike.hold")).exists())

    def on_pre_llm_call(session_id: str = "", user_message: str = "", turn_id: str = "",
                        task_id: str = "", **kwargs: Any) -> None:
        match = _MARK.search(user_message or "")
        if not match:
            return None
        mode, arg = match.group(1), int(match.group(2) or 0)
        _remember([turn_id, task_id, session_id], mode, arg)
        _log("pre_llm_call", mode=mode, arg=arg, turn_id=turn_id, session_id=session_id, hold=hold)
        return None

    def on_llm_execution(request: Any = None, next_call: Any = None, turn_id: str = "",
                         task_id: str = "", session_id: str = "", **kwargs: Any) -> Any:
        found = _lookup(turn_id, task_id, session_id)
        if not found or found[0] != "apierror" or not isinstance(request, dict):
            return next_call(request)
        broken = dict(request, model="mr-spike-no-such-model")
        _log("llm_execution_break", turn_id=turn_id, original_model=request.get("model"))
        return next_call(broken)

    def on_transform_llm_output(response_text: str = "", session_id: str = "", turn_id: str = "",
                                **kwargs: Any) -> str | None:
        found = _lookup(turn_id, session_id)
        if not found:
            return None
        mode, arg = found
        started = time.monotonic()
        base = {"mode": mode, "turn_id": turn_id, "hold": hold, "parent_visible": _parent_visible(),
                "original_head": (response_text or "")[:160], "kwargs": sorted(kwargs)}
        _log("transform_start", **base)
        if mode == "sleep":
            time.sleep(arg)
            _log("transform_return", mode=mode, turn_id=turn_id, elapsed_s=round(time.monotonic() - started, 2))
            return f"SPIKE-SLEPT-{arg}s (replacement reached the user)"
        if mode in ("replace", "apierror"):
            try:
                res = ctx.llm.complete([{"role": "user", "content": "Reply with exactly: SPIKE-REPLACED"}],
                                       timeout=120, purpose="mr-spike S1 latency")
                text = str(getattr(res, "text", "") or "").strip()
                err = None
            except Exception as exc:
                text, err = "", repr(exc)
            ms = int((time.monotonic() - started) * 1000)
            _log("transform_return", mode=mode, turn_id=turn_id, llm_ms=ms, llm_text=text[:80], llm_error=err)
            return f"SPIKE-REPLACED by ctx.llm in {ms} ms: {text[:80] or err}"
        return None

    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("transform_llm_output", on_transform_llm_output, hold_stream=hold)
    ctx.register_middleware("llm_execution", on_llm_execution)
    _log("registered", hold=hold)
