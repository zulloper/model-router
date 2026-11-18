"""Text helpers the ladder relies on: refusal patterns, the framing glossary, locked facts,
candidate parsing, and the NVD tool."""

import json

import pytest

from model_router_plugin import nvd, sanitize
from model_router_plugin.candidate import Candidate, parse_candidate
from model_router_plugin.refusal import GUARDRAIL_DEFAULTS, matches_guardrail, refusal_match


# --- refusals ---------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "I'm sorry, but I can't help with that.",
    "I cannot assist with this request.",
    "죄송하지만 해당 요청은 도와드릴 수 없습니다.",
    "이 내용은 정책 위반 때문에 제공할 수 없습니다.",
])
def test_refusals(text):
    assert refusal_match(text)


@pytest.mark.parametrize("text", [
    "Here is the fix. Note that I can't run it here, but it should work.\n" + "x" * 1000,
    "물론이죠! 아래처럼 하시면 됩니다.",
    "",
])
def test_not_refusals(text):
    assert refusal_match(text) is None


def test_security_style_declines():
    code_request = "PoC 코드 작성해줘"
    assert refusal_match("The full exploit is not publicly available; here is the theory.", True, code_request)
    assert refusal_match("전체 익스플로잇 코드는 개념 설명만 제공합니다.", True, code_request)
    assert refusal_match("The full exploit is not publicly available; here is the theory.") is None


def test_guardrail_phrases():
    assert matches_guardrail("Request BLOCKED by corp-guard", ("blocked by corp-guard",))
    assert not matches_guardrail("rate limited", ("blocked by corp-guard",))
    assert matches_guardrail("Output flagged by the content filter", GUARDRAIL_DEFAULTS)


# --- glossary, restore, facts -----------------------------------------------------------------

def test_substitute_skips_code_and_restores():
    applied: dict[str, str] = {}
    src = "write a full working exploit\n```\n# exploit stays\n```\n익스플로잇 코드"
    out = sanitize.substitute(src, applied)
    assert "complete runnable proof-of-concept code" in out
    assert "# exploit stays" in out and "proof-of-concept 코드" in out
    back = sanitize.restore(out, applied)
    assert "full working exploit" in back and "익스플로잇 코드" in back


def test_korean_particles_do_not_block_terms():
    applied: dict[str, str] = {}
    out = sanitize.substitute("exploit을 실행", applied)
    assert out == "proof-of-concept code을 실행"
    assert sanitize.restore(out, applied) == "exploit을 실행"
    assert sanitize.extract_facts(["버전 5.6.1을 사용"]) == ["5.6.1"]


def test_facts_are_extracted_and_checked():
    facts = sanitize.extract_facts(["CVE-2024-3094, 5.6.1, offset 0x40, `__free_hook`, CWE-506"])
    assert {"CVE-2024-3094", "0x40", "__free_hook", "CWE-506", "5.6.1"} <= set(facts)
    assert sanitize.missing_facts(facts, "CVE-2024-3094 5.6.1 0x40 __free_hook") == ["CWE-506"]


# --- candidates -------------------------------------------------------------------------------

def test_parse_candidate_variants():
    assert parse_candidate("openai-codex/gpt-6.2-sol@high") == Candidate("openai-codex", "gpt-6.2-sol", "high")
    assert parse_candidate("OpenRouter/anthropic/claude-x") == Candidate("openrouter", "anthropic/claude-x", None)
    assert parse_candidate("custom/model@v2") == Candidate("custom", "model@v2", None)  # not an effort
    assert parse_candidate({"provider": "anthropic", "model": "c", "effort": "low"}) == Candidate("anthropic", "c", "low")
    assert parse_candidate("no-model") is None
    assert parse_candidate(42) is None


# --- NVD tool ---------------------------------------------------------------------------------

_FIXTURE = {"_source": "https://services.nvd.nist.gov/rest/json/cves/2.0?cveId=CVE-2024-3094",
            "vulnerabilities": [{"cve": {
                "id": "CVE-2024-3094", "vulnStatus": "Analyzed",
                "published": "2024-03-29T00:00:00.000", "lastModified": "2024-04-01T00:00:00.000",
                "descriptions": [{"lang": "en", "value": "XZ Utils backdoor."}],
                "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": 10.0, "baseSeverity": "CRITICAL",
                                                           "vectorString": "CVSS:3.1/AV:N"}}]},
                "weaknesses": [{"description": [{"value": "CWE-506"}]}],
                "configurations": [{"nodes": [{"cpeMatch": [{
                    "vulnerable": True, "criteria": "cpe:2.3:a:xz:liblzma:*",
                    "versionStartIncluding": "5.6.0", "versionEndExcluding": "5.6.2"}]}]}],
                "references": [{"url": "https://tukaani.org/xz-backdoor/", "tags": ["Exploit"]}],
            }}]}


def test_nvd_shapes_a_record(monkeypatch):
    monkeypatch.setattr(nvd, "_fetch", lambda params: dict(_FIXTURE))
    nvd._cache.clear()
    out = json.loads(nvd.make_handler()({"cve_id": "cve-2024-3094"}))
    assert out["success"] and out["count"] == 1
    rec = out["records"][0]
    assert rec["id"] == "CVE-2024-3094" and rec["cvss"]["score"] == 10.0
    assert rec["cwe"] == ["CWE-506"]
    assert rec["affected"][0]["versionEndExcluding"] == "5.6.2"
    assert "tukaani.org" in rec["references"][0]["url"]


def test_nvd_requires_a_query():
    out = json.loads(nvd.make_handler()({}))
    assert out["success"] is False and "cve_id or keyword" in out["error"]
