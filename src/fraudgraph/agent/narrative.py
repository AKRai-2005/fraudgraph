"""Case summary and SAR narrative.

Both are written deterministically from the evidence that was actually
retrieved.  If an LLM narrator is configured it may *rewrite* that text for
readability, but its output is validated before use: it may not introduce an
identifier, an amount or a date that is not already in the evidence, and if it
does the deterministic text is kept.  The LLM therefore cannot invent evidence.
"""
from __future__ import annotations

import re

from ..schemas import AnswerFile, Verdict

ID_RE = re.compile(r"\b(?:C\d{5}(?:-K\d)?|CC-\d{4}|\d{7})\b")
MONEY_RE = re.compile(r"\$[\d,]+(?:\.\d{2})?")
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def _allowed_tokens(answer: AnswerFile, feats) -> set[str]:
    allowed: set[str] = {feats.card_id, feats.customer_id, feats.txn_id}
    c = answer.case
    allowed |= set(c.affected_txn_ids) | set(c.connected_card_ids) | set(c.similar_prior_cases)
    if c.first_suspicious_txn_id:
        allowed.add(c.first_suspicious_txn_id)
    for e in c.evidence:
        allowed |= {str(x) for x in e.entity_ids}
        allowed |= set(ID_RE.findall(e.claim))
        allowed |= set(MONEY_RE.findall(e.claim))
        allowed |= set(DATE_RE.findall(e.claim))
    allowed |= set(MONEY_RE.findall(f"${c.exposure_usd:,.2f}"))
    allowed.add(f"${c.exposure_usd:,.2f}")
    allowed |= set(DATE_RE.findall(feats.ts))
    allowed |= set(MONEY_RE.findall(f"${feats.amount:,.2f}"))
    return {a for a in allowed if a}


def _validate(text: str, answer: AnswerFile, feats) -> tuple[bool, str]:
    """Reject LLM text that introduces identifiers/amounts/dates we never saw."""
    allowed = _allowed_tokens(answer, feats)
    for pattern, label in ((ID_RE, "identifier"), (MONEY_RE, "amount"), (DATE_RE, "date")):
        for tok in set(pattern.findall(text)):
            if tok not in allowed:
                return False, f"introduced an unsupported {label}: {tok}"
    return True, ""


# ------------------------------------------------------------------ summary


def _deterministic_summary(answer: AnswerFile, feats, risk) -> str:
    c = answer.case
    matched = [f for f in answer.findings if f.matched and f.strength >= 0.3]
    incul = [f for f in matched if f.name not in ("recurring_charge", "consistent_with_history")]
    excul = [f for f in matched if f.name in ("recurring_charge", "consistent_with_history")]

    lead = (
        f"{answer.trigger.get('trigger_type', 'alert').replace('_', ' ').capitalize()} on card "
        f"{feats.card_id}: ${feats.amount:,.2f} {feats.channel.replace('_', '-')} on "
        f"{feats.ts[:10]}, scored {feats.bank_risk_score:.2f} by the bank's model."
    )
    if incul:
        body = " " + " ".join(f"{f.why.capitalize()}." for f in incul[:3])
    elif excul:
        body = " " + " ".join(f"{f.why.capitalize()}." for f in excul[:2])
    else:
        body = (
            " Nothing in the card's graph neighbourhood departs from its established behaviour: "
            f"{feats.hist_n_txns} prior transactions, median ${feats.hist_median_amt:,.2f}, "
            f"{feats.region_prior_txns} of them in the same billing region."
        )
    if c.similar_prior_cases:
        body += (
            f" Case memory returned {len(c.similar_prior_cases)} comparable closed "
            f"investigation(s) ({', '.join(c.similar_prior_cases[:3])}), used as context rather "
            "than as a finding about this transaction."
        )
    if risk.conflicting_evidence:
        body += " " + risk.conflicting_evidence[0].capitalize() + "."

    verdict_txt = {
        Verdict.FRAUD: (
            f"Assessed as fraud at probability {c.fraud_probability:.2f} on "
            f"{risk.independent_signal_count} independent signals, with exposure "
            f"${c.exposure_usd:,.2f} across {len(c.affected_txn_ids)} transaction(s)."
        ),
        Verdict.LEGITIMATE: (
            f"Assessed as legitimate at fraud probability {c.fraud_probability:.2f}; the bank "
            f"model's score of {feats.bank_risk_score:.2f} is not corroborated by the graph."
        ),
        Verdict.UNCERTAIN: (
            f"Verdict uncertain at probability {c.fraud_probability:.2f} with confidence "
            f"{risk.confidence:.2f}; the evidence does not yet settle the question."
        ),
    }[c.verdict]
    actions = ", ".join(
        f"{a.action.value} ({a.route.value})" for a in answer.next_best_actions.final
    )
    return f"{lead}{body} {verdict_txt} Recommended: {actions}."


