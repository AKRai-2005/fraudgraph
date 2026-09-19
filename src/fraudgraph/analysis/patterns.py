"""Deterministic fraud-pattern detectors.

The five documented typologies from the dataset README, plus two typologies that
are *not* documented there but are present in the data and described in the
analyst notes of the nine ``undocumented`` closed cases (see
docs/DATA_NOTES.md).  Detectors are keyed on behaviour, never on case ids.

Each detector returns a ``PatternFinding`` with a strength in [0, 1], the
transactions it believes form the episode, and an explicit statement of what it
cannot rule out.  No LLM is involved.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from ..schemas import Pattern, PatternFinding
from .features import CaseContext, Features, ROUND_THRESHOLDS, STRUCTURING_BAND, _f

SMALL_AUTH_ABS = 5.0
RING_MIN_CARDS = 8
RING_WINDOW_DAYS = 30


@dataclass
class Episode:
    """The transactions a detector believes belong to one fraud episode."""

    txn_ids: list[str]
    first_txn_id: str
    exposure: float
    first_ts: str = ""
    last_ts: str = ""


def _rows(ctx: CaseContext) -> list[dict]:
    """The +/-72h burst window: what 'around this alert' means."""
    return (ctx.window or {}).get("transactions") or []


def _wide(ctx: CaseContext, days: float) -> list[dict]:
    """A wider slice of the same card, for episodes that span more than 72h.

    A device ring or an out-of-region run can stretch over weeks, so exposure
    must be summed over the episode's own span rather than the burst window.
    """
    rows = (ctx.wide_window or {}).get("transactions") or []
    if not rows or not ctx.ts:
        return _rows(ctx)
    centre = pd.Timestamp(ctx.ts)
    return [
        r for r in rows
        if abs((pd.Timestamp(r["ts"]) - centre).total_seconds()) <= days * 86400
    ]


def _episode(rows: list[dict]) -> Episode:
    if not rows:
        return Episode([], "", 0.0)
    ordered = sorted(rows, key=lambda r: pd.Timestamp(r["ts"]))
    return Episode(
        txn_ids=[str(r["TransactionID"]) for r in ordered],
        first_txn_id=str(ordered[0]["TransactionID"]),
        exposure=round(sum(abs(_f(r["TransactionAmt"])) for r in ordered), 2),
        first_ts=str(ordered[0]["ts"])[:10],
        last_ts=str(ordered[-1]["ts"])[:10],
    )


def _near(rows: list[dict], centre: pd.Timestamp, hours: float) -> list[dict]:
    return [
        r for r in rows
        if abs((pd.Timestamp(r["ts"]) - centre).total_seconds()) <= hours * 3600
    ]


# ---------------------------------------------------------------- detectors


def detect_card_testing(ctx: CaseContext, f: Features) -> tuple[PatternFinding, Episode | None]:
    """Pattern 1 / policy R5: three or more tiny online authorisations, often
    under $5, inside an hour, then a larger purchase."""
    rows = _rows(ctx)
    centre = pd.Timestamp(ctx.ts)
    # "three or more tiny online authorizations, often under $5".  An earlier,
    # looser cut (8% of the card's median, uncapped) fired on 29 legitimate
    # cases against 15 frauds in the closed history -- a card whose median is
    # $200 makes three ordinary $16 purchases look like a testing run.  The cut
    # is now absolute-first and capped, and a larger follow-up purchase is
    # required rather than optional.
    small_cut = (
        min(10.0, max(SMALL_AUTH_ABS, 0.04 * f.hist_median_amt))
        if f.hist_median_amt else SMALL_AUTH_ABS
    )
    online = [r for r in rows if r.get("channel") == "online"]
    best: tuple[int, list[dict]] = (0, [])
    for anchor in online:
        a_ts = pd.Timestamp(anchor["ts"])
        group = [
            r for r in online
            if 0 <= (pd.Timestamp(r["ts"]) - a_ts).total_seconds() <= 3600
            and _f(r["TransactionAmt"]) <= small_cut
        ]
        if len(group) > best[0]:
            best = (len(group), group)
    n_small, small_group = best
    follow: list[dict] = []
    if n_small >= 3:
        last_small = max(pd.Timestamp(r["ts"]) for r in small_group)
        med_small = sorted(_f(r["TransactionAmt"]) for r in small_group)[n_small // 2]
        follow = [
            r for r in online
            if 0 < (pd.Timestamp(r["ts"]) - last_small).total_seconds() <= 6 * 3600
            and _f(r["TransactionAmt"]) >= max(25.0, 10.0 * max(med_small, 0.5))
        ]
    # the sequence is the signal: small run *followed by* a materially larger
    # purchase.  A run of small authorisations on its own is ordinary.
    matched = n_small >= 3 and bool(follow)
    strength = 0.0
    if matched:
        strength = min(0.95, 0.74 + 0.05 * (n_small - 3) + 0.06 * min(len(follow), 2))
    why = (
        f"{n_small} online authorisations at or below ${small_cut:.2f} within one hour"
        + (f", followed by {len(follow)} larger purchase(s) within six hours" if follow else "")
        if matched else
        f"no run of 3+ small online authorisations within an hour (largest run {n_small})"
    )
    ep = _episode(small_group + follow) if matched else None
    return (
        PatternFinding(
            pattern=Pattern.CARD_TESTING, name="card_testing", matched=matched,
            strength=strength, why=why,
            limitations="Small online authorisations also occur legitimately "
                        "(digital top-ups, subscription trials); the sequence is the signal.",
            txn_ids=ep.txn_ids if ep else [],
            entity_ids=[f.card_id],
        ),
        ep,
    )


def detect_structuring(ctx: CaseContext, f: Features) -> tuple[PatternFinding, Episode | None]:
    """Undocumented typology A, read from closed cases CC-3748 / CC-3841 /
    CC-3907 / CC-4086 / CC-4124: several online purchases in under an hour, each
    just below a round authorisation threshold."""
    rows = _rows(ctx)
    best: tuple[float, float, list[dict]] = (0.0, 0.0, [])
    for thresh in ROUND_THRESHOLDS:
        lo, hi = STRUCTURING_BAND * thresh, thresh
        band = [r for r in rows if r.get("channel") == "online" and lo <= _f(r["TransactionAmt"]) < hi]
        for anchor in band:
            a_ts = pd.Timestamp(anchor["ts"])
            group = [
                r for r in band
                if abs((pd.Timestamp(r["ts"]) - a_ts).total_seconds()) <= 3600
            ]
            if len(group) > len(best[2]):
                best = (thresh, hi - min(_f(r["TransactionAmt"]) for r in group), group)
    thresh, _, group = best
    # Three conditions, all from the analyst notes on CC-3748 and its siblings:
    # several purchases, inside one short window, whose *combined* total clears
    # the threshold each one individually stays under.  Requiring the total is
    # what separates structuring from a card that simply makes purchases in that
    # price band; without it the detector fired on 25 legitimate cases against
    # 14 frauds in the closed history.
    total_amt = sum(_f(r["TransactionAmt"]) for r in group)
    distinct = len({round(_f(r["TransactionAmt"]), 2) for r in group})
    matched = (
        len(group) >= 3
        and total_amt > thresh * 1.5
        and distinct >= max(2, len(group) - 1)
    )
    strength = 0.0
    if matched:
        span_min = (
            max(pd.Timestamp(r["ts"]) for r in group)
            - min(pd.Timestamp(r["ts"]) for r in group)
        ).total_seconds() / 60.0
        total = sum(_f(r["TransactionAmt"]) for r in group)
        strength = 0.62 + min(0.18, 0.06 * (len(group) - 3))
        if span_min <= 60:
            strength += 0.10
        if f.channel_novel or f.product_novel:
            strength += 0.08
        strength = min(0.95, strength)
    else:
        span_min = total = 0.0
    why = (
        f"{len(group)} online purchases within {span_min:.0f} minutes, each between "
        f"${STRUCTURING_BAND * thresh:,.0f} and ${thresh:,.0f} (total ${total:,.2f}); "
        f"amounts sit just under the ${thresh:,.0f} authorisation threshold"
        if matched else
        "no run of 3+ online purchases clustered just under a round authorisation threshold"
    )
    ep = _episode(group) if matched else None
    return (
        PatternFinding(
            pattern=Pattern.UNDOCUMENTED, name="sub_threshold_structuring", matched=matched,
            strength=strength, why=why,
            limitations="A single merchant with fixed high-value pricing could produce "
                        "similar amounts; the sub-threshold clustering plus the short span is what distinguishes it.",
            txn_ids=ep.txn_ids if ep else [],
            entity_ids=[f.card_id],
            description=(
                "Sub-threshold structuring: several online purchases executed within a single "
                f"short window, each priced just below the ${thresh:,.0f} authorisation "
                "threshold so that no individual charge triggers step-up review, while the "
                f"combined total (${total:,.2f}) far exceeds it. It affects one cardholder at a "
                "time and was found by scanning the card's own transaction window for amount "
                "clustering immediately beneath round thresholds rather than by any single "
                "transaction looking unusual."
            ) if matched else "",
        ),
        ep,
    )


def detect_device_ring(ctx: CaseContext, f: Features) -> tuple[PatternFinding, Episode | None]:
    """Undocumented typology B, read from closed cases CC-2649 / CC-2971 /
    CC-2985 / CC-3035: one device fingerprint, new to every account it touches
    and behind an anonymising proxy, spread across many unrelated cards."""
    dr = ctx.device_ring or {}
    cards = int(dr.get("n_cards") or 0)
    matched = (
        bool(f.device_profile)
        and cards >= RING_MIN_CARDS
        and f.device_marked_new
        and f.anonymous_proxy
        and f.ring_new_fraction >= 0.9
    )
    strength = 0.0
    if matched:
        strength = min(0.95, 0.66 + 0.012 * min(cards, 20) + (0.06 if f.ring_new_fraction >= 0.99 else 0.0))
    why = (
        f"device profile shared by {cards} unrelated cards within {RING_WINDOW_DAYS} days, "
        f"marked New for {f.ring_new_fraction:.0%} of the transactions it ever appears on, "
        f"and consistently behind {f.proxy_flag or 'an anonymising proxy'}"
        if matched else
        f"device profile touches {cards} cards in the window; "
        + ("not marked New for this account" if not f.device_marked_new else "")
        + ("; no anonymising proxy" if not f.anonymous_proxy else "")
    )
    # The ring episode is every transaction this card made from the ring device
    # inside the ring window, which can be weeks rather than the 72h burst window.
    dev_rows = [
        r for r in _wide(ctx, RING_WINDOW_DAYS)
        if r.get("device_profile") == f.device_profile
    ]
    ep = _episode(dev_rows) if matched and dev_rows else None
    others = [
        c["card_id"] for c in (dr.get("cards") or []) if c.get("card_id") != f.card_id
    ]
    return (
        PatternFinding(
            pattern=Pattern.UNDOCUMENTED, name="shared_device_ring", matched=matched,
            strength=strength, why=why,
            limitations="Device profiles are coarse fingerprints (model + OS + browser + screen) "
                        "and common desktop fingerprints are shared by many unrelated customers. "
                        "What isolates this one is that it is New for every account it touches and "
                        "always proxied.",
            txn_ids=ep.txn_ids if ep else [],
            entity_ids=[f.device_profile] + others[:40],
            description=(
                "Coordinated shared-device ring: a single mobile device fingerprint, always "
                "reached through an anonymising proxy, appears on many unrelated cardholders' "
                "accounts within a few weeks and is marked New for every one of them. No "
                "cardholder has any prior association with the device. It was found by pivoting "
                "from the flagged transaction to the device profile vertex and counting distinct "
                "cards on it inside the alert window, then checking the New-device and proxy "
                "flags across the device's whole history."
            ) if matched else "",
        ),
        ep,
    )


def detect_out_of_region(ctx: CaseContext, f: Features) -> tuple[PatternFinding, Episode | None]:
    """Pattern 4: card-present purchases in a billing region the cardholder has
    no history in, while normal activity continues at home."""
    if f.channel != "in_person" or f.region_id is None:
        return (
            PatternFinding(
                pattern=Pattern.OUT_OF_REGION_USE, name="out_of_region_use", matched=False,
                strength=0.0, why="not a card-present transaction with a billing region",
            ),
            None,
        )
    rows = _rows(ctx)
    centre = pd.Timestamp(ctx.ts)
    # an out-of-region run can span several days, so look wider than the burst window
    same_region = [
        r for r in _wide(ctx, 10.0)
        if _f(r.get("addr1"), -1) == f.region_id and r.get("channel") == "in_person"
    ]
    home = [
        r for r in rows
        if f.home_region is not None and _f(r.get("addr1"), -1) == f.home_region
    ]
    # a trip: several days of activity in the new region and no home activity meanwhile
    if same_region:
        span_days = (
            max(pd.Timestamp(r["ts"]) for r in same_region)
            - min(pd.Timestamp(r["ts"]) for r in same_region)
        ).total_seconds() / 86400.0
    else:
        span_days = 0.0
    home_near = _near(home, centre, 24)
    f.home_activity_continues = bool(home_near)
    f.region_days_span = round(span_days, 2)

    matched = f.region_novel and f.channel == "in_person"
    strength = 0.0
    if matched:
        strength = 0.45
        if home_near:
            # cardholder is transacting at home the same day -> not a trip
            strength = 0.72
        if span_days <= 1.0:
            strength += 0.08
        if f.amount_novel:
            strength += 0.05
        strength = min(0.92, strength)
    home_txt = f"{f.home_region:.0f}" if f.home_region is not None else "unknown"
    if matched:
        why = (
            f"card-present use in billing region {f.region_id:.0f}, where this card has no prior "
            f"history across {f.hist_n_txns} transactions (home region {home_txt})"
            + (
                f"; home-region activity continues within 24 hours, so this is not a trip"
                if home_near else
                f"; activity in the new region spans {span_days:.1f} days"
            )
        )
    else:
        why = (
            f"card has {f.region_prior_txns} prior transactions in billing region "
            f"{f.region_id:.0f} (home region {home_txt})"
        )
    ep = _episode(same_region) if matched and same_region else None
    return (
        PatternFinding(
            pattern=Pattern.OUT_OF_REGION_USE, name="out_of_region_use", matched=matched,
            strength=strength, why=why,
            limitations="Travel produces the same shape. The discriminator used here is whether "
                        "home-region activity continues during the same window; 716 of the 900 "
                        "cleared closed cases were exactly this alert cleared as travel.",
            txn_ids=ep.txn_ids if ep else [],
            entity_ids=[f.card_id] + ([str(int(f.region_id))] if f.region_id is not None else []),
        ),
        ep,
    )


def detect_cnp_new_device(ctx: CaseContext, f: Features) -> tuple[PatternFinding, Episode | None]:
    """Pattern 3: card-not-present use from a device marked New for the account."""
    matched = (
        f.channel == "online"
        and f.device_novel
        and f.device_marked_new
        and (f.amount_novel or f.product_novel or f.channel_novel or f.n_txns_48h >= 2)
    )
    strength = 0.0
    if matched:
        strength = 0.52
        if f.anonymous_proxy:
            strength += 0.14
        if f.amount_novel:
            strength += 0.10
        if f.channel_novel:
            strength += 0.08
        if f.match_flag_anomaly:
            strength += 0.06
        strength = min(0.90, strength)
    why = (
        f"online purchase from a device profile never seen on this card "
        f"({f.n_devices_seen} devices previously seen), marked New for the account"
        + (f", behind {f.proxy_flag}" if f.anonymous_proxy else "")
        if matched else
        ("device has been used on this card before" if f.device_prior_txns else
         "not an online transaction from a new device with corroborating novelty")
    )
    rows = [r for r in _rows(ctx) if r.get("device_profile") == f.device_profile]
    ep = _episode(rows) if matched and rows else None
    return (
        PatternFinding(
            pattern=Pattern.CARD_NOT_PRESENT_NEW_DEVICE, name="cnp_new_device", matched=matched,
            strength=strength, why=why,
            limitations="People buy new phones and laptops; 158 of the 900 cleared closed cases "
                        "were a new-device alert the cardholder confirmed. A new device alone is "
                        "not proof, which is why corroboration is required here.",
            txn_ids=ep.txn_ids if ep else [],
            entity_ids=[f.card_id] + ([f.device_profile] if f.device_profile else []),
        ),
        ep,
    )


def detect_cnp_fraud(ctx: CaseContext, f: Features) -> tuple[PatternFinding, Episode | None]:
    """Pattern 2: card-not-present use with amounts or products that do not fit
    the cardholder's history, often a short burst."""
    rows = _rows(ctx)
    centre = pd.Timestamp(ctx.ts)
    near48 = [r for r in _near(rows, centre, 48) if r.get("channel") == "online"]
    inconsistent = f.amount_novel or f.product_novel or f.channel_novel
    matched = f.channel == "online" and inconsistent
    strength = 0.0
    if matched:
        strength = 0.38
        if f.amount_novel:
            strength += 0.14
        if f.product_novel:
            strength += 0.10
        if f.channel_novel:
            strength += 0.12
        if 2 <= len(near48) <= 4:
            strength += 0.08
        if f.device_novel:
            strength += 0.06
        strength = min(0.88, strength)
    bits = []
    if f.amount_novel:
        bits.append(f"${f.amount:,.2f} exceeds the card's historical maximum of ${f.hist_max_amt:,.2f}")
    if f.product_novel:
        bits.append(f"product code {f.product_cd} never used on this card")
    if f.channel_novel:
        bits.append(f"online accounts for only {f.channel_share:.1%} of this card's history")
    why = "; ".join(bits) if bits else "online activity consistent with the cardholder's history"
    ep = _episode(near48) if matched and near48 else None
    return (
        PatternFinding(
            pattern=Pattern.CARD_NOT_PRESENT_FRAUD, name="cnp_fraud", matched=matched,
            strength=strength, why=why,
            limitations="A single unusual online purchase is ambiguous on its own; the dataset "
                        "README says to verify rather than conclude.",
            txn_ids=ep.txn_ids if ep else [],
            entity_ids=[f.card_id],
        ),
        ep,
    )


