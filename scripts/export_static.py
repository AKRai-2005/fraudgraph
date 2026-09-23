"""Freeze the console into a static site.

Hugging Face now charges for container Spaces, so the free way to give someone
a link is a static one: every GET the console makes is written to a file, and
the page reads those instead of a server (``window.FG_STATIC`` in app.js).

What survives: the 20 cases, their evidence and graphs, the queue, the
divergence chart, case memory, the model card and the backtest. What cannot:
live investigation, the stream, approvals, ad-hoc lookup -- there is no agent
behind a static file. The page says so rather than failing on click.

    python scripts/export_static.py                  # -> build/static_site
    python scripts/export_static.py --serve 8090     # check it locally

The responses are captured from the real app, not rebuilt, so the export
cannot drift from what the API returns.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

# a local, LLM-free capture: deterministic, and it spends no free-tier quota
os.environ.setdefault("FG_GRAPH_BACKEND", "local")
os.environ.setdefault("FG_LLM_PROVIDER", "none")

FIXED = ["/api/health", "/api/queue", "/api/overview", "/api/divergence",
         "/api/memory", "/api/model-card", "/api/policy", "/api/ingest-quality",
         "/api/case-pack"]


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:                                           # noqa: BLE001
        return ""


def export(out: Path) -> dict:
    from fastapi.testclient import TestClient

    from fraudgraph.api.main import app

    if out.exists():
        shutil.rmtree(out)
    (out / "api" / "cases").mkdir(parents=True)

    client = TestClient(app)
    written, failed = 0, []

    def grab(path: str) -> dict | None:
        nonlocal written
        r = client.get(path)
        if r.status_code != 200:
            failed.append(f"{path} -> {r.status_code}")
            return None
        target = out / (path.lstrip("/") + ".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(r.json()), encoding="utf-8")
        written += 1
        return r.json()

    health = client.get("/api/health").json()
    for path in FIXED:
        grab(path)

    queue = grab("/api/queue") or []
    for row in queue:
        cid = row["case_id"]
        grab(f"/api/cases/{cid}")
        grab(f"/api/cases/{cid}/graph")

    # health is rewritten last: it carries what this export is and when it was
    # taken, which the page shows in the status strip and the banner
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": _commit(),
        "source": "https://github.com/AKRai-2005/fraudgraph",
        "cases": len(queue),
    }
    health["static_export"] = meta
    (out / "api" / "health.json").write_text(json.dumps(health), encoding="utf-8")

    # the page itself: assets go relative, so it works under a /repo/ subpath
    shutil.copy2(REPO_ROOT / "frontend" / "styles.css", out / "styles.css")
    shutil.copy2(REPO_ROOT / "frontend" / "app.js", out / "app.js")
    html = (REPO_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    html = html.replace('href="/static/styles.css"', 'href="styles.css"')
    html = html.replace('src="/static/app.js"', 'src="app.js"')
    flag = ("<script>window.FG_STATIC = "
            + json.dumps({"generated_at": meta["generated_at"], "source": meta["source"]})
            + ";</script>\n")
    html = html.replace('<script src="app.js"></script>', flag + '<script src="app.js"></script>')
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")   # GitHub Pages: serve files as-is

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file()) / 1048576
    return {"written": written, "failed": failed, "size_mb": round(size, 2),
            "cases": len(queue), "meta": meta}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(REPO_ROOT / "build" / "static_site"))
    ap.add_argument("--serve", type=int, metavar="PORT", help="serve the result and exit on Ctrl-C")
    args = ap.parse_args(argv)

    out = Path(args.out)
    res = export(out)
    print(f"{res['written']} responses, {res['cases']} cases, {res['size_mb']} MB -> {out}")
    if res["failed"]:
        print("FAILED:", file=sys.stderr)
        for f in res["failed"]:
            print("  " + f, file=sys.stderr)

    if args.serve:
        import functools
        import http.server

        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
        print(f"http://127.0.0.1:{args.serve}  (Ctrl-C to stop)")
        http.server.ThreadingHTTPServer(("127.0.0.1", args.serve), handler).serve_forever()
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
