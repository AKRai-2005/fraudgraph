"""Streaming an investigation while it runs.

The observers are the risk here: anything hanging off the agent's inner loop
that can raise, block or mutate would turn a display feature into a
correctness bug. These tests hold the line that an observer is a listener --
it sees everything and changes nothing.
"""
from __future__ import annotations

import functools
import json

import pytest

from fraudgraph.config import PATHS

pytestmark = pytest.mark.skipif(
    not (PATHS.build / "tx_index.parquet").exists(),
    reason="parquet cache not built",
)


@pytest.fixture(scope="module")
def client(isolated_records):
    from fastapi.testclient import TestClient

    from fraudgraph.api import main as api_main
    from fraudgraph.api.service import CaseService

    service = CaseService(backend="local", use_llm=False)
    api_main.svc = functools.lru_cache(maxsize=1)(lambda: service)
    for route in api_main.app.routes:
        fn = getattr(route, "endpoint", None)
        if fn is not None and hasattr(fn, "__globals__"):
            fn.__globals__["svc"] = api_main.svc
    with TestClient(api_main.app) as c:
        yield c


def read_stream(client, case_id: str) -> dict:
    """Drain an SSE response into {event_name: [payloads]}."""
    out: dict[str, list] = {}
    event = None
    with client.stream("GET", f"/api/cases/{case_id}/investigate/stream") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and event:
                out.setdefault(event, []).append(json.loads(line[5:].strip()))
    return out


# ---------------------------------------------------------------- the stream
def test_the_stream_reports_steps_queries_and_a_result(client):
    ev = read_stream(client, "HHG-014")
    assert ev.get("open"), "no open event"
    assert len(ev.get("step", [])) >= 5, "an investigation is more than a few steps"
    assert len(ev.get("query", [])) >= 5, "no graph calls were reported"
    assert len(ev.get("done", [])) == 1
    assert not ev.get("error")


def test_streamed_queries_name_a_real_catalogue_query(client):
    from fraudgraph.graph.queries import CATALOGUE

    ev = read_stream(client, "HHG-014")
    for q in ev["query"]:
        assert q["ref"].startswith("query:")
        name = q["ref"][len("query:"):].split("(")[0]
        assert name in CATALOGUE, f"streamed a query that is not in the catalogue: {name}"
        assert q["backend"], "a streamed query must say which backend served it"
        assert q["duration_ms"] >= 0


def test_streamed_steps_are_numbered_in_order(client):
    ev = read_stream(client, "HHG-014")
    steps = [s["step"] for s in ev["step"]]
    assert steps == sorted(steps), "steps arrived out of order"
    assert len(set(steps)) == len(steps), "a step was emitted twice"


def test_the_stream_and_the_record_agree(client):
    """What was streamed must be what was recorded -- not a parallel story."""
    ev = read_stream(client, "HHG-014")
    record = ev["done"][0]
    assert len(ev["step"]) == len(record["timeline"])
    assert len(ev["query"]) == len(record["tool_log"])
    for streamed, logged in zip(ev["step"], record["timeline"]):
        assert streamed["detail"] == logged["detail"]
    for streamed, logged in zip(ev["query"], record["tool_log"]):
        assert streamed["ref"] == logged["ref"]


def test_the_streamed_result_matches_a_plain_rerun(client):
    """Attaching observers must not change the answer."""
    from fraudgraph.benchmark.compare import ComparisonResult, compare_answers

    quiet = client.post("/api/cases/HHG-014/investigate").json()
    streamed = read_stream(client, "HHG-014")["done"][0]
    res = ComparisonResult("quiet re-run", "streamed re-run")
    compare_answers(quiet["answer"], streamed["answer"], "HHG-014", res)
    assert res.agree, res.report()


def test_the_stream_does_not_touch_the_published_answer(client):
    import hashlib

    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(PATHS.cases_out.glob("*.json"))}
    read_stream(client, "HHG-014")
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(PATHS.cases_out.glob("*.json"))}
    assert before == after


def test_streaming_an_unknown_case_is_404(client):
    r = client.get("/api/cases/HHG-999/investigate/stream")
    assert r.status_code == 404


# ------------------------------------------------------- observers are inert
def test_a_raising_step_observer_cannot_break_an_investigation():
    """A browser disconnecting mid-run must not corrupt the case."""
    import pandas as pd

    from fraudgraph.agent.orchestrator import InvestigationAgent, Trigger
    from fraudgraph.graph.local_mirror import get_local_backend
    from fraudgraph.graph.store import GraphStore
    from fraudgraph.memory.case_memory import CaseMemory

    def explode(_entry):
        raise RuntimeError("the client went away")

    row = pd.read_csv(PATHS.case_pack_csv).iloc[0].to_dict()
    store = GraphStore(backend=get_local_backend(), on_call=explode)
    agent = InvestigationAgent(store=store, memory=CaseMemory(store), narrator=None,
                               on_step=explode)
    answer = agent.investigate(Trigger.from_case_pack_row(row))
    assert answer.case.verdict is not None
    assert answer.case.evidence, "the investigation lost its evidence"
    assert answer.tool_calls > 0


def test_observers_see_every_step_and_every_call():
    import pandas as pd

    from fraudgraph.agent.orchestrator import InvestigationAgent, Trigger
    from fraudgraph.graph.local_mirror import get_local_backend
    from fraudgraph.graph.store import GraphStore
    from fraudgraph.memory.case_memory import CaseMemory

    steps, calls = [], []
    row = pd.read_csv(PATHS.case_pack_csv).iloc[0].to_dict()
    store = GraphStore(backend=get_local_backend(), on_call=calls.append)
    agent = InvestigationAgent(store=store, memory=CaseMemory(store), narrator=None,
                               on_step=steps.append)
    answer = agent.investigate(Trigger.from_case_pack_row(row))
    assert len(steps) == len(answer.timeline)
    assert len(calls) == answer.tool_calls


def test_an_observer_is_optional():
    """The benchmark path passes none; it must behave exactly as before."""
    import pandas as pd

    from fraudgraph.agent.orchestrator import InvestigationAgent, Trigger
    from fraudgraph.graph.local_mirror import get_local_backend
    from fraudgraph.graph.store import GraphStore
    from fraudgraph.memory.case_memory import CaseMemory

    row = pd.read_csv(PATHS.case_pack_csv).iloc[0].to_dict()
    store = GraphStore(backend=get_local_backend())
    agent = InvestigationAgent(store=store, memory=CaseMemory(store), narrator=None)
    assert agent.investigate(Trigger.from_case_pack_row(row)).case.evidence


def test_a_full_event_queue_drops_frames_rather_than_stalling():
    """A slow reader must not be able to block the investigation thread."""
    from fraudgraph.api.stream import InvestigationStream

    stream = InvestigationStream(service=None, case_id="X", runner=lambda a, b: {})
    stream.q.maxsize = 2
    for i in range(50):
        stream._put("step", {"i": i})
    assert stream.q.qsize() <= 2
