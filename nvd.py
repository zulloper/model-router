"""``cve_lookup`` tool: NVD 2.0 ground truth for CVE analysis and PoC work.

Models invent CVE IDs, affected ranges and CVSS scores; this returns the authoritative record
(description, CVSS, CWE, affected CPE ranges, references) with the API URL so the answer can cite
it. Read-only, no key required. NVD throttles anonymous clients (roughly 5 requests / 30 s), so the
last 64 answers are cached for 15 minutes; set ``NVD_API_KEY`` to raise the limit.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_API = "https://services.nvd.nist.gov/rest/json/cves/2.0"
_TTL = 900.0
_MAX_CACHE = 64
_UA = "hermes-model-router/0.3 (+nvd cve lookup)"
_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}

SCHEMA = {
    "name": "cve_lookup",
    "description": (
        "Look up authoritative CVE data from NVD 2.0: description, CVSS v3.1/v4.0, CWE, affected "
        "CPE version ranges and references. Use it before writing CVE analysis or a PoC so IDs, "
        "versions and severity are not guessed. Read-only. Accepts a CVE id or a keyword/product "
        "(optionally with a version). Returns the source URL."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "cve_id": {"type": "string", "description": "e.g. CVE-2024-3094"},
            "keyword": {"type": "string", "description": "Product or free text, e.g. 'OpenSSH 9.7p1'"},
            "limit": {"type": "integer", "description": "Max records for a keyword search (1-20, default 5)"},
        },
    },
}


def _fetch(params: dict[str, str]) -> dict:
    url = f"{_API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    key = os.environ.get("NVD_API_KEY")
    if key:
        req.add_header("apiKey", key)
    with urllib.request.urlopen(req, timeout=15) as res:  # noqa: S310 - fixed https host
        data = json.loads(res.read().decode("utf-8", "replace"))
    if not isinstance(data, dict):
        raise ValueError("unexpected NVD response")
    data["_source"] = url
    return data


def _cached(params: dict[str, str]) -> dict:
    key = json.dumps(params, sort_keys=True)
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    data = _fetch(params)
    with _lock:
        _cache[key] = (now + _TTL, data)
        while len(_cache) > _MAX_CACHE:
            _cache.pop(next(iter(_cache)))
    return data


def _metric(metrics: dict) -> dict[str, Any]:
    for version in ("cvssMetricV40", "cvssMetricV31", "cvssMetricV30"):
        for entry in metrics.get(version) or []:
            data = entry.get("cvssData") or {}
            if data:
                return {"version": version[-3:], "score": data.get("baseScore"),
                        "severity": data.get("baseSeverity") or entry.get("cvssData", {}).get("baseSeverity"),
                        "vector": data.get("vectorString")}
    return {}


def _shape(vuln: dict) -> dict[str, Any]:
    cve = vuln.get("cve") or {}
    descriptions = cve.get("descriptions") or []
    text = next((d.get("value") for d in descriptions if d.get("lang") == "en"), None) or (
        descriptions[0].get("value") if descriptions else "")
    weaknesses = []
    for w in cve.get("weaknesses") or []:
        for d in w.get("description") or []:
            if d.get("value") and d["value"] not in weaknesses:
                weaknesses.append(d["value"])
    affected = []
    for conf in cve.get("configurations") or []:
        for node in conf.get("nodes") or []:
            for match in node.get("cpeMatch") or []:
                if not match.get("vulnerable"):
                    continue
                affected.append({k: match.get(k) for k in (
                    "criteria", "versionStartIncluding", "versionStartExcluding",
                    "versionEndIncluding", "versionEndExcluding") if match.get(k)})
    refs = []
    for ref in cve.get("references") or []:
        if isinstance(ref, dict) and ref.get("url"):
            refs.append({"url": ref["url"], "tags": ref.get("tags") or []})
    return {
        "id": cve.get("id"),
        "status": cve.get("vulnStatus"),
        "published": cve.get("published"),
        "last_modified": cve.get("lastModified"),
        "description": text,
        "cvss": _metric(cve.get("metrics") or {}),
        "cwe": weaknesses[:10],
        "affected": affected[:20],
        "references": refs[:10],
    }


def make_handler():
    def handle(params: dict, **kwargs: Any) -> str:
        params = params or {}
        cve_id = str(params.get("cve_id") or "").strip()
        keyword = str(params.get("keyword") or "").strip()
        try:
            limit = max(1, min(20, int(params.get("limit") or 5)))
        except (TypeError, ValueError):
            limit = 5
        if cve_id:
            query = {"cveId": cve_id.upper()}
        elif keyword:
            query = {"keywordSearch": keyword, "resultsPerPage": str(limit), "noRejected": ""}
        else:
            return json.dumps({"success": False, "error": "cve_id or keyword is required"})
        try:
            data = _cached(query)
        except urllib.error.HTTPError as exc:
            hint = ("NVD rate limit: retry in ~30s or set NVD_API_KEY." if exc.code in (403, 429)
                    else f"NVD returned HTTP {exc.code}.")
            return json.dumps({"success": False, "error": hint})
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            return json.dumps({"success": False, "error": f"NVD request failed: {exc}"})
        vulns = data.get("vulnerabilities") or []
        records = [_shape(v) for v in vulns if isinstance(v, dict)][:limit if not cve_id else 1]
        return json.dumps({
            "success": True,
            "count": len(records),
            "source": data.get("_source"),
            "note": "Authoritative NVD record. Cite the source URL; mark anything outside it [unverified].",
            "records": records,
        }, ensure_ascii=False)
    return handle
