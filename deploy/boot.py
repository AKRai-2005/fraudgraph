"""Fetch the private data bundle, then serve the console.

The console runs without the bundle -- it will serve the published answer
files and say, on every screen, that the graph is unavailable. That is the
intended degraded state, not a crash: a Space that shows the case files beats
a Space that shows a stack trace. So a failed download is reported loudly and
the server starts anyway.

Environment:
    FG_DATA_REPO   the private repo holding the bundle, e.g. "user/fraudgraph-data"
    FG_DATA_TYPE   "dataset" (default) or "model"
    HF_TOKEN       a read token for that repo
    FG_BUILD_DIR   where the bundle lands (default /home/user/app/build)
"""
from __future__ import annotations

import os
import shutil
import sys
import tarfile
import time
from pathlib import Path

BUILD = Path(os.environ.setdefault("FG_BUILD_DIR", "/home/user/app/build"))
REPO = os.getenv("FG_DATA_REPO", "").strip()
REPO_TYPE = os.getenv("FG_DATA_TYPE", "dataset").strip() or "dataset"
TOKEN = os.getenv("HF_TOKEN", "").strip()


def log(msg: str) -> None:
    print(f"[boot] {msg}", flush=True)


def fetch_bundle() -> bool:
    if not REPO:
        log("FG_DATA_REPO is not set: serving the published answers with no "
            "dataset behind them.")
        return False
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        log("huggingface_hub is not installed; skipping the data fetch.")
        return False

    t0 = time.perf_counter()
    try:
        src = Path(snapshot_download(repo_id=REPO, repo_type=REPO_TYPE,
                                     token=TOKEN or None))
    except Exception as exc:                                    # noqa: BLE001
        log(f"could not fetch {REPO}: {type(exc).__name__}: {str(exc)[:300]}")
        log("serving without the dataset; the console will say the graph is down.")
        return False

    BUILD.mkdir(parents=True, exist_ok=True)
    tarballs = sorted(src.glob("*.tar.gz"))
    if tarballs:
        with tarfile.open(tarballs[0]) as tf:
            tf.extractall(BUILD)            # noqa: S202 - our own bundle
        log(f"unpacked {tarballs[0].name}")
    else:
        for item in src.iterdir():
            if item.name.startswith("."):   # .cache, .gitattributes
                continue
            target = BUILD / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
        log(f"copied {sum(1 for _ in src.iterdir())} entries")

    have = sorted(p.name for p in BUILD.iterdir())
    size = sum(f.stat().st_size for f in BUILD.rglob("*") if f.is_file()) / 1048576
    log(f"{len(have)} entries, {size:.1f} MB in {BUILD} ({time.perf_counter() - t0:.1f}s)")
    if not (BUILD / "tx_index.parquet").exists():
        log("WARNING: tx_index.parquet is missing, so the local mirror cannot "
            "answer. Live investigation will need TigerGraph.")
    return True


def main() -> int:
    fetch_bundle()
    # imported after the environment is settled: config reads it at import time
    import uvicorn

    from fraudgraph.api.main import app

    host = os.getenv("FG_API_HOST", "0.0.0.0")
    port = int(os.getenv("FG_API_PORT", "7860"))
    log(f"serving on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
