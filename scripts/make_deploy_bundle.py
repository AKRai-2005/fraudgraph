"""Assemble the files a deployment needs, for a PRIVATE data repository.

The console can serve published answers with nothing behind them, but the
parts that make it worth showing -- a live investigation, the graph around a
case, ad-hoc lookup -- need the transaction cache. The cache is the
organisers' dataset in another form, so it is not published: it goes to a
private Hugging Face dataset repo and the Space pulls it at boot with a token.

    python scripts/make_deploy_bundle.py            # writes build/deploy_bundle/
    python scripts/make_deploy_bundle.py --tar      # and a single .tar.gz

Upload the contents (or the tarball) to your private repo, then set
FG_DATA_REPO and HF_TOKEN on the Space. See docs/DEPLOY.md.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fraudgraph.config import PATHS  # noqa: E402

# What the local mirror reads, plus what the console reads off disk. Nothing
# else: the 675 MB source CSVs and the V-column parquet stay out.
FILES = [
    "tx_index.parquet",          # the transaction index the mirror queries
    "cards.parquet",
    "customers.parquet",
    "device_profiles.parquet",
    "regions.parquet",
    "closed_cases.parquet",      # the 5,565 closed investigations
    "risk_model.json",           # model card views
    "risk_model_logistic.json",
    "detector_rates.json",
    "backtest.json",
    "benchmark_summary.json",
    "agent_cases.jsonl",         # the journal the Memory view counts
]
DIRS = ["case_records"]          # the records the console renders


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(PATHS.build / "deploy_bundle"))
    ap.add_argument("--tar", action="store_true", help="also write <out>.tar.gz")
    args = ap.parse_args(argv)

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    missing, total = [], 0
    for name in FILES:
        src = PATHS.build / name
        if not src.exists():
            missing.append(name)
            continue
        shutil.copy2(src, out / name)
        total += src.stat().st_size
    for name in DIRS:
        src = PATHS.build / name
        if not src.is_dir():
            missing.append(name + "/")
            continue
        shutil.copytree(src, out / name)
        total += sum(f.stat().st_size for f in src.rglob("*") if f.is_file())

    for name in sorted(p.name for p in out.iterdir()):
        path = out / name
        size = (path.stat().st_size if path.is_file()
                else sum(f.stat().st_size for f in path.rglob("*") if f.is_file()))
        print(f"  {name:28} {size / 1048576:7.2f} MB")
    print(f"\n{out}  --  {total / 1048576:.1f} MB total")

    if missing:
        print("\nMISSING (build them first, or the deployment degrades):", file=sys.stderr)
        for m in missing:
            print("  " + m, file=sys.stderr)

    if args.tar:
        tarball = out.with_suffix(".tar.gz")
        with tarfile.open(tarball, "w:gz") as tf:
            tf.add(out, arcname=".")
        print(f"{tarball}  --  {tarball.stat().st_size / 1048576:.1f} MB")

    print("\nThis bundle is derived from the challenge dataset. Upload it to a "
          "PRIVATE repository, never a public one.")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
