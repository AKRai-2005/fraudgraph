"""The TigerGraph loading job uses positional columns. Pin them to the CSVs.

``loading.gsql`` addresses columns as ``$0, $1, ...`` because named columns
would require the server to know each file's header before the file is posted.
That is correct but fragile: reorder a column in ``tg_export.py`` and the load
silently puts the wrong value in the wrong attribute. These tests make that a
test failure instead.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from fraudgraph.config import PATHS
from fraudgraph.ingest.tg_load import EDGE_TYPES, FILE_TAGS, VERTEX_TYPES, gsql_statements

GSQL_DIR = Path(__file__).resolve().parents[1] / "src" / "fraudgraph" / "graph" / "gsql"

#: What each exported CSV's header must be, in order. Mirrors tg_export.py.
EXPECTED_HEADERS = {
    "customers.csv": ["customer_id", "n_cards", "n_txns", "first_ts", "last_ts",
                      "total_amt", "home_region", "home_country"],
    "cards.csv": ["card_id", "customer_id", "card1", "network", "card_type", "n_txns",
                  "first_ts", "last_ts", "total_amt", "median_amt", "max_amt",
                  "n_online", "n_in_person", "mean_risk"],
    "transactions.csv": ["txn_id", "card_id", "customer_id", "ts", "amount", "product_cd",
                         "channel", "risk_score", "addr1", "addr2", "dist1", "p_email",
                         "r_email", "device_profile", "device_new", "proxy_flag",
                         "match_status", "seq_in_card"],
    "device_profiles.csv": ["device_profile", "device_type", "device_info", "os", "browser",
                            "screen", "proxy_flags", "n_txns", "n_cards", "n_customers",
                            "n_new_for_account", "first_ts", "last_ts", "total_amt"],
    "regions.csv": ["region_id", "country", "n_txns", "n_cards", "n_customers"],
    "email_domains.csv": ["domain", "n_txns"],
    "closed_cases.csv": ["case_id", "customer_id", "card_id", "opened_at", "closed_at",
                         "outcome", "pattern", "first_fraud_txn_id", "n_txns",
                         "exposure_usd", "actions_taken", "report_filed", "analyst_notes"],
    "fraud_patterns.csv": ["pattern_id", "name", "description", "documented", "policy_rules"],
    "policy_rules.csv": ["rule_id", "summary", "rule_text"],
    "edge_next.csv": ["from_txn", "to_txn", "gap_s"],
    "edge_case_txn.csv": ["case_id", "txn_id"],
    "edge_case_connected.csv": ["case_id", "card_id"],
}

#: Attribute order of each vertex type in schema.gsql, after the PRIMARY_ID.
#: A LOAD ... TO VERTEX X VALUES($0, $1, ...) fills these in order.
VERTEX_LOAD_ORDER = {
    "Customer": "customers.csv",
    "PaymentCard": "cards.csv",
    "Transaction": "transactions.csv",
    "DeviceProfile": "device_profiles.csv",
    "BillingRegion": "regions.csv",
    "EmailDomain": "email_domains.csv",
    "ClosedCase": "closed_cases.csv",
    "FraudPattern": "fraud_patterns.csv",
    "PolicyRule": "policy_rules.csv",
}

csv_dir = PATHS.build / "tg_csv"
needs_export = pytest.mark.skipif(
    not csv_dir.exists(), reason="run python -m fraudgraph.ingest.tg_export first"
)


# ------------------------------------------------------------------ headers
@needs_export
@pytest.mark.parametrize("fname,expected", sorted(EXPECTED_HEADERS.items()))
def test_exported_csv_header_matches_the_loading_job(fname, expected):
    path = csv_dir / fname
    assert path.exists(), f"{fname} was not exported"
    header = path.read_text(encoding="utf-8").splitlines()[0].strip().split(",")
    assert header == expected, (
        f"{fname} column order changed; loading.gsql addresses columns positionally "
        f"and must be updated too"
    )


@needs_export
def test_every_file_tag_has_an_exported_csv():
    for tag, fname in FILE_TAGS.items():
        assert (csv_dir / fname).exists(), f"{tag} -> {fname} missing"


# ------------------------------------------------------------- loading job
def _loading_job() -> str:
    return (GSQL_DIR / "loading.gsql").read_text(encoding="utf-8")


def test_loading_job_uses_positional_columns_only():
    job = _loading_job()
    body = job[job.index("{"):]
    named = re.findall(r'\$"[^"]+"', body)
    assert named == [], f"named columns would fail at CREATE time: {named[:5]}"


def test_loading_job_indices_are_in_range_for_each_file():
    """$N must exist in the file that LOAD statement reads."""
    job = _loading_job()
    tag_to_file = {t: f for t, f in FILE_TAGS.items()}
    for block in re.finditer(r"LOAD\s+(f_\w+)(.*?)USING", job, re.S):
        tag, body = block.group(1), block.group(2)
        fname = tag_to_file[tag]
        width = len(EXPECTED_HEADERS[fname])
        used = {int(m) for m in re.findall(r"\$(\d+)", body)}
        assert used, f"{tag} uses no columns"
        assert max(used) < width, (
            f"{tag} references ${max(used)} but {fname} has only {width} columns"
        )


def test_vertex_loads_fill_every_declared_attribute():
    """VALUES(...) arity must equal PRIMARY_ID + attributes in schema.gsql."""
    schema = (GSQL_DIR / "schema.gsql").read_text(encoding="utf-8")
    job = _loading_job()
    for vtype, fname in VERTEX_LOAD_ORDER.items():
        m = re.search(rf"CREATE VERTEX {vtype} \((.*?)\) WITH", schema, re.S)
        assert m, f"{vtype} not found in schema.gsql"
        n_attrs = len([a for a in m.group(1).split(",") if a.strip()])
        lm = re.search(rf"TO VERTEX {vtype}\s*\n?\s*VALUES\(([^)]*)\)", job, re.S)
        assert lm, f"no LOAD ... TO VERTEX {vtype} in loading.gsql"
        n_vals = len([v for v in lm.group(1).split(",") if v.strip()])
        assert n_vals == n_attrs, (
            f"{vtype}: schema declares {n_attrs} fields but the load supplies {n_vals}"
        )
        assert n_attrs == len(EXPECTED_HEADERS[fname]), (
            f"{vtype}: schema declares {n_attrs} fields but {fname} has "
            f"{len(EXPECTED_HEADERS[fname])} columns"
        )


def test_loading_job_is_one_statement():
    assert len(gsql_statements(_loading_job())) == 1


# ------------------------------------------------------------------ schema
def test_schema_splits_into_one_statement_per_type():
    stmts = gsql_statements((GSQL_DIR / "schema.gsql").read_text(encoding="utf-8"))
    assert len(stmts) == len(VERTEX_TYPES) + len(EDGE_TYPES)
    for v in VERTEX_TYPES:
        assert any(s.startswith(f"CREATE VERTEX {v} ") for s in stmts), v
    for e in EDGE_TYPES:
        assert any(re.match(rf"CREATE DIRECTED EDGE {e}\b", s) for s in stmts), e


def test_no_statement_carries_a_trailing_semicolon():
    """The Savanna GSQL endpoint rejects them."""
    for f in ("schema.gsql", "loading.gsql", "queries.gsql"):
        for s in gsql_statements((GSQL_DIR / f).read_text(encoding="utf-8")):
            assert not s.rstrip().endswith(";"), f"{f}: {s[:60]}"


def test_card_vertex_is_namespaced_away_from_the_sample_graph():
    """A Savanna workspace that ran Transaction_Fraud already owns a global Card."""
    for f in ("schema.gsql", "loading.gsql", "queries.gsql"):
        text = (GSQL_DIR / f).read_text(encoding="utf-8")
        body = "\n".join(l for l in text.splitlines() if not l.strip().startswith("*"))
        assert not re.search(r"(?<!Payment)\bCard\b(?!_)", body), (
            f"{f} still refers to a bare Card vertex type"
        )


def test_queries_split_matches_the_catalogue():
    from fraudgraph.graph.queries import CATALOGUE

    stmts = gsql_statements((GSQL_DIR / "queries.gsql").read_text(encoding="utf-8"))
    names = {re.match(r"CREATE QUERY (\w+)", s).group(1) for s in stmts}
    expected = {n for n in CATALOGUE if n != "write_case"} | {"graph_health"}
    assert expected <= names, f"missing installed queries: {sorted(expected - names)}"
