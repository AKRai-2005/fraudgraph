"""Run the 20 benchmark cases and write ``cases/<case_id>.json``.

Reproducible: same inputs, same outputs.  Nothing about the answer key is used,
and no expected answer is hardcoded anywhere in this package.

Run:  python -m fraudgraph.benchmark.run [--backend local|tigergraph|auto] [--no-llm]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..config import PATHS, ensure_dirs
from ..agent.orchestrator import InvestigationAgent, Trigger
from ..graph.store import GraphStore
from ..memory.case_memory import CaseMemory
from ..schemas import AnswerFile


def build_agent(backend: str, use_llm: bool) -> InvestigationAgent:
    store = GraphStore(prefer=backend)
    narrator = None
    if use_llm:
        from ..agent.llm import build_narrator

        narrator = build_narrator()
    return InvestigationAgent(store=store, memory=CaseMemory(store), narrator=narrator)


def run_all(backend: str = "auto", use_llm: bool = True, only: list[str] | None = None) -> dict:
    ensure_dirs()
    cp = pd.read_csv(PATHS.case_pack_csv)
    if only:
        cp = cp[cp.case_id.isin(only)]
    agent = build_agent(backend, use_llm)
    out_dir: Path = PATHS.cases_out
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[AnswerFile] = []
    errors: list[dict] = []
    t0 = time.perf_counter()
    for _, row in cp.iterrows():
        trig = Trigger.from_case_pack_row(row.to_dict())
        try:
            answer = agent.investigate(trig)
        except Exception as exc:  # noqa: BLE001 - one bad case must not stop the run
            errors.append({"case_id": trig.case_id, "error": f"{type(exc).__name__}: {exc}"})
            print(f"  {trig.case_id}: FAILED -- {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        (out_dir / f"{trig.case_id}.json").write_text(
            json.dumps(answer.to_answer_dict(), indent=2), encoding="utf-8"
        )
        _write_internal(answer)
        results.append(answer)
        c = answer.case
        print(
            f"  {answer.case_id}  {c.verdict.value:10s} p={c.fraud_probability:.2f} "
            f"{c.pattern.value:28s} exp=${c.exposure_usd:>9,.2f} "
            f"n={len(c.affected_txn_ids):>2d} tools={answer.tool_calls:>2d} "
            f"sar={'Y' if answer.sar.file else 'n'} "
            f"-> {','.join(a.action.value for a in answer.next_best_actions.final)}"
        )
    elapsed = time.perf_counter() - t0

    summary = {
        "cases_written": len(results),
        "cases_expected": int(len(cp)),
        "errors": errors,
        "backend": agent.store.backend_name,
        "llm": bool(use_llm and getattr(agent.narrator, "enabled", False)),
        "elapsed_s": round(elapsed, 2),
        "verdicts": pd.Series([r.case.verdict.value for r in results]).value_counts().to_dict()
        if results else {},
        "patterns": pd.Series([r.case.pattern.value for r in results]).value_counts().to_dict()
        if results else {},
        "sar_filings": sum(1 for r in results if r.sar.file),
        "written_to_graph": sum(1 for r in results if r.case.written_to_graph),
        "total_tool_calls": sum(r.tool_calls for r in results),
        "total_tokens": sum(r.tokens for r in results),
        "llm_stats": getattr(agent.narrator, "stats", None) if agent.narrator else None,
    }
    st = summary.get("llm_stats") or {}
    if st.get("calls_rate_limited"):
        print(
            f"\n  NOTE: {st['calls_rate_limited']} LLM call(s) hit the provider's rate "
            f"limit and fell back to the deterministic template. The verdicts, actions "
            f"and SAR decisions are unaffected -- they never come from the LLM -- but "
            f"some narratives are templates. Re-run when the quota resets, or raise "
            f"FG_LLM_MIN_INTERVAL.",
            file=sys.stderr,
        )
    (PATHS.build / "benchmark_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def _write_internal(answer: AnswerFile) -> None:
    """The full internal record: audit trail, findings, risk breakdown."""
    d = PATHS.build / "case_records"
    d.mkdir(parents=True, exist_ok=True)
    flagged_ts = ""
    for t in answer.timeline:
        if t.kind == "trigger":
            flagged_ts = str(t.data.get("opened_at") or "")
    for ev in answer.case.evidence:
        m = re.search(r"on (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", ev.claim)
        if m:
            flagged_ts = m.group(1)
            break
    blob = {
        "case_id": answer.case_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "flagged_ts": flagged_ts,
        "trigger": answer.trigger,
        "answer": answer.to_answer_dict(),
        "risk": answer.risk.model_dump(mode="json") if answer.risk else None,
        "findings": [f.model_dump(mode="json") for f in answer.findings],
        "timeline": [t.model_dump(mode="json") for t in answer.timeline],
        "tool_log": [t.model_dump(mode="json") for t in answer.tool_log],
        "evidence_requests_full": [e.model_dump(mode="json") for e in answer.evidence_requests],
        "actions_full": {
            "initial": [a.model_dump(mode="json") for a in answer.next_best_actions.initial],
            "final": [a.model_dump(mode="json") for a in answer.next_best_actions.final],
        },
    }
    (d / f"{answer.case_id}.json").write_text(json.dumps(blob, indent=2, default=str), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the 20 benchmark cases")
    ap.add_argument("--backend", default="auto",
                    choices=["auto", "local", "tigergraph", "mcp"])
    ap.add_argument("--no-llm", action="store_true", help="skip the LLM narrator")
    ap.add_argument("--only", nargs="*", help="run only these case ids")
    args = ap.parse_args(argv)
    print(f"Running benchmark (backend={args.backend}, llm={not args.no_llm}) ...")
    summary = run_all(backend=args.backend, use_llm=not args.no_llm, only=args.only)
    print("\n" + json.dumps(summary, indent=2))
    return 0 if summary["cases_written"] == summary["cases_expected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
