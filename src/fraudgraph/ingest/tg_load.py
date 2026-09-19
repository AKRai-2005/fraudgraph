"""Create the graph, install the queries, and load the data into TigerGraph.

Idempotent: vertices and edges upsert by primary id, so re-running updates
rather than duplicates.  Safe to interrupt and resume.

Run:
  python -m fraudgraph.ingest.tg_load --schema      # create graph + schema
  python -m fraudgraph.ingest.tg_load --queries     # install GSQL queries
  python -m fraudgraph.ingest.tg_load --data        # run the loading job
  python -m fraudgraph.ingest.tg_load --all         # all three, in order
  python -m fraudgraph.ingest.tg_load --check       # counts only
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from ..config import PATHS, TG

GSQL_DIR = Path(__file__).resolve().parents[1] / "graph" / "gsql"

#: loading-job file tag -> exported CSV
FILE_TAGS = {
    "f_customer": "customers.csv",
    "f_card": "cards.csv",
    "f_txn": "transactions.csv",
    "f_device": "device_profiles.csv",
    "f_region": "regions.csv",
    "f_email": "email_domains.csv",
    "f_closed_case": "closed_cases.csv",
    "f_pattern": "fraud_patterns.csv",
    "f_policy": "policy_rules.csv",
    "f_edge_next": "edge_next.csv",
    "f_edge_case_txn": "edge_case_txn.csv",
    "f_edge_case_conn": "edge_case_connected.csv",
}


def _require_config() -> None:
    if not TG.configured:
        print(
            "TigerGraph is not configured. Copy .env.example to .env and fill in TG_HOST, "
            "TG_USERNAME and TG_PASSWORD (or TG_SECRET) from your Savanna workspace.",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _conn(graph: str | None = None):
    import pyTigerGraph as tg

    kwargs = {
        "host": TG.host.rstrip("/"),
        "username": TG.username or "tigergraph",
        "restppPort": TG.rest_port,
        "gsPort": TG.gs_port,
    }
    if graph:
        kwargs["graphname"] = graph
    if TG.password:
        kwargs["password"] = TG.password
    return tg.TigerGraphConnection(**kwargs)


def create_schema(drop: bool = False) -> None:
    _require_config()
    conn = _conn()
    schema = (GSQL_DIR / "schema.gsql").read_text(encoding="utf-8")
    print(f"Creating graph {TG.graph} ...")
    if drop:
        print("  dropping existing graph (requested)")
        print(conn.gsql(f"DROP GRAPH {TG.graph}"))
    # global vertex/edge types, then the graph over all of them
    out = conn.gsql("USE GLOBAL\n" + schema)
    print(out[-2000:] if out else "(no output)")
    out = conn.gsql(f"CREATE GRAPH {TG.graph}(*)")
    print(out[-800:] if out else "(no output)")


def install_queries() -> None:
    _require_config()
    conn = _conn(TG.graph)
    if TG.secret:
        conn.getToken(TG.secret)
    queries = (GSQL_DIR / "queries.gsql").read_text(encoding="utf-8")
    print("Installing GSQL queries (this takes a few minutes) ...")
    out = conn.gsql(f"USE GRAPH {TG.graph}\n" + queries)
    print(out[-3000:] if out else "(no output)")
    out = conn.gsql(f"USE GRAPH {TG.graph}\nINSTALL QUERY ALL")
    print(out[-2000:] if out else "(no output)")


def create_loading_job() -> None:
    _require_config()
    conn = _conn(TG.graph)
    job = (GSQL_DIR / "loading.gsql").read_text(encoding="utf-8")
    job = job.replace("FraudInvestigation", TG.graph)
    print("Creating loading job ...")
    out = conn.gsql(f"USE GRAPH {TG.graph}\n" + job)
    print(out[-2000:] if out else "(no output)")


def load_data(only: list[str] | None = None) -> dict:
    _require_config()
    conn = _conn(TG.graph)
    if TG.secret:
        conn.getToken(TG.secret)
    csv_dir = PATHS.build / "tg_csv"
    if not csv_dir.exists():
        print("No exported CSVs. Run: python -m fraudgraph.ingest.tg_export", file=sys.stderr)
        raise SystemExit(2)
    results: dict[str, str] = {}
    for tag, fname in FILE_TAGS.items():
        if only and tag not in only:
            continue
        path = csv_dir / fname
        if not path.exists():
            results[tag] = "missing file"
            continue
        size = path.stat().st_size / 1e6
        print(f"  loading {fname} ({size:.1f} MB) as {tag} ...", flush=True)
        t0 = time.perf_counter()
        try:
            res = conn.runLoadingJobWithFile(str(path), tag, "load_fraud_graph", timeout=3_600_000)
            results[tag] = json.dumps(res)[:300]
        except Exception as exc:  # noqa: BLE001
            results[tag] = f"ERROR {type(exc).__name__}: {exc}"[:300]
        print(f"    {time.perf_counter() - t0:.1f}s -> {results[tag][:160]}")
    (PATHS.build / "tg_load_results.json").write_text(json.dumps(results, indent=2))
    return results


def check() -> dict:
    _require_config()
    from ..graph.tigergraph import TigerGraphBackend

    h = TigerGraphBackend().ping()
    print(json.dumps(h, indent=2))
    return h


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", action="store_true")
    ap.add_argument("--queries", action="store_true")
    ap.add_argument("--job", action="store_true")
    ap.add_argument("--data", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--drop", action="store_true", help="drop the graph first (destructive)")
    ap.add_argument("--only", nargs="*", help="load only these file tags")
    a = ap.parse_args(argv)
    if not any([a.schema, a.queries, a.job, a.data, a.check, a.all]):
        ap.print_help()
        return 1
    if a.all or a.schema:
        create_schema(drop=a.drop)
    if a.all or a.job:
        create_loading_job()
    if a.all or a.data:
        load_data(only=a.only)
    if a.all or a.queries:
        install_queries()
    if a.all or a.check:
        check()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
