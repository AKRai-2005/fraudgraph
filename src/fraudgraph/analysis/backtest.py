"""Replay closed investigations through the whole agent and score the verdicts.

Everything else in this project measures a *part*: detector firing rates, the
risk model's cross-validated discrimination, format compliance. None of it
answers the only question that matters -- when the agent disagrees with the
bank's score, is it right?

The 20 exam cases cannot answer it either; we do not have their key. The 5,565
closed investigations can: they carry real outcomes. So this replays them as
if they were live alerts and scores what the agent concluded.

Three things make that harder than it sounds, and all three are handled
explicitly rather than quietly:

1. **Time.** A historical alert must not see investigations that closed after
   it, least of all itself. Every case-memory query is given ``as_of`` = the
   case's own ``opened_at``, the case id is excluded by name, and the replay
   *verifies* afterwards that nothing from the future was retrieved. A
   violation is a failure, not a warning.

2. **The trigger prior.** In this history the trigger almost is the label: all
   900 cleared cases began as a model score, and investigated customer
   disputes were confirmed fraud. The agent's trigger prior was fitted on
   exactly that, so replaying cases under their real trigger type would score
   the prior, not the investigation, and would look superb. The headline run
   therefore gives **every case the same trigger**, so the prior is a constant
   and the only thing that can separate the classes is what the graph found.
   The as-triggered run is reported too, clearly marked as the weaker evidence.

3. **The sample is not a sample of alerts.** It is a sample of investigations
   a bank chose to open. The cleared cases are all *high-scoring* alerts that
   turned out to be travel or a new phone -- the hardest negatives there are,
   not average traffic. Specificity measured here is a lower bound on
   specificity in the wild, and precision depends on a base rate this data
   cannot tell us. The report prints that caveat next to every number.

Run:  python -m fraudgraph.analysis.backtest [--n 300] [--backend local]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from dataclasses import dataclass, field

import pandas as pd

from ..agent.orchestrator import InvestigationAgent, Trigger
from ..config import PATHS
from ..graph.store import GraphStore
from ..memory.case_memory import CaseMemory

#: Truth labels in the shipped history.
FRAUD, CLEARED = "confirmed_fraud", "cleared"

#: Trigger modes. The choice matters more than anything else in this file.
#:
#: "neutral"  every case arrives as an analyst_request, whose prior is exactly
#:            0.00 log-odds (p=0.50). Nothing is on either scale, so whatever
#:            separates the classes is graph evidence and only graph evidence.
#:            This is the headline: it is the question the project claims to
#:            answer.
#: "score"    every case arrives as a model score, prior -1.60 (p=0.17). The
#:            agent's real operating point for the exam, and a deliberately
#:            pessimistic one.
#: "actual"   the trigger the case really had, recovered from the analyst
#:            notes. Highest numbers, weakest evidence: in this history the
#:            trigger nearly *is* the label, and the prior was fitted on that
#:            correlation, so a good score here is partly circular. A customer
#:            dispute is also genuine evidence, not merely a prior, so this is
#:            not purely inflation either -- which is why all three are run.
TRIGGER_MODES = {
    "neutral": "analyst_request",
    "score": "risk_score",
    "actual": "",           # inferred per case
}


@dataclass
class CaseResult:
    case_id: str
    truth: str
    verdict: str
    probability: float
    confidence: float
    pattern: str
    n_evidence: int
    tool_calls: int
    bank_risk_score: float | None
    truth_pattern: str = ""
    leaked: list[str] = field(default_factory=list)
    error: str = ""


# --------------------------------------------------------------- the replay
class ReplayMemory(CaseMemory):
    """Case memory that reads like the real thing and never writes.

    A replay is a measurement, not an investigation, so nothing it concludes
    may become memory. The first version of this backtest used the ordinary
    CaseMemory, whose write_case persists every closed investigation: against
    the local mirror that only appended 11,689 replayed closed cases to the
    agent's journal -- which the console then listed as "cases this system
    closed", 850 of them -- and against TigerGraph it would have written each
    replay into the graph as an AgentCase, where the next live investigation
    would have retrieved it as precedent.
    """

    def write_case(self, answer) -> dict:  # noqa: D401 - same contract as CaseMemory
        return {"written": False, "graph_case_id": "", "backend": self.store.backend_name,
                "detail": {"reason": "backtest replay: never persisted"}}


def _trigger_for(row, trigger_mode: str) -> Trigger | None:
    """Build the alert as it would have arrived, without leaking the outcome.

    The flagged transaction is ``txn_id_list[0]`` for both classes. That is the
    only symmetric choice available: cleared cases carry exactly one
    transaction and no ``first_fraud_txn_id`` at all, so using that field would
    hand the agent a different quality of starting point depending on the
    label.
    """
    # txn_id_list is a numpy array, so `or []` raises on anything but a
    # single element -- the truth value of an array is ambiguous
    raw = row.txn_id_list
    txns = [] if raw is None else list(raw)
    if not txns:
        return None
    opened = str(row.opened_at)
    return Trigger(
        case_id=str(row.case_id),
        trigger_type=(TRIGGER_MODES.get(trigger_mode)
                      or _infer_trigger(str(row.analyst_notes or ""))),
        trigger_text=f"Replay of closed investigation {row.case_id}.",
        flagged_txn_id=str(int(float(txns[0]))),
        card_id=str(row.card_id),
        customer_id=str(row.customer_id),
        opened_at=opened,
        # risk_score is deliberately not passed: the agent reads the bank
        # score off the transaction itself, exactly as it does live.
        as_of=opened,
        exclude_case_ids=(str(row.case_id),),
    )


def _infer_trigger(notes: str) -> str:
    """Recover how the alert arrived, from the analyst's own words."""
    low = notes.lower()
    if "reported" in low or "cardholder confirmed" in low and "model scored" not in low:
        return "customer_report"
    if "model scored" in low or "scored a" in low:
        return "risk_score"
    if "analyst" in low or "review" in low:
        return "analyst_request"
    return "risk_score"


