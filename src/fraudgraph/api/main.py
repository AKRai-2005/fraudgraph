"""FastAPI backend for the analyst dashboard.

Run:  python -m fraudgraph.api.main
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..config import RUNTIME
from .service import CaseService

FRONTEND = Path(__file__).resolve().parents[3] / "frontend"

app = FastAPI(
    title="Agentic Fraud Investigation",
    description="Graph-grounded fraud investigation agent on TigerGraph (HHGOA IEEE-CIS).",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@lru_cache(maxsize=1)
def svc() -> CaseService:
    return CaseService()


class ApprovalRequest(BaseModel):
    action: str
    approver: str = Field(min_length=1, max_length=80)
    decision: str = Field(pattern="^(approved|rejected)$")
    note: str = Field(default="", max_length=1000)


class AssignRequest(BaseModel):
    analyst: str = Field(min_length=1, max_length=80)


class AdhocRequest(BaseModel):
    txn_id: str = Field(min_length=1, max_length=32)
    note: str = Field(default="", max_length=500)


@app.get("/api/health")
def health():
    return svc().health()


@app.get("/api/overview")
def overview():
    return svc().overview()


@app.get("/api/queue")
def queue():
    return svc().queue()


@app.get("/api/divergence")
def divergence():
    """Where the agent ended up relative to the score that raised the alert."""
    return svc().divergence()


@app.get("/api/case-pack")
def case_pack():
    return svc().case_pack()


@app.get("/api/cases/{case_id}")
def case_detail(case_id: str):
    rec = svc().case_detail(case_id)
    if rec is None:
        raise HTTPException(404, f"no investigation record for {case_id}")
    return rec


@app.get("/api/cases/{case_id}/published")
def published(case_id: str):
    """The submitted answer file, as it sits on disk in ``cases/``."""
    ans = svc().published_answer(case_id)
    if ans is None:
        raise HTTPException(404, f"{case_id} has no published answer file")
    return ans


@app.get("/api/cases/{case_id}/graph")
def case_graph(case_id: str, max_nodes: int = 160):
    return svc().case_graph(case_id, max_nodes=max_nodes)


@app.post("/api/cases/{case_id}/investigate")
def investigate(case_id: str):
    try:
        return svc().investigate(case_id)
    except KeyError:
        raise HTTPException(404, f"{case_id} is not in the case pack") from None
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"investigation failed: {type(exc).__name__}: {exc}") from None


@app.post("/api/investigate-adhoc")
def investigate_adhoc(req: AdhocRequest):
    if not req.txn_id.isdigit():
        raise HTTPException(400, "txn_id must be numeric")
    try:
        return svc().investigate_adhoc(req.txn_id, req.note)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"investigation failed: {type(exc).__name__}: {exc}") from None


@app.post("/api/cases/{case_id}/approve")
def approve(case_id: str, req: ApprovalRequest):
    try:
        return svc().approve(case_id, req.action, req.approver, req.decision, req.note)
    except KeyError:
        raise HTTPException(404, f"no investigation record for {case_id}") from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@app.post("/api/cases/{case_id}/assign")
def assign(case_id: str, req: AssignRequest):
    return svc().assign(case_id, req.analyst)


@app.get("/api/memory")
def memory():
    return svc().memory_view()


@app.get("/api/model-card")
def model_card():
    return svc().model_card()


@app.get("/api/policy")
def policy():
    return svc().policy_view()


@app.get("/api/ingest-quality")
def ingest_quality():
    return svc().ingest_quality()


@app.get("/api/actions/log")
def action_log(case_id: str | None = None):
    return svc().actions.history(case_id=case_id)


# ---- static frontend -------------------------------------------------------
def _asset_version() -> str:
    """A token that changes whenever the JS or CSS changes.

    Without it the browser keeps serving the bundle it already has: a reload
    after an update silently ran the old app.js against the new API, and the
    new panel simply never appeared. The page itself is sent no-store, so the
    version token is always re-read.
    """
    stamp = 0.0
    for name in ("app.js", "styles.css"):
        f = FRONTEND / name
        if f.exists():
            stamp = max(stamp, f.stat().st_mtime)
    return f"{int(stamp)}"


if FRONTEND.exists():
    @app.get("/")
    def index():
        f = FRONTEND / "index.html"
        if not f.exists():
            return JSONResponse({"error": "frontend not built"}, status_code=404)
        html = f.read_text(encoding="utf-8")
        v = _asset_version()
        html = (html.replace("/static/app.js", f"/static/app.js?v={v}")
                    .replace("/static/styles.css", f"/static/styles.css?v={v}"))
        return HTMLResponse(html, headers={"cache-control": "no-store"})

    app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")


def main() -> None:
    import os

    import uvicorn

    # FG_RELOAD=1 restarts on edit. Off by default: reload needs an import
    # string rather than the app object, which re-imports the whole package
    # (and re-reads the 590k-row parquet cache) on every save.
    if os.getenv("FG_RELOAD", "").lower() in ("1", "true", "yes"):
        uvicorn.run("fraudgraph.api.main:app", host=RUNTIME.api_host,
                    port=RUNTIME.api_port, log_level="info", reload=True,
                    reload_dirs=[str(Path(__file__).resolve().parents[2])])
        return
    uvicorn.run(app, host=RUNTIME.api_host, port=RUNTIME.api_port, log_level="info")


if __name__ == "__main__":
    main()