def detect_account_takeover(ctx: CaseContext, f: Features) -> tuple[PatternFinding, Episode | None]:
    """Pattern 5: mixed-channel activity inconsistent with the cardholder, with
    device and match-flag anomalies -- stolen credentials rather than a stolen
    number."""
    rows = _rows(ctx)
    centre = pd.Timestamp(ctx.ts)
    near = _near(rows, centre, 48)
    channels = {r.get("channel") for r in near}
    mixed = len(channels) > 1
    anomalies = sum([f.device_novel, f.device_marked_new, f.match_flag_anomaly,
                     f.anonymous_proxy, f.email_novel])
    matched = mixed and anomalies >= 2 and (f.amount_novel or f.product_novel or f.channel_novel)
    strength = 0.0
    if matched:
        strength = min(0.88, 0.44 + 0.09 * anomalies + (0.06 if f.amount_novel else 0.0))
    why = (
        f"activity on both channels within 48 hours with {anomalies} identity anomalies "
        f"(new device={f.device_novel}, device marked New={f.device_marked_new}, "
        f"match flag={f.match_status or 'n/a'}, proxy={f.proxy_flag or 'none'}, "
        f"new email domain={f.email_novel})"
        if matched else
        f"{'mixed-channel activity but only ' + str(anomalies) + ' identity anomalies' if mixed else 'single-channel activity in the window'}"
    )
    ep = _episode(near) if matched and near else None
    return (
        PatternFinding(
            pattern=Pattern.ACCOUNT_TAKEOVER, name="account_takeover", matched=matched,
            strength=strength, why=why,
            limitations="Mixed-channel use is normal for many cardholders; this detector requires "
                        "identity anomalies on top of the channel mix.",
            txn_ids=ep.txn_ids if ep else [],
            entity_ids=[f.card_id, f.customer_id],
        ),
        ep,
    )