def _leakage(answer, trig: Trigger, closed_at_by_id: dict) -> list[str]:
    """Anything the agent retrieved that it could not have known.

    Checked rather than trusted: a silent leak here would turn the whole
    exercise into a measurement of hindsight.
    """
    bad = []
    for cid in answer.case.similar_prior_cases:
        if cid == trig.case_id:
            bad.append(f"{cid} (the case under test)")
            continue
        closed = closed_at_by_id.get(cid)
        if closed is not None and str(closed) >= str(trig.as_of):
            bad.append(f"{cid} (closed {closed}, alert opened {trig.as_of})")
    return bad


def _window_leakage(answer, trig: Trigger) -> list[str]:
    """Any transaction query whose window reached past the moment the case opened.

    Case memory was time-boxed from the first version of this backtest.
    Transaction windows were not, and nothing checked them: the agent looked
    30 days past the flagged transaction for an episode and the same either
    side for a device ring, so a replayed alert could count cards compromised
    after its own investigation had opened. This reads the bounds actually
    sent, from the tool ledger, rather than trusting that the clamp was
    applied at every call site.
    """
    bad = []
    cutoff = pd.Timestamp(trig.as_of)
    for call in answer.tool_log:
        p = call.params or {}
        end = None
        if call.name == "card_window" and p.get("center_ts"):
            end = pd.Timestamp(p["center_ts"]) + pd.Timedelta(
                hours=float(p.get("hours_after") or 48.0))
        elif p.get("to_ts"):
            end = pd.Timestamp(p["to_ts"])
        if end is not None and end > cutoff:
            bad.append(f"{call.name} window ends {end}, after the case opened {trig.as_of}")
    return bad


