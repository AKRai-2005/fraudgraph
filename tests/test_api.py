"""The HTTP surface: every endpoint, plus the two things it must never do.

The console is the only part of this system a judge or an analyst actually
touches, and it had no tests at all. Two of the tests below exist because the
untested behaviour was wrong:

* ``/api/cases/{id}/investigate`` overwrote the published answer file in
  ``cases/`` -- the deliverable -- on every click;
* nothing checked that credentials stay out of responses, and the service
  hands back configuration in ``/api/health``.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from fraudgraph.config import PATHS

pytestmark = pytest.mark.skipif(
    not (PATHS.build / "tx_index.parquet").exists(),
    reason="parquet cache not built; run fraudgraph.ingest.prepare && .ingest.entities",
)


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from fraudgraph.api import main as api_main
    from fraudgraph.api.service import CaseService

    # pin the local mirror and no LLM: these tests must not depend on a live
    # Savanna workspace or burn free-tier quota
    api_main.svc.cache_clear()
    api_main.svc.__wrapped__ = lambda: CaseService(backend="local", use_llm=False)
    service = CaseService(backend="local", use_llm=False)
    api_main.app.dependency_overrides = {}
    api_main.svc.cache_clear()
    import functools

    api_main.svc = functools.lru_cache(maxsize=1)(lambda: service)
    # rebind the closures the route functions captured
    for route in api_main.app.routes:
        fn = getattr(route, "endpoint", None)
        if fn is not None and hasattr(fn, "__globals__"):
            fn.__globals__["svc"] = api_main.svc
    with TestClient(api_main.app) as c:
        yield c


def _case_ids() -> list[str]:
    return sorted(p.stem for p in PATHS.cases_out.glob("HHG-*.json"))


def _fingerprint_published() -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(PATHS.cases_out.glob("*.json"))}


# ------------------------------------------------------------- reachability
@pytest.mark.parametrize("path", [
    "/api/health", "/api/overview", "/api/queue", "/api/case-pack",
    "/api/memory", "/api/model-card", "/api/policy", "/api/ingest-quality",
    "/api/actions/log",
])
def test_every_read_endpoint_answers(client, path):
    r = client.get(path)
    assert r.status_code == 200, f"{path} -> {r.status_code} {r.text[:200]}"
    assert r.json() is not None


def test_the_console_itself_is_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "<html" in r.text.lower()


def test_queue_rows_carry_what_the_table_renders(client):
    rows = client.get("/api/queue").json()
    assert rows, "no investigations on disk to serve"
    for key in ("case_id", "verdict", "fraud_probability", "pattern",
                "next_action", "status", "trigger_type"):
        assert key in rows[0], f"queue row is missing {key}"


def test_overview_counts_match_the_queue(client):
    o = client.get("/api/overview").json()
    rows = client.get("/api/queue").json()
    assert o["cases_investigated"] == len(rows)
    assert sum(o["by_verdict"].values()) == len(rows)


def test_case_detail_and_graph(client):
    cid = _case_ids()[0]
    rec = client.get(f"/api/cases/{cid}").json()
    assert rec["case_id"] == cid
    assert rec["answer"]["case"]["evidence"], "a case with no evidence is not an answer"
    g = client.get(f"/api/cases/{cid}/graph").json()
    assert g["nodes"] and isinstance(g["edges"], list)
    ids = {n["id"] for n in g["nodes"]}
    for e in g["edges"]:
        assert e["source"] in ids and e["target"] in ids, "graph edge dangles"


def test_published_answer_is_served_verbatim(client):
    cid = _case_ids()[0]
    served = client.get(f"/api/cases/{cid}/published").json()
    on_disk = json.loads((PATHS.cases_out / f"{cid}.json").read_text(encoding="utf-8"))
    assert served == on_disk


# ---------------------------------------------------------------- integrity
def test_a_rerun_does_not_touch_the_published_answer_files(client):
    """The regression: one click rewrote the deliverable.

    With the Savanna workspace asleep the store falls back to the local
    mirror, so the rewritten answer also flipped ``written_to_graph`` to
    false and cited a different prior case -- silently, in a file already
    committed as the submission.
    """
    cid = _case_ids()[0]
    before = _fingerprint_published()
    r = client.post(f"/api/cases/{cid}/investigate")
    assert r.status_code == 200
    assert _fingerprint_published() == before, "a re-run edited cases/"


def test_a_rerun_is_labelled_as_one(client):
    cid = _case_ids()[0]
    rec = client.post(f"/api/cases/{cid}/investigate").json()
    prov = rec.get("provenance") or {}
    assert prov.get("kind") == "rerun"
    assert prov.get("backend"), "a re-run must name the backend that served it"


def test_drift_against_the_published_answer_is_reported(client):
    cid = _case_ids()[0]
    rec = client.get(f"/api/cases/{cid}").json()
    drift = rec["drift"]
    assert drift["published"] is True
    assert isinstance(drift["matches"], bool)
    if not drift["matches"]:
        assert drift["differences"], "drift claimed without naming a field"
        assert all("path" in d for d in drift["differences"])


# --------------------------------------------------------------- validation
def test_unknown_case_is_404_not_500(client):
    assert client.get("/api/cases/HHG-999").status_code == 404
    assert client.get("/api/cases/HHG-999/published").status_code == 404
    assert client.post("/api/cases/HHG-999/investigate").status_code == 404


def test_adhoc_rejects_a_non_numeric_transaction_id(client):
    r = client.post("/api/investigate-adhoc", json={"txn_id": "'; DROP TABLE--"})
    assert r.status_code in (400, 422)


def test_adhoc_rejects_an_oversized_id(client):
    r = client.post("/api/investigate-adhoc", json={"txn_id": "9" * 64})
    assert r.status_code in (400, 422)


def test_approving_an_auto_action_is_refused(client):
    """Auto actions need no approval; recording one would fake an audit entry."""
    for cid in _case_ids():
        rec = client.get(f"/api/cases/{cid}").json()
        auto = [a for a in (rec.get("actions_full") or {}).get("final", [])
                if a.get("route") == "auto"]
        if not auto:
            continue
        r = client.post(f"/api/cases/{cid}/approve", json={
            "action": auto[0]["action"], "approver": "tester", "decision": "approved",
        })
        assert r.status_code == 400
        return
    pytest.skip("no auto-routed action in any case")


def test_approving_an_action_the_agent_did_not_recommend_is_refused(client):
    cid = _case_ids()[0]
    r = client.post(f"/api/cases/{cid}/approve", json={
        "action": "BLOCK_CARD", "approver": "tester", "decision": "approved",
    })
    if r.status_code == 200:
        pytest.skip("BLOCK_CARD happens to be recommended on this case")
    assert r.status_code == 400


def test_approval_requires_a_named_approver(client):
    cid = _case_ids()[0]
    r = client.post(f"/api/cases/{cid}/approve", json={
        "action": "BLOCK_CARD", "approver": "", "decision": "approved",
    })
    assert r.status_code == 422


def test_approval_decision_is_constrained(client):
    cid = _case_ids()[0]
    r = client.post(f"/api/cases/{cid}/approve", json={
        "action": "BLOCK_CARD", "approver": "tester", "decision": "executed",
    })
    assert r.status_code == 422, "only approved/rejected may be recorded"


# ----------------------------------------------------------------- secrecy
SECRET_ENV = ("TG_SECRET", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY",
              "TG_PASSWORD", "TG_TOKEN")


def test_no_endpoint_leaks_a_credential(client):
    """Nothing the browser can fetch may contain a secret, in any form."""
    import os

    secrets = [v for k in SECRET_ENV if (v := os.getenv(k, "")) and len(v) >= 8]
    if not secrets:
        pytest.skip("no credentials configured in this environment to leak")
    paths = ["/api/health", "/api/overview", "/api/queue", "/api/model-card",
             "/api/policy", "/api/memory", "/api/ingest-quality", "/api/case-pack",
             "/api/actions/log"]
    paths += [f"/api/cases/{cid}" for cid in _case_ids()[:3]]
    for path in paths:
        body = client.get(path).text
        for s in secrets:
            assert s not in body, f"{path} leaked a credential"


def test_health_does_not_publish_the_full_host(client):
    """The workspace URL identifies the tenant; the console needs only a hint."""
    h = client.get("/api/health").json()
    host = h.get("tigergraph_host", "")
    assert "..." in host or host == "", f"health exposed the full host: {host}"


def test_static_mount_does_not_serve_the_project_root(client):
    """A path-traversal attempt must not reach .env."""
    for attempt in ("/static/../.env", "/static/..%2f.env", "/static/../../.env"):
        r = client.get(attempt)
        assert r.status_code != 200 or "TG_SECRET" not in r.text


def test_cors_does_not_admit_arbitrary_origins(client):
    """A page the analyst happens to have open must not be able to POST here.

    With allow_origins=["*"] and allow_methods=["*"] the preflight succeeded
    for every origin, so any site could record an approval on a case or start
    investigations on this console.
    """
    r = client.options("/api/cases/HHG-001/approve", headers={
        "origin": "https://evil.example",
        "access-control-request-method": "POST",
        "access-control-request-headers": "content-type",
    })
    allowed = r.headers.get("access-control-allow-origin", "")
    assert allowed not in ("*", "https://evil.example"), f"CORS admitted {allowed!r}"


def test_cors_still_admits_the_console_itself(client):
    from fraudgraph.config import RUNTIME

    origin = f"http://127.0.0.1:{RUNTIME.api_port}"
    r = client.options("/api/cases/HHG-001/approve", headers={
        "origin": origin,
        "access-control-request-method": "POST",
        "access-control-request-headers": "content-type",
    })
    assert r.headers.get("access-control-allow-origin") == origin