def detect_recurring_charge(ctx: CaseContext, f: Features) -> PatternFinding:
    """Policy R7's shape: a disputed charge that matches the cardholder's own
    established recurring pattern. This is *exculpatory* evidence."""
    matched = f.looks_recurring
    strength = 0.0
    if matched:
        strength = min(0.85, 0.55 + 0.1 * min(f.same_amount_before, 3))
    why = (
        f"${f.amount:,.2f} has been charged to this card {f.same_amount_before} times before at a "
        f"regular cadence of about {f.recurring_cadence_days} days, matching a recurring charge"
        if matched else
        f"amount ${f.amount:,.2f} seen {f.same_amount_before} time(s) before"
        + (f" at an irregular cadence ({f.recurring_cadence_days} days average)"
           if f.recurring_cadence_days else "")
    )
    return PatternFinding(
        pattern=Pattern.NONE, name="recurring_charge", matched=matched, strength=strength,
        why=why,
        limitations="A fraudster repeating an identical amount monthly would look the same; "
                    "the cardholder should still be asked to confirm (policy R7 says verify, not block).",
        entity_ids=[f.card_id],
    )


def detect_consistent_with_history(ctx: CaseContext, f: Features) -> PatternFinding:
    """Exculpatory: nothing about the transaction departs from the card's own
    established behaviour."""
    reasons = []
    if f.hist_n_txns >= 20:
        if f.amt_over_p95 and f.amt_over_p95 <= 1.0:
            reasons.append(f"${f.amount:,.2f} is at or below the card's 95th-percentile amount (${f.hist_p95_amt:,.2f})")
        if f.channel_share >= 0.20:
            reasons.append(f"{f.channel} is {f.channel_share:.0%} of this card's history")
        if f.region_prior_txns >= 3:
            reasons.append(f"{f.region_prior_txns} prior transactions in billing region {f.region_id:.0f}")
        if f.product_share >= 0.05:
            reasons.append(f"product code {f.product_cd} is {f.product_share:.0%} of this card's history")
        if f.device_prior_txns > 0:
            reasons.append(f"device profile already seen {f.device_prior_txns} times on this card")
    matched = len(reasons) >= 3 and not (f.amount_novel or f.region_novel or f.channel_novel)
    strength = min(0.85, 0.35 + 0.12 * len(reasons)) if matched else 0.0
    return PatternFinding(
        pattern=Pattern.NONE, name="consistent_with_history", matched=matched, strength=strength,
        why="; ".join(reasons) if reasons else "insufficient history to establish a baseline",
        limitations="A compromised card used carefully within the cardholder's normal envelope "
                    "would also look like this.",
        entity_ids=[f.card_id],
    )


ALL_DETECTORS = (
    detect_card_testing,
    detect_structuring,
    detect_device_ring,
    detect_out_of_region,
    detect_cnp_new_device,
    detect_cnp_fraud,
    detect_account_takeover,
)

EXCULPATORY = (detect_recurring_charge, detect_consistent_with_history)


def run_all(ctx: CaseContext, f: Features) -> tuple[list[PatternFinding], dict[str, Episode]]:
    findings: list[PatternFinding] = []
    episodes: dict[str, Episode] = {}
    for det in ALL_DETECTORS:
        finding, ep = det(ctx, f)
        findings.append(finding)
        if ep is not None:
            episodes[finding.name] = ep
    for det in EXCULPATORY:
        findings.append(det(ctx, f))
    return findings, episodes