def replay(
    n_per_class: int = 300, backend: str = "local", trigger_mode: str = "neutral",
    seed: int = 20260920, progress: bool = True, patterns: tuple[str, ...] = (),
) -> dict:
    """Replay a stratified sample of closed cases.

    ``patterns`` restricts the fraud class to those typologies and takes *all*
    of them regardless of ``n_per_class``. The relational typologies this agent
    is built around are rare -- 9 undocumented and 16 card-testing cases in the
    whole history -- so a random sample of 300 catches one or two of them and
    can say nothing useful about the thing the project actually claims.
    """
    cc = pd.read_parquet(PATHS.closed_cases_parquet)
    closed_at_by_id = dict(zip(cc.case_id, cc.closed_at.astype(str)))

    rng = random.Random(seed)
    sample = []
    for label in (FRAUD, CLEARED):
        pool = cc[cc.outcome == label]
        if patterns and label == FRAUD:
            pool = pool[pool.pattern.isin(patterns)]
            take = len(pool)          # the whole population of these typologies
        else:
            take = min(n_per_class, len(pool))
        idx = list(range(len(pool)))
        rng.shuffle(idx)
        sample += [pool.iloc[i] for i in idx[:take]]
    rng.shuffle(sample)

    store = GraphStore(prefer=backend)
    # narrator=None on purpose: the LLM writes prose, which no metric here
    # reads, and a free-tier quota would cap the sample size at 20.
    agent = InvestigationAgent(store=store, memory=ReplayMemory(store), narrator=None)

    results: list[CaseResult] = []
    t0 = time.perf_counter()
    for i, row in enumerate(sample, 1):
        trig = _trigger_for(row, trigger_mode)
        if trig is None:
            continue
        try:
            answer = agent.investigate(trig)
        except Exception as exc:  # noqa: BLE001 - recorded, never hidden
            results.append(CaseResult(
                case_id=str(row.case_id), truth=str(row.outcome), verdict="error",
                probability=float("nan"), confidence=0.0, pattern="", n_evidence=0,
                tool_calls=0, bank_risk_score=None,
                truth_pattern=str(row.pattern), error=f"{type(exc).__name__}: {exc}",
            ))
            continue
        c = answer.case
        results.append(CaseResult(
            case_id=str(row.case_id),
            truth=str(row.outcome),
            verdict=c.verdict.value,
            probability=float(c.fraud_probability),
            confidence=float(answer.risk.confidence if answer.risk else 0.0),
            pattern=c.pattern.value,
            n_evidence=len(c.evidence),
            tool_calls=answer.tool_calls,
            bank_risk_score=(float(answer.risk.bank_risk_score)
                             if answer.risk and answer.risk.bank_risk_score is not None
                             else None),
            truth_pattern=str(row.pattern),
            leaked=_leakage(answer, trig, closed_at_by_id) + _window_leakage(answer, trig),
        ))
        if progress and i % 25 == 0:
            done = len(results)
            rate = (time.perf_counter() - t0) / max(1, done)
            print(f"  {done}/{len(sample)}  ({rate:.2f}s/case, "
                  f"~{rate * (len(sample) - done) / 60:.1f} min left)",
                  file=sys.stderr, flush=True)

    return score(results, trigger_mode=trigger_mode, backend=store.backend_name,
                 seed=seed, elapsed_s=round(time.perf_counter() - t0, 1))


# -------------------------------------------------------------- the scoring
def _auc(pairs: list[tuple[float, int]]) -> float | None:
    """ROC AUC by rank, so it does not depend on where the verdict cut falls."""
    usable = [(p, y) for p, y in pairs if not math.isnan(p)]
    pos = [p for p, y in usable if y == 1]
    neg = [p for p, y in usable if y == 0]
    if not pos or not neg:
        return None
    ranked = sorted(usable, key=lambda t: t[0])
    ranks: dict[int, float] = {}
    i = 0
    while i < len(ranked):
        j = i
        while j + 1 < len(ranked) and ranked[j + 1][0] == ranked[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0          # average rank over the tie block
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    rank_sum = sum(r for k, r in ranks.items() if ranked[k][1] == 1)
    return (rank_sum - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg))


