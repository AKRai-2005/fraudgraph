"""Verify the cases in TigerGraph actually match the answer files.

Counting edges is not enough. A bug that deleted the case vertex after writing
it left every AgentCase with blank attributes, recreated implicitly by its own
edges -- and every edge count still matched. So this checks content: the
attributes on each vertex, not just how many things point at it.

Run:  python scripts/verify_graph_cases.py [--backend tigergraph|mcp]
"""
from __future__ import annotations

import argparse
import glob
import json

from fraudgraph.config import TG
from fraudgraph.ingest.tg_load import _conn


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="cases")
    a = ap.parse_args()

    c = _conn(TG.graph)
    files = sorted(glob.glob(f"{a.cases}/HHG-*.json"))
    if not files:
        print("no answer files found")
        return 1

    want_edges = {k: 0 for k in ("CASE_CITES_PRIOR", "CASE_INVESTIGATES",
                                 "CASE_CONNECTED_TO", "CASE_ON_CARD",
                                 "CASE_MATCHES_PATTERN", "CASE_CONTAINS_EVIDENCE")}
    problems: list[str] = []
    checked = 0

    for f in files:
        blob = json.load(open(f, encoding="utf-8"))
        d = blob["case"]
        gid = d.get("graph_case_id")
        want_edges["CASE_CITES_PRIOR"] += len(d["similar_prior_cases"])
        want_edges["CASE_INVESTIGATES"] += len(d["affected_txn_ids"])
        want_edges["CASE_CONNECTED_TO"] += len(d["connected_card_ids"])
        want_edges["CASE_ON_CARD"] += 1
        want_edges["CASE_MATCHES_PATTERN"] += 1 if d["pattern"] != "none" else 0
        want_edges["CASE_CONTAINS_EVIDENCE"] += len(d["evidence"])

        if not d.get("written_to_graph"):
            problems.append(f"{blob['case_id']}: answer file says it was not written")
            continue
        rows = c.getVerticesById("AgentCase", gid)
        if not rows:
            problems.append(f"{blob['case_id']}: no AgentCase {gid} in the graph")
            continue
        attrs = rows[0].get("attributes", {})
        checked += 1
        for field, expected in (
            ("case_id", blob["case_id"]),
            ("verdict", d["verdict"]),
            ("status", d["status"]),
            ("pattern", d["pattern"]),
        ):
            got = attrs.get(field)
            if str(got) != str(expected):
                problems.append(
                    f"{blob['case_id']}: {field} is {got!r} in the graph, "
                    f"{expected!r} in the answer file"
                )
        if abs(float(attrs.get("exposure_usd") or 0) - float(d["exposure_usd"])) > 0.01:
            problems.append(
                f"{blob['case_id']}: exposure {attrs.get('exposure_usd')} != {d['exposure_usd']}"
            )
        if abs(float(attrs.get("fraud_probability") or 0) - float(d["fraud_probability"])) > 0.002:
            problems.append(
                f"{blob['case_id']}: probability {attrs.get('fraud_probability')} "
                f"!= {d['fraud_probability']}"
            )
        if not str(attrs.get("summary") or "").strip():
            problems.append(f"{blob['case_id']}: summary is empty in the graph")

    print(f"Checked {checked} of {len(files)} AgentCase vertices for content\n")
    print("Edge counts:")
    for e, w in want_edges.items():
        g = c.getEdgeCount(e)
        mark = "ok  " if g == w else "DIFF"
        print(f"  {mark} {e:24s} graph={g:<6} answers={w}")
        if g != w:
            problems.append(f"{e}: graph {g} != answers {w}")

    print(f"\nAgentCase vertices: {c.getVertexCount('AgentCase')}"
          f"  CaseEvidence: {c.getVertexCount('CaseEvidence')}")
    if problems:
        print(f"\n{len(problems)} problem(s):")
        for p in problems[:25]:
            print(f"  - {p}")
        return 1
    print("\nPASSED: every case in the graph matches its answer file, "
          "in content as well as count.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
