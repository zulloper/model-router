import json

import pytest

from model_router_plugin.attempts import ENRICH, ORIGIN, PLAIN, Attempt, AttemptLog, cap_prompt, prompt_hash


def make(index=1, kind=PLAIN, outcome="ok", **kw):
    base = dict(turn_id="t1", session_id="s1", kind=kind, tier=None if kind == ORIGIN else 0, index=index,
                provider="anthropic", model="claude-opus-4-8", outcome=outcome)
    base.update(kw)
    return Attempt(**base)


def test_attempt_rejects_unknown_kind_and_outcome():
    with pytest.raises(ValueError):
        make(kind="guess")
    with pytest.raises(ValueError):
        make(outcome="fine")


def test_attempt_round_trips_and_tolerates_newer_fields():
    a = make(kind=ENRICH, outcome="blocked", reason="refusal: I can't help", prompt=[{"role": "user", "content": "x"}],
             facts_expected=["CVE-2024-3094"], facts_missing=["CVE-2024-3094"], latency_ms=1200)
    data = a.to_json() | {"added_in_v2": True}
    back = Attempt.from_json(data)
    assert back == a and back.key == "anthropic/claude-opus-4-8"


def test_invalid_stored_record_is_skipped_not_raised():
    assert Attempt.from_json({"turn_id": "t", "kind": "bogus"}) is None


def test_prompt_hash_ignores_key_order_and_changes_with_content():
    m1 = [{"role": "user", "content": "hi"}]
    m2 = [{"content": "hi", "role": "user"}]
    assert prompt_hash(m1) == prompt_hash(m2)
    assert prompt_hash(m1) != prompt_hash([{"role": "user", "content": "hi!"}])


def test_cap_prompt_cuts_long_contents_and_says_so():
    capped = cap_prompt([{"role": "user", "content": "a" * 50}, "raw"], 10)
    assert capped[0]["content"].startswith("a" * 10) and "+40 chars" in capped[0]["content"]
    assert capped[1] == "raw"


def test_log_appends_and_reads_oldest_first(tmp_path):
    log = AttemptLog(tmp_path / "attempts.jsonl")
    for i in range(3):
        assert log.append(make(index=i, kind=ORIGIN if i == 0 else PLAIN))
    got = log.recent()
    assert [a.index for a in got] == [0, 1, 2]
    assert log.recent(limit=2)[0].index == 1


def test_log_caps_stored_prompts(tmp_path):
    log = AttemptLog(tmp_path / "attempts.jsonl", max_prompt_chars=5)
    log.append(make(prompt=[{"role": "user", "content": "0123456789"}]))
    stored = json.loads((tmp_path / "attempts.jsonl").read_text())
    assert stored["prompt"][0]["content"].startswith("01234…")


def test_log_rotates_by_size_and_keeps_a_fixed_number_of_backups(tmp_path):
    path = tmp_path / "data" / "attempts.jsonl"
    log = AttemptLog(path, max_bytes=600, backups=2)
    for i in range(12):
        log.append(make(index=i))
    files = sorted(p.name for p in path.parent.iterdir())
    assert files == ["attempts.jsonl", "attempts.jsonl.1", "attempts.jsonl.2"]
    got = log.recent(limit=100)
    assert [a.index for a in got] == sorted(a.index for a in got)  # oldest first across files
    assert got[-1].index == 11


def test_log_skips_corrupt_lines(tmp_path):
    path = tmp_path / "attempts.jsonl"
    log = AttemptLog(path)
    log.append(make(index=1))
    with open(path, "a") as fh:
        fh.write("{not json\n")
    log.append(make(index=2))
    assert [a.index for a in log.recent()] == [1, 2]


def test_log_write_failure_is_reported_not_raised(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    log = AttemptLog(blocker / "attempts.jsonl")  # parent is a file: mkdir fails
    assert log.append(make()) is False