def score(results: list[CaseResult], **meta) -> dict:
    ok = [r for r in results if r.verdict != "error"]
    errors = [r for r in results if r.verdict == "error"]
    leaks = [r for r in results if r.leaked]

    matrix: dict[str, dict[str, int]] = {
        truth: {v: 0 for v in ("fraud", "legitimate", "uncertain")}
        for truth in (FRAUD, CLEARED)
    }
    for r in ok:
        if r.truth in matrix and r.verdict in matrix[r.truth]:
            matrix[r.truth][r.verdict] += 1

    f, c = matrix[FRAUD], matrix[CLEARED]
    n_fraud, n_cleared = sum(f.values()), sum(c.values())

    # Two readings of "uncertain", both reported, because picking one quietly
    # would be choosing the flattering number.
    decided = {
        "tp": f["fraud"], "fn": f["legitimate"],
        "fp": c["fraud"], "tn": c["legitimate"],
        "excluded_uncertain": f["uncertain"] + c["uncertain"],
    }
    # Operationally, "uncertain" means the agent did not conclude fraud, so it
    # takes no fraud action: it behaves as a negative.
    conservative = {
        "tp": f["fraud"], "fn": f["legitimate"] + f["uncertain"],
        "fp": c["fraud"], "tn": c["legitimate"] + c["uncertain"],
        "excluded_uncertain": 0,
    }

    def metrics(m: dict) -> dict:
        tp, fp, fn, tn = m["tp"], m["fp"], m["fn"], m["tn"]
        prec = tp / (tp + fp) if tp + fp else None
        rec = tp / (tp + fn) if tp + fn else None
        spec = tn / (tn + fp) if tn + fp else None
        f1 = (2 * prec * rec / (prec + rec)) if prec and rec else None
        total = tp + fp + fn + tn
        return {
            **m,
            "precision": round(prec, 4) if prec is not None else None,
            "recall_on_fraud": round(rec, 4) if rec is not None else None,
            "specificity_on_cleared": round(spec, 4) if spec is not None else None,
            "f1": round(f1, 4) if f1 is not None else None,
            "accuracy": round((tp + tn) / total, 4) if total else None,
            "n_scored": total,
        }

    agent_auc = _auc([(r.probability, 1 if r.truth == FRAUD else 0) for r in ok])
    bank_pairs = [(r.bank_risk_score, 1 if r.truth == FRAUD else 0)
                  for r in ok if r.bank_risk_score is not None]
    bank_auc = _auc([(float(p), y) for p, y in bank_pairs])

    def band(rows):
        out = {}
        for r in rows:
            if math.isnan(r.probability):
                continue
            k = f"{int(r.probability * 5) / 5:.1f}-{int(r.probability * 5) / 5 + 0.2:.1f}"
            out[k] = out.get(k, 0) + 1
        return dict(sorted(out.items()))

    # Recall by the typology the analyst recorded. The project claims the
    # graph finds ring and structuring fraud; this is where that claim either
    # shows up or does not.
    by_pattern: dict[str, dict] = {}
    for r in ok:
        if r.truth != FRAUD:
            continue
        d = by_pattern.setdefault(r.truth_pattern or "unknown",
                                  {"n": 0, "called_fraud": 0, "mean_probability": 0.0})
        d["n"] += 1
        d["called_fraud"] += int(r.verdict == "fraud")
        d["mean_probability"] += r.probability
    for d in by_pattern.values():
        d["mean_probability"] = round(d["mean_probability"] / max(1, d["n"]), 3)
        d["recall"] = round(d["called_fraud"] / max(1, d["n"]), 4)
    by_pattern = dict(sorted(by_pattern.items(), key=lambda kv: -kv[1]["recall"]))

    return {
        **meta,
        "recall_by_true_pattern": by_pattern,
        "n_cases": len(results),
        "n_scored": len(ok),
        "n_errors": len(errors),
        "errors": [{"case_id": r.case_id, "error": r.error} for r in errors[:10]],
        "leakage_violations": len(leaks),
        "leakage_examples": [{"case_id": r.case_id, "retrieved": r.leaked} for r in leaks[:10]],
        "class_counts": {FRAUD: n_fraud, CLEARED: n_cleared},
        "confusion": matrix,
        "decided_only": metrics(decided),
        "uncertain_as_negative": metrics(conservative),
        "auc": {
            "agent_probability": round(agent_auc, 4) if agent_auc is not None else None,
            "bank_risk_score": round(bank_auc, 4) if bank_auc is not None else None,
            "n_with_bank_score": len(bank_pairs),
            "note": "rank-based, so it does not depend on where the verdict cut falls",
        },
        "probability_bands": {
            FRAUD: band([r for r in ok if r.truth == FRAUD]),
            CLEARED: band([r for r in ok if r.truth == CLEARED]),
        },
        "caveats": CAVEATS,
        "per_case": [vars(r) for r in results],
    }


