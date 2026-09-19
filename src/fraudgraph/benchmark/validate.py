"""Validate the answer files against the dataset README's Answer Format.

Checks structure, enum values, internal consistency, and -- importantly -- that
every identifier emitted actually exists in the shipped dataset.  The README is
explicit: "Made-up IDs score zero."

Run:  python -m fraudgraph.benchmark.validate
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from ..config import PATHS

TOP_LEVEL = ["case_id", "case", "evidence_requests", "next_best_actions", "sar",
             "stop_reason", "tool_calls", "tokens", "latency_s"]
CASE_FIELDS = ["status", "verdict", "fraud_probability", "pattern", "pattern_description",
               "affected_txn_ids", "first_suspicious_txn_id", "connected_card_ids",
               "connected_device_profiles", "exposure_usd", "evidence",
               "similar_prior_cases", "summary", "written_to_graph", "graph_case_id"]
SAR_FIELDS = ["file", "reason", "narrative", "subjects", "total_amount_usd", "activity_dates"]
NBA_FIELDS = ["initial", "final", "what_changed"]

STATUSES = {"open", "closed_fraud", "closed_legitimate", "escalated"}
VERDICTS = {"fraud", "legitimate", "uncertain"}
PATTERNS = {"card_testing", "card_not_present_fraud", "card_not_present_new_device",
            "out_of_region_use", "account_takeover", "undocumented", "none"}
SOURCES = {"graph", "document", "customer", "external"}
ROUTES = {"auto", "L1", "L2"}
REQ_TYPES = {"customer_validation", "step_up_auth", "analyst_info"}
ACTIONS = {"ALLOW_TRANSACTION", "DECLINE_TRANSACTION", "MONITOR_CARD",
           "MONITOR_CONNECTED_CARDS", "WARN_CUSTOMER", "VERIFY_WITH_CUSTOMER",
           "STEP_UP_AUTH", "BLOCK_CARD", "BLOCK_ALL_CARDS", "GENERATE_REPORT",
           "CREATE_CASE", "FILE_REPORT", "ESCALATE_TO_ANALYST", "CLOSE_NO_FRAUD"}

ROUTE_TABLE = {
    "DECLINE_TRANSACTION": {"L1"},
    "BLOCK_ALL_CARDS": {"L2"},
    "FILE_REPORT": {"L2"},
}
AUTO_ONLY = {"ALLOW_TRANSACTION", "MONITOR_CARD", "MONITOR_CONNECTED_CARDS",
             "WARN_CUSTOMER", "VERIFY_WITH_CUSTOMER", "STEP_UP_AUTH",
             "GENERATE_REPORT", "CREATE_CASE", "ESCALATE_TO_ANALYST", "CLOSE_NO_FRAUD"}


class Universe:
    """The ids that actually exist in the shipped dataset."""

    def __init__(self) -> None:
        tx = pd.read_parquet(PATHS.build / "tx_index.parquet",
                             columns=["TransactionID", "card_id", "customer_id",
                                      "device_profile", "TransactionAmt"])
        self.txn_ids = set(tx.TransactionID.astype("int64").astype(str))
        self.amounts = dict(zip(tx.TransactionID.astype("int64").astype(str),
                                tx.TransactionAmt.astype(float)))
        self.card_ids = set(tx.card_id.dropna())
        self.customer_ids = set(tx.customer_id.dropna())
        self.device_profiles = set(tx.device_profile.dropna())
        cc = pd.read_parquet(PATHS.closed_cases_parquet, columns=["case_id"])
        self.closed_case_ids = set(cc.case_id)
        self.pack = pd.read_csv(PATHS.case_pack_csv)
        self.pack_ids = list(self.pack.case_id)


def validate_one(blob: dict, u: Universe) -> list[str]:
    errs: list[str] = []
    cid = blob.get("case_id", "<missing>")

    def e(msg: str) -> None:
        errs.append(f"{cid}: {msg}")

    for k in TOP_LEVEL:
        if k not in blob:
            e(f"missing top-level field '{k}'")
    if errs:
        return errs

    case = blob["case"]
    for k in CASE_FIELDS:
        if k not in case:
            e(f"case missing field '{k}'")
    sar = blob["sar"]
    for k in SAR_FIELDS:
        if k not in sar:
            e(f"sar missing field '{k}'")
    nba = blob["next_best_actions"]
    for k in NBA_FIELDS:
        if k not in nba:
            e(f"next_best_actions missing field '{k}'")
    if errs:
        return errs

    # ---- enums and ranges
    if case["status"] not in STATUSES:
        e(f"invalid status {case['status']!r}")
    if case["verdict"] not in VERDICTS:
        e(f"invalid verdict {case['verdict']!r}")
    if case["pattern"] not in PATTERNS:
        e(f"invalid pattern {case['pattern']!r}")
    p = case["fraud_probability"]
    if not isinstance(p, (int, float)) or not 0.0 <= p <= 1.0:
        e(f"fraud_probability {p!r} out of range")
    if case["pattern"] == "undocumented" and not case["pattern_description"].strip():
        e("pattern is 'undocumented' but pattern_description is empty")
    if case["pattern"] != "undocumented" and case["pattern_description"].strip():
        e("pattern_description must be empty unless the pattern is 'undocumented'")

    # ---- ids must exist
    for t in case["affected_txn_ids"]:
        if str(t) not in u.txn_ids:
            e(f"affected_txn_id {t} is not in the dataset")
    fs = case["first_suspicious_txn_id"]
    if fs and str(fs) not in u.txn_ids:
        e(f"first_suspicious_txn_id {fs} is not in the dataset")
    for c in case["connected_card_ids"]:
        if c not in u.card_ids:
            e(f"connected_card_id {c} is not in the dataset")
    for d in case["connected_device_profiles"]:
        if d not in u.device_profiles:
            e(f"connected_device_profile {d!r} is not in the dataset")
    for pc in case["similar_prior_cases"]:
        if pc not in u.closed_case_ids:
            e(f"similar_prior_case {pc} is not in closed_cases_history.csv")

    # ---- legitimate-verdict contract (README notes)
    if case["verdict"] == "legitimate":
        if case["affected_txn_ids"]:
            e("verdict is legitimate but affected_txn_ids is not empty")
        if float(case["exposure_usd"]) != 0:
            e("verdict is legitimate but exposure_usd is not 0")
        if sar["file"]:
            e("verdict is legitimate but sar.file is true")

    # ---- exposure must equal the sum of the affected amounts
    if case["affected_txn_ids"]:
        expected = round(sum(abs(u.amounts.get(str(t), 0.0)) for t in case["affected_txn_ids"]), 2)
        got = round(float(case["exposure_usd"]), 2)
        if abs(expected - got) > 0.05:
            e(f"exposure_usd {got} != sum of affected amounts {expected}")
        if fs and str(fs) not in {str(x) for x in case["affected_txn_ids"]}:
            e("first_suspicious_txn_id is not in affected_txn_ids")

    # ---- evidence
    if not case["evidence"]:
        e("evidence list is empty")
    for i, ev in enumerate(case["evidence"]):
        for k in ("claim", "source", "ref", "entity_ids"):
            if k not in ev:
                e(f"evidence[{i}] missing '{k}'")
        if ev.get("source") not in SOURCES:
            e(f"evidence[{i}] invalid source {ev.get('source')!r}")
        if not str(ev.get("claim", "")).strip():
            e(f"evidence[{i}] has an empty claim")
        if not str(ev.get("ref", "")).strip():
            e(f"evidence[{i}] has an empty ref")

    # ---- actions
    final_actions = []
    for phase in ("initial", "final"):
        for i, a in enumerate(nba[phase]):
            for k in ("action", "route", "reason"):
                if k not in a:
                    e(f"{phase}[{i}] missing '{k}'")
                    continue
            if a.get("action") not in ACTIONS:
                e(f"{phase}[{i}] invalid action {a.get('action')!r}")
            if a.get("route") not in ROUTES:
                e(f"{phase}[{i}] invalid route {a.get('route')!r}")
            act, rt = a.get("action"), a.get("route")
            if act in AUTO_ONLY and rt != "auto":
                e(f"{phase}[{i}] {act} must be route auto, got {rt}")
            if act in ROUTE_TABLE and rt not in ROUTE_TABLE[act]:
                e(f"{phase}[{i}] {act} must be route {ROUTE_TABLE[act]}, got {rt}")
            if act == "BLOCK_CARD":
                exp = float(case["exposure_usd"])
                want = "L2" if exp > 2500 else "L1"
                if rt != want:
                    e(f"{phase}[{i}] BLOCK_CARD at exposure ${exp:,.2f} must be {want}, got {rt}")
            if not str(a.get("reason", "")).strip():
                e(f"{phase}[{i}] has an empty reason")
            if phase == "final":
                final_actions.append(act)
    if not nba["initial"]:
        e("next_best_actions.initial is empty")
    if not nba["final"]:
        e("next_best_actions.final is empty")

    # ---- verdict must cohere with the recommended actions
    if case["verdict"] == "legitimate" and (
        "BLOCK_CARD" in final_actions or "BLOCK_ALL_CARDS" in final_actions
    ):
        e("verdict is legitimate but the final actions block the card")
    if case["verdict"] == "fraud" and "CLOSE_NO_FRAUD" in final_actions:
        e("verdict is fraud but the final actions close the alert as legitimate")
    if case["status"] == "closed_legitimate" and "FILE_REPORT" in final_actions:
        e("case is closed as legitimate but a regulatory filing is recommended")

    # ---- SAR must agree with FILE_REPORT
    if sar["file"] != ("FILE_REPORT" in final_actions):
        e(f"sar.file={sar['file']} disagrees with FILE_REPORT in final actions "
          f"({'present' if 'FILE_REPORT' in final_actions else 'absent'})")
    if sar["file"]:
        if len(str(sar["narrative"]).split()) < 60:
            e("sar.narrative is required and should be six to twelve sentences")
        if not sar["subjects"]:
            e("sar.subjects is empty on a filed report")
        for s in sar["subjects"]:
            if not (s in u.card_ids or s in u.customer_ids or s in u.device_profiles
                    or str(s) in u.txn_ids):
                e(f"sar subject {s!r} is not an id in the dataset")
        if len(sar["activity_dates"]) != 2:
            e("sar.activity_dates must hold exactly two dates")
        if float(sar["total_amount_usd"]) <= 0:
            e("sar.total_amount_usd must be positive on a filed report")
    else:
        if sar["narrative"] or sar["subjects"] or float(sar["total_amount_usd"]) != 0 \
                or sar["activity_dates"]:
            e("sar.file is false but narrative/subjects/total/dates are not empty")
    if not str(sar["reason"]).strip():
        e("sar.reason is empty")

    # ---- evidence requests
    for i, r in enumerate(blob["evidence_requests"]):
        for k in ("type", "asked_after_step", "assumed_response"):
            if k not in r:
                e(f"evidence_requests[{i}] missing '{k}'")
        if r.get("type") not in REQ_TYPES:
            e(f"evidence_requests[{i}] invalid type {r.get('type')!r}")
        if not str(r.get("assumed_response", "")).strip():
            e(f"evidence_requests[{i}] has an empty assumed_response")
    if not blob["evidence_requests"] and nba["initial"] != nba["final"]:
        e("no evidence was requested but final actions differ from initial")

    # ---- misc
    if not str(blob["stop_reason"]).strip():
        e("stop_reason is empty")
    if not str(case["summary"]).strip():
        e("summary is empty")
    if int(blob["tool_calls"]) <= 0:
        e("tool_calls must be positive")
    if case["written_to_graph"] and not case["graph_case_id"]:
        e("written_to_graph is true but graph_case_id is empty")
    return errs


def validate_all(cases_dir: Path | None = None) -> dict:
    cases_dir = cases_dir or PATHS.cases_out
    u = Universe()
    report: dict = {"dir": str(cases_dir), "expected": len(u.pack_ids),
                    "found": 0, "missing": [], "errors": {}, "ok": []}
    for cid in u.pack_ids:
        p = cases_dir / f"{cid}.json"
        if not p.exists():
            report["missing"].append(cid)
            continue
        report["found"] += 1
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            report["errors"][cid] = [f"{cid}: invalid JSON -- {exc}"]
            continue
        if blob.get("case_id") != cid:
            report["errors"].setdefault(cid, []).append(
                f"{cid}: case_id inside the file is {blob.get('case_id')!r}")
        errs = validate_one(blob, u)
        if errs:
            report["errors"][cid] = errs
        else:
            report["ok"].append(cid)
    extra = sorted(p.stem for p in cases_dir.glob("*.json") if p.stem not in set(u.pack_ids))
    report["unexpected_files"] = extra
    report["valid"] = len(report["ok"])
    report["passed"] = (report["found"] == report["expected"]
                        and not report["errors"] and not report["missing"])
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=None)
    a = ap.parse_args(argv)
    rep = validate_all(Path(a.dir) if a.dir else None)
    print(f"Answer files in {rep['dir']}")
    print(f"  expected {rep['expected']}, found {rep['found']}, valid {rep['valid']}")
    if rep["missing"]:
        print(f"  MISSING: {', '.join(rep['missing'])}")
    if rep["unexpected_files"]:
        print(f"  note: extra files present: {', '.join(rep['unexpected_files'])}")
    for cid, errs in rep["errors"].items():
        print(f"  {cid}:")
        for x in errs:
            print(f"      - {x}")
    print("PASSED" if rep["passed"] else "FAILED")
    (PATHS.build / "validation_report.json").write_text(json.dumps(rep, indent=2))
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