def write_summary(answer: AnswerFile, feats, risk, narrator=None) -> str:
    base = _deterministic_summary(answer, feats, risk)
    if narrator is None:
        return base
    try:
        rewritten = narrator.rewrite_summary(base, answer, feats, risk)
    except Exception:  # noqa: BLE001 - narrator failure must not fail the case
        return base
    if not rewritten:
        return base
    ok, _why = _validate(rewritten, answer, feats)
    return rewritten.strip() if ok else base


# ---------------------------------------------------------------- SAR


def _deterministic_sar(answer: AnswerFile, feats, risk, shared) -> str:
    """Who, what, when, where, how, why -- FinCEN's narrative structure."""
    c = answer.case
    dates = list(answer.sar.activity_dates or [])
    first_date = dates[0] if dates else feats.ts[:10]
    last_date = dates[1] if len(dates) > 1 else first_date
    matched = [f for f in answer.findings if f.matched and f.name not in
               ("recurring_charge", "consistent_with_history")]
    top = max(matched, key=lambda f: f.strength) if matched else None

    who = (
        f"Customer {feats.customer_id}, holder of card {feats.card_id}"
        + (f" and {feats.customer_n_cards - 1} other card(s) with this institution"
           if feats.customer_n_cards > 1 else "")
        + "."
    )
    what = (
        f"Between {first_date} and {last_date}, {len(c.affected_txn_ids)} transaction(s) totalling "
        f"${c.exposure_usd:,.2f} were identified as part of a single suspected fraud episode, "
        f"beginning with transaction {c.first_suspicious_txn_id}."
    )
    where = (
        f"The activity was conducted {feats.channel.replace('_', '-')}"
        + (f" under product code {feats.product_cd}" if feats.product_cd else "")
        + (f", billed in region {feats.region_id:.0f}" if feats.region_id is not None else "")
        + (f", from device profile '{feats.device_profile}'" if feats.device_profile else "")
        + "."
    )
    how = (top.why.capitalize() + ".") if top else (
        "The activity departs from the cardholder's established transaction profile."
    )
    why_bits = [
        f"The cardholder's established profile over {feats.hist_n_txns} prior transactions shows a "
        f"median amount of ${feats.hist_median_amt:,.2f} and a maximum of ${feats.hist_max_amt:,.2f}."
    ]
    if shared:
        why_bits.append(
            f"The same device profile was used on {len(shared.card_ids)} other cardholders' cards "
            f"inside the same window, indicating a common actor across multiple customers rather "
            f"than an isolated compromise."
        )
    if c.similar_prior_cases:
        why_bits.append(
            f"Closed investigation(s) {', '.join(c.similar_prior_cases[:3])} record comparable "
            f"activity previously confirmed by this institution."
        )
    if feats.bank_risk_score < 0.3:
        why_bits.append(
            f"The institution's detection model scored the flagged transaction only "
            f"{feats.bank_risk_score:.2f}; the suspicion arises from the relationship evidence, "
            f"not from the model score."
        )
    customer_stmt = ""
    if answer.evidence_requests:
        req = answer.evidence_requests[0]
        customer_stmt = (
            " The cardholder was contacted for validation; the response recorded in this "
            "institution's case file is a simulated response, as no live customer channel was "
            "connected for this investigation."
        )
    actions = ", ".join(a.action.value for a in answer.next_best_actions.final)
    closing = (
        f"The institution's recommended disposition is: {actions}. "
        f"Actions requiring team-lead or fraud-manager approval are recorded as awaiting approval "
        f"and have not been executed."
    )
    return " ".join([who, what, where, how, *why_bits]) + customer_stmt + " " + closing


def write_sar_narrative(answer: AnswerFile, feats, risk, shared, narrator=None) -> str:
    base = _deterministic_sar(answer, feats, risk, shared)
    if narrator is None:
        return base
    try:
        rewritten = narrator.rewrite_sar(base, answer, feats, risk)
    except Exception:  # noqa: BLE001
        return base
    if not rewritten:
        return base
    ok, _why = _validate(rewritten, answer, feats)
    return rewritten.strip() if ok else base
