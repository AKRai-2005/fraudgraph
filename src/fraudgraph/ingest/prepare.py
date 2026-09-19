"""Stage 1 of ingestion: raw CSV -> columnar parquet cache.

The raw transaction file is ~708 MB with 397 columns.  Everything downstream
(graph loading, analysis, the API) reads the parquet cache instead, which keeps
repeated access cheap and makes the pipeline reproducible.

Run:  python -m fraudgraph.ingest.prepare
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

from ..config import PATHS, ensure_dirs

# Columns that carry investigable meaning.  V1..V339 are Vesta's unnamed
# engineered features and are written to a separate file so they can be used as
# signals without paying for them on every read.
CORE_COLUMNS = (
    ["TransactionID", "TransactionDT", "TransactionAmt", "ProductCD"]
    + [f"card{i}" for i in range(1, 7)]
    + ["addr1", "addr2", "dist1", "dist2", "P_emaildomain", "R_emaildomain"]
    + [f"C{i}" for i in range(1, 15)]
    + [f"D{i}" for i in range(1, 16)]
    + [f"M{i}" for i in range(1, 10)]
    + ["customer_id", "ts", "channel", "risk_score"]
)

CHUNK = 100_000


def _read_header(path: Path) -> list[str]:
    return pd.read_csv(path, nrows=0).columns.tolist()


def build_transactions(verbose: bool = True) -> dict:
    """Convert transactions.csv into two parquet files and return stats."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    header = _read_header(PATHS.transactions_csv)
    missing = [c for c in CORE_COLUMNS if c not in header]
    if missing:
        raise ValueError(f"transactions.csv is missing expected columns: {missing}")
    v_cols = ["TransactionID"] + [c for c in header if c.startswith("V") and c[1:].isdigit()]

    core_writer = v_writer = None
    n_rows = 0
    try:
        reader = pd.read_csv(PATHS.transactions_csv, chunksize=CHUNK, low_memory=False)
        for i, chunk in enumerate(reader):
            core = chunk[CORE_COLUMNS].copy()
            core["ts"] = pd.to_datetime(core["ts"], errors="coerce")
            core["TransactionID"] = core["TransactionID"].astype("int64")
            for c in ("TransactionAmt", "risk_score", "dist1", "dist2"):
                core[c] = pd.to_numeric(core[c], errors="coerce")
            core_tbl = pa.Table.from_pandas(core, preserve_index=False)
            v_tbl = pa.Table.from_pandas(chunk[v_cols], preserve_index=False)
            if core_writer is None:
                core_writer = pq.ParquetWriter(PATHS.tx_core, core_tbl.schema, compression="zstd")
                v_writer = pq.ParquetWriter(PATHS.tx_vcols, v_tbl.schema, compression="zstd")
            core_writer.write_table(core_tbl)
            v_writer.write_table(v_tbl)
            n_rows += len(chunk)
            if verbose:
                print(f"  transactions: {n_rows:,} rows", end="\r", flush=True)
    finally:
        if core_writer is not None:
            core_writer.close()
        if v_writer is not None:
            v_writer.close()
    if verbose:
        print(f"  transactions: {n_rows:,} rows written                ")
    return {"rows": n_rows, "core_columns": len(CORE_COLUMNS), "v_columns": len(v_cols) - 1}


def build_identity(verbose: bool = True) -> dict:
    df = pd.read_csv(PATHS.identity_csv, low_memory=False)
    df["TransactionID"] = df["TransactionID"].astype("int64")
    df.to_parquet(PATHS.identity_parquet, compression="zstd", index=False)
    if verbose:
        print(f"  identity: {len(df):,} rows, {df.shape[1]} columns")
    return {"rows": int(len(df)), "columns": int(df.shape[1])}


def build_closed_cases(verbose: bool = True) -> dict:
    df = pd.read_csv(PATHS.closed_cases_csv)
    df["opened_at"] = pd.to_datetime(df["opened_at"], errors="coerce")
    df["closed_at"] = pd.to_datetime(df["closed_at"], errors="coerce")
    df["txn_id_list"] = df["txn_ids"].fillna("").apply(
        lambda s: [int(x) for x in str(s).split("|") if str(x).strip().isdigit()]
    )
    df["connected_card_list"] = df["connected_card_ids"].fillna("").apply(
        lambda s: [x for x in str(s).split("|") if x.strip()]
    )
    df["action_list"] = df["actions_taken"].fillna("").apply(
        lambda s: [x for x in str(s).split("|") if x.strip()]
    )
    df.to_parquet(PATHS.closed_cases_parquet, compression="zstd", index=False)
    if verbose:
        print(f"  closed cases: {len(df):,} rows")
    return {"rows": int(len(df))}


def main(argv: list[str] | None = None) -> int:
    ensure_dirs()
    argv = argv or sys.argv[1:]
    force = "--force" in argv
    stats: dict = {}
    print("Preparing parquet cache from raw CSV ...")
    if force or not PATHS.tx_core.exists():
        stats["transactions"] = build_transactions()
    else:
        print("  transactions: cached (use --force to rebuild)")
    if force or not PATHS.identity_parquet.exists():
        stats["identity"] = build_identity()
    else:
        print("  identity: cached")
    if force or not PATHS.closed_cases_parquet.exists():
        stats["closed_cases"] = build_closed_cases()
    else:
        print("  closed cases: cached")
    if stats:
        out = PATHS.build / "prepare_stats.json"
        out.write_text(json.dumps(stats, indent=2))
        print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