CAVEATS = [
    "The sample is closed investigations, not alerts. Every cleared case is a "
    "HIGH-SCORING model alert that turned out to be legitimate -- the hardest "
    "negatives in the dataset, not average traffic. Specificity here is a "
    "lower bound on specificity in the wild.",
    "Precision depends on the fraud base rate among real alerts, which this "
    "data does not contain. The precision figure describes this sample only.",
    "Cleared cases carry exactly one transaction each and confirmed frauds can "
    "carry many, so burst-shaped detectors have less to find on the negative "
    "class than they would on real traffic.",
    "For confirmed frauds the replay starts at the first fraudulent "
    "transaction, which is a more informative starting point than a real alert "
    "would always give.",
    "This says nothing about the 20 exam cases, whose outcomes are unknown.",
    "The bank score's AUC on this sample is far below 0.5 because of the same "
    "selection: every cleared case is a high score and most confirmed frauds "
    "were customer disputes carrying low ones. It is not a statement about the "
    "bank's model on real traffic, and neither is any comparison with it.",
]


def report(res: dict) -> str:
    lines = [
        f"Backtest: {res['n_scored']} closed investigations replayed through the "
        f"full agent on '{res['backend']}'",
        f"  trigger mode: {res['trigger_mode']}  -- {_MODE_NOTE[res['trigger_mode']]}",
        f"  seed {res['seed']}, {res['elapsed_s']}s"
        + (f", {res['n_errors']} errors" if res["n_errors"] else ""),
        "",
    ]
    if res["leakage_violations"]:
        lines += [f"  !! LEAKAGE: {res['leakage_violations']} case(s) retrieved "
                  f"an investigation from their own future", ""]
    else:
        lines += ["  no leakage: no case retrieved an investigation closed at or "
                  "after its own alert, and no transaction window reached past it", ""]

    m = res["confusion"]
    lines += ["  Agent verdict vs the bank's recorded outcome:",
              f"    {'':<20}{'fraud':>10}{'legitimate':>12}{'uncertain':>11}"]
    for truth, label in ((FRAUD, "confirmed fraud"), (CLEARED, "cleared")):
        r = m[truth]
        lines.append(f"    {label:<20}{r['fraud']:>10}{r['legitimate']:>12}{r['uncertain']:>11}")
    lines.append("")

    for key, title in (("uncertain_as_negative", "uncertain treated as 'no fraud action'"),
                       ("decided_only", "uncertain excluded")):
        d = res[key]
        lines += [f"  {title} (n={d['n_scored']}):",
                  f"    recall on fraud      {_pct(d['recall_on_fraud'])}"
                  f"   ({d['tp']} of {d['tp'] + d['fn']} confirmed frauds called fraud)",
                  f"    specificity          {_pct(d['specificity_on_cleared'])}"
                  f"   ({d['tn']} of {d['tn'] + d['fp']} cleared cases not called fraud)",
                  f"    precision            {_pct(d['precision'])}   (this sample's mix only)",
                  ""]

    bp = res.get("recall_by_true_pattern") or {}
    if bp:
        lines += ["  Recall by the typology the analyst recorded:"]
        for pat, d in bp.items():
            lines.append(f"    {pat:<30}{d['called_fraud']:>4}/{d['n']:<5}"
                         f"{_pct(d['recall'])}   mean p={d['mean_probability']:.2f}")
        lines.append("")

    a = res["auc"]
    lines += ["  Ranking power (threshold-free):",
              f"    agent probability    AUC {a['agent_probability']}",
              f"    bank risk score      AUC {a['bank_risk_score']}"
              f"   (on the {a['n_with_bank_score']} cases carrying one)",
              ""]
    lines += ["  Read this with:"]
    lines += [f"    - {c}" for c in res["caveats"]]
    return "\n".join(lines)


