"""Run the benchmark on two backends and diff the answers field by field.

    python scripts/compare_backends.py local tigergraph
    python scripts/compare_backends.py local mcp
    python scripts/compare_backends.py local local     # determinism check

Exits non-zero if the two disagree on anything outside
``fraudgraph.benchmark.compare.EXPECTED_TO_DIFFER``.

The LLM narrator is off on both sides: it rewrites prose non-deterministically,
so leaving it on would compare the sampler, not the backends.  Everything it
touches is prose -- no verdict, probability, action or SAR decision comes from
it -- so nothing that matters is excluded by turning it off.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

BACKENDS = ("local", "tigergraph", "mcp", "auto")


def run_into(backend: str, out_dir: Path) -> str:
    """Run the 20 cases on ``backend``, writing answers into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    os.environ["FG_CASES_DIR"] = str(out_dir)
    # redirect the internal records too, or the two runs overwrite each other's
    # -- and the ones the dashboard is displaying
    os.environ["FG_RECORDS_DIR"] = str(out_dir / "records")
    # config is read at import time, so reload the modules that captured it
    import importlib

    from fraudgraph import config

    importlib.reload(config)
    from fraudgraph.benchmark import run as runner

    importlib.reload(runner)
    summary = runner.run_all(backend=backend, use_llm=False)
    if summary["cases_written"] != summary["cases_expected"]:
        raise SystemExit(
            f"{backend}: only {summary['cases_written']}/{summary['cases_expected']} "
            f"cases ran -- {summary['errors']}"
        )
    return summary["backend"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("left", choices=BACKENDS)
    ap.add_argument("right", choices=BACKENDS)
    ap.add_argument("--keep", action="store_true", help="keep the two output dirs")
    args = ap.parse_args(argv)

    from fraudgraph.benchmark.compare import compare_dirs

    tmp = Path(tempfile.mkdtemp(prefix="fg_compare_"))
    try:
        print(f"Running the 20 cases on '{args.left}' ...")
        left_name = run_into(args.left, tmp / "left")
        print(f"Running the 20 cases on '{args.right}' ...")
        right_name = run_into(args.right, tmp / "right")
        if args.left == args.right:
            left_name, right_name = f"{left_name} (run 1)", f"{right_name} (run 2)"
        result = compare_dirs(tmp / "left", tmp / "right", left_name, right_name)
        print("\n" + result.report())
        if args.keep:
            print(f"\nAnswer files kept in {tmp}")
        return 0 if result.agree else 1
    finally:
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
