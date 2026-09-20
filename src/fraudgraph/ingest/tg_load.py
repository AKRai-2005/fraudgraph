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


def gsql_statements(text: str) -> list[str]:
    """Split a .gsql file into individually-executable statements.

    The GSQL endpoint a Savanna workspace exposes rejects a trailing semicolon
    ("Encountered ';'"), and will not take a multi-statement blob, so each
    statement is sent on its own with its terminator stripped.

    Splitting cannot be a plain ``text.split(';')``: a CREATE QUERY body and a
    CREATE LOADING JOB body are full of semicolons. So brace depth is tracked,
    and a statement ends either at a semicolon while at depth 0, or at the brace
    that closes a block. Comments and string literals are skipped so a ``;``
    inside either is never mistaken for a terminator.
    """
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    i, n = 0, len(text)
    in_str: str | None = None
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_str:
            buf.append(ch)
            if ch == in_str and text[i - 1] != "\\":
                in_str = None
            i += 1
            continue
        if ch in ("'", '"'):
            in_str = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if (ch == "/" and nxt == "/") or ch == "#":
            j = text.find("\n", i)
            i = n if j == -1 else j
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            buf.append(ch)
            i += 1
            if depth == 0:
                stmt = "".join(buf).strip()
                if stmt:
                    out.append(stmt)
                buf = []
            continue
        if ch == ";" and depth == 0:
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


#: GSQL responses that mean "already there", which makes a re-run a no-op
#: rather than a failure.
_BENIGN = ("already exists", "already in the graph", "is already")


def _run_statements(conn, prefix: str, statements: list[str], label: str) -> dict:
    ok = skipped = failed = 0
    errors: list[str] = []
    for idx, stmt in enumerate(statements, 1):
        head = " ".join(stmt.split())[:72]
        try:
            out = conn.gsql(prefix + "\n" + stmt) or ""
        except Exception as exc:  # noqa: BLE001
            out = f"EXCEPTION {type(exc).__name__}: {exc}"
        low = out.lower()
        if any(b in low for b in _BENIGN):
            skipped += 1
            print(f"  [{idx:2d}/{len(statements)}] skip   {head}")
        elif "fail" in low or "error" in low or "exception" in low or "not match" in low:
            failed += 1
            errors.append(f"{head} -> {' '.join(out.split())[:240]}")
            print(f"  [{idx:2d}/{len(statements)}] FAIL   {head}")
        else:
            ok += 1
            print(f"  [{idx:2d}/{len(statements)}] ok     {head}", flush=True)
    print(f"{label}: {ok} created, {skipped} already present, {failed} failed")
    for e in errors:
        print(f"    ! {e}")
    return {"ok": ok, "skipped": skipped, "failed": failed, "errors": errors}

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
            "TigerGraph is not configured. Fill in .env:\n"
            "  TG_HOST   - Savanna -> Workspaces -> your workspace -> copy its URL\n"
            "  TG_SECRET - Savanna -> Database Secrets -> Create Secret (shown once)\n"
            "Savanna authenticates tools with a secret, not a password.",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _conn(graph: str | None = None):
    """Same auth rules as the runtime backend -- see fraudgraph.graph.tigergraph."""
    import pyTigerGraph as tg

    kwargs = {
        "host": TG.host.rstrip("/"),
        "username": TG.username or "tigergraph",
        "restppPort": TG.rest_port,
        "gsPort": TG.gs_port,
    }
    if graph:
        kwargs["graphname"] = graph
    if TG.secret:
        kwargs["gsqlSecret"] = TG.secret
        kwargs["tgCloud"] = True
        kwargs["sslPort"] = TG.rest_port
    if TG.password:
        kwargs["password"] = TG.password
    conn = tg.TigerGraphConnection(**kwargs)
    if TG.secret:
        try:
            conn.getToken(TG.secret)
        except Exception:  # noqa: BLE001
            pass
    return conn


#: Exactly the types this project owns. ``CREATE GRAPH g(*)`` would pull in
#: every global type in the database -- including the 29 belonging to the
#: Transaction_Fraud sample a Savanna workspace ships with -- so the graph is
#: created over a named list instead.
VERTEX_TYPES = [
    "Customer", "PaymentCard", "Transaction", "DeviceProfile", "BillingRegion",
    "EmailDomain", "ClosedCase", "AgentCase", "CaseEvidence", "FraudPattern",
    "PolicyRule",
]
EDGE_TYPES = [
    "OWNS", "MADE", "FROM_DEVICE", "BILLED_IN", "PURCHASER_EMAIL",
    "RECIPIENT_EMAIL", "NEXT_TXN", "INVOLVES", "ON_CARD", "CONNECTED_TO",
    "CASE_INVESTIGATES", "CASE_ON_CARD", "CASE_CONNECTED_TO",
    "CASE_CONTAINS_EVIDENCE", "CASE_MATCHES_PATTERN", "CASE_FROM_DEVICE",
    "CASE_CITES_PRIOR", "CASE_APPLIES_RULE",
]


def create_schema(drop: bool = False) -> None:
    _require_config()
    conn = _conn()
    schema = (GSQL_DIR / "schema.gsql").read_text(encoding="utf-8")
    print(f"Creating graph {TG.graph} ...")
    if drop:
        print("  dropping existing graph (requested)")
        print(conn.gsql(f"DROP GRAPH {TG.graph}"))
    stmts = gsql_statements(schema)
    print(f"  {len(stmts)} schema statements")
    _run_statements(conn, "USE GLOBAL", stmts, "schema")
    members = ", ".join(VERTEX_TYPES + EDGE_TYPES)
    out = conn.gsql(f"CREATE GRAPH {TG.graph}({members})") or ""
    print("  " + (" ".join(out.split())[:900] or "(no output)"))


def install_queries() -> None:
    _require_config()
    conn = _conn(TG.graph)
    queries = (GSQL_DIR / "queries.gsql").read_text(encoding="utf-8")
    stmts = gsql_statements(queries)
    print(f"Creating {len(stmts)} GSQL queries ...")
    _run_statements(conn, f"USE GRAPH {TG.graph}", stmts, "queries")
    print("Installing (this takes a few minutes) ...", flush=True)
    out = conn.gsql(f"USE GRAPH {TG.graph}\nINSTALL QUERY ALL") or ""
    print("  " + (" ".join(out.split())[-1500:] or "(no output)"))


def create_loading_job() -> None:
    _require_config()
    conn = _conn(TG.graph)
    job = (GSQL_DIR / "loading.gsql").read_text(encoding="utf-8")
    job = job.replace("FraudInvestigation", TG.graph)
    stmts = gsql_statements(job)
    print(f"Creating loading job ({len(stmts)} statement(s)) ...")
    _run_statements(conn, f"USE GRAPH {TG.graph}", stmts, "loading job")


def load_data(only: list[str] | None = None) -> dict:
    _require_config()
    conn = _conn(TG.graph)
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