_MODE_NOTE = {
    "neutral": "every case arrives as an analyst_request (prior 0.00 log-odds, "
               "p=0.50), so ONLY graph evidence can move the answer",
    "score":   "every case arrives as a model score (prior -1.60, p=0.17), the "
               "agent's operating point for the exam",
    "actual":  "the real trigger -- READ THE CAVEATS: in this history the "
               "trigger nearly is the label",
}


def _pct(v) -> str:
    return "    n/a" if v is None else f"{v * 100:6.2f}%"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Replay closed cases through the agent")
    ap.add_argument("--n", type=int, default=300, help="cases per class")
    ap.add_argument("--backend", default="local", choices=["local", "tigergraph", "mcp", "auto"])
    ap.add_argument("--trigger", default="neutral",
                    choices=["neutral", "score", "actual"])
    ap.add_argument("--seed", type=int, default=20260920)
    ap.add_argument("--patterns", default="",
                    help="comma-separated fraud typologies; takes ALL cases of each")
    ap.add_argument("--all-modes", action="store_true",
                    help="run neutral, score and actual, and report all three")
    args = ap.parse_args(argv)

    modes = ["neutral", "score", "actual"] if args.all_modes else [args.trigger]
    pats = tuple(x.strip() for x in args.patterns.split(",") if x.strip())
    suffix = (":" + "+".join(pats)) if pats else ""
    # merge into whatever is already on disk, so the rare-typology run and the
    # general run can be built by two commands and read as one artefact
    path = PATHS.build / "backtest.json"
    out = {}
    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    for mode in modes:
        print(f"Replaying {args.n} per class, trigger mode '{mode}' ...", file=sys.stderr)
        res = replay(n_per_class=args.n, backend=args.backend,
                     trigger_mode=mode, seed=args.seed, patterns=pats)
        print("\n" + report(res) + "\n")
        res["patterns"] = list(pats)
        # keyed by mode *and* typology filter, so a targeted run does not
        # overwrite the general one -- they answer different questions
        out[mode + suffix] = res

    PATHS.build.mkdir(parents=True, exist_ok=True)
    slim = dict(existing)
    slim.update({
        key: {k: v for k, v in r.items() if k != "per_case"} for key, r in out.items()
    })
    slim["generated_at"] = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).isoformat()
    path.write_text(json.dumps(slim, indent=2, default=str), encoding="utf-8")
    (PATHS.build / "backtest_cases.json").write_text(
        json.dumps({k: r["per_case"] for k, r in out.items()}, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"wrote {path}", file=sys.stderr)
    return 1 if any(r["leakage_violations"] or r["n_errors"] for r in out.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
