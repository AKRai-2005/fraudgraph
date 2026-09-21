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

#: Detector name -> the FraudPattern vertex id loaded into the graph by
#: fraudgraph.ingest.tg_export. The two differ because the detectors are named
#: for what they look for while the graph vertices are named for the typology
#: the challenge documents. tests/test_tg_loading.py asserts the mapping is
#: total and that every target exists in the exported catalogue.
DETECTOR_TO_PATTERN_ID = {
    "card_testing": "card_testing",
    "cnp_fraud": "card_not_present_fraud",
    "cnp_new_device": "card_not_present_new_device",
    "out_of_region_use": "out_of_region_use",
    "account_takeover": "account_takeover",
    "shared_device_ring": "shared_device_ring",
    "sub_threshold_structuring": "sub_threshold_structuring",
}


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


#: card_testing, second definition. See detect_card_testing for how these were
#: chosen; they were fixed by a rule stated before recall was looked at.
TEST_PROBE_MAX = 2.00       # a "test" authorisation: online, under $2
TEST_MIN_PROBES = 2
TEST_USE_MIN = 25.0         # a "use": an online purchase of at least $25 ...
TEST_USE_WITHIN_H = 48      # ... within 48 hours after some probe
TEST_WINDOW_DAYS = 14


def detect_card_testing(ctx: CaseContext, f: Features) -> tuple[PatternFinding, Episode | None]:
    """Pattern 1 / policy R5: probing a stolen card number with tiny online
    authorisations before using it.

    **The first definition caught none of the 16 card-testing cases.** It took
    the README literally -- three or more small authorisations inside one
    hour, *then* a larger purchase -- and the end-to-end backtest measured it
    at 0 of 16. Reading the transactions showed why, three ways over:

    * probes and purchases **interleave** (test, buy, test, buy), where the
      old rule required every probe to precede the purchase;
    * probes are **sparse** -- one or two at a time, hours or days apart --
      so three inside an hour almost never happens;
    * runs span up to eleven days, and the rule looked at +/-72h.

    What does separate them is the amount. Sub-$2 online authorisations are
    nearly absent from ordinary traffic: 162 of 590,742 transactions, on 18 of
    14,318 cards.

    The thresholds were chosen by a rule fixed *before* looking at recall:
    the variant closest to the README's "often under $5" whose firing rate on
    **cleared** cases is at most 1%. Selection used the negative class only.

    ======  ======  =============  =============  ============
    probe   probes  cleared FPR    other fraud    card testing
    ======  ======  =============  =============  ============
    < $5    >= 1    2.44%  fails   7.23%          15/16
    < $5    >= 2    1.33%  fails   3.79%          13/16
    < $2    >= 1    0.11%          0.52%          7/16
    < $2    >= 2    0.11%          0.26%          5/16   <- chosen
    ======  ======  =============  =============  ============

    $5 -- the README's own figure -- fails: at that size a "probe" is an
    ordinary small purchase. At $2 one probe and two gave identical cleared
    rates (1 of 900); the tie went to fewer misattributions on other fraud,
    which is also the lower-recall option, so it was not chosen to flatter.

    The honest limit: card testing done with $2-$5 probes is not separable
    from ordinary small online purchases by amount and timing alone. Catching
    it would cost false fraud calls on 2.4% of legitimate high-score alerts,
    which this agent does not accept.
    """
    rows = _wide(ctx, TEST_WINDOW_DAYS)
    online = sorted(
        (r for r in rows if r.get("channel") == "online"),
        key=lambda r: pd.Timestamp(r["ts"]),
    )
    probes = [r for r in online if _f(r["TransactionAmt"]) < TEST_PROBE_MAX]
    uses: list[dict] = []
    for r in online:
        if _f(r["TransactionAmt"]) < TEST_USE_MIN:
            continue
        r_ts = pd.Timestamp(r["ts"])
        # a use is any sizeable purchase that follows *some* probe -- probes
        # and purchases interleave, so no ordering of the whole run is assumed
        if any(0 < (r_ts - pd.Timestamp(p["ts"])).total_seconds() <= TEST_USE_WITHIN_H * 3600
               for p in probes):
            uses.append(r)
    matched = len(probes) >= TEST_MIN_PROBES and bool(uses)
    strength = 0.0
    if matched:
        strength = min(0.95, 0.80 + 0.04 * (len(probes) - TEST_MIN_PROBES))
    if matched:
        why = (f"{len(probes)} online authorisation(s) under ${TEST_PROBE_MAX:.2f} within "
               f"{TEST_WINDOW_DAYS} days, each small enough to be a test of the card number, "
               f"with {len(uses)} purchase(s) of ${TEST_USE_MIN:.0f} or more following within "
               f"{TEST_USE_WITHIN_H}h")
    else:
        why = (f"{len(probes)} online authorisation(s) under ${TEST_PROBE_MAX:.2f} in "
               f"{TEST_WINDOW_DAYS} days"
               + ("" if len(probes) < TEST_MIN_PROBES
                  else f", but no purchase of ${TEST_USE_MIN:.0f}+ followed within "
                       f"{TEST_USE_WITHIN_H}h"))
    ep = _episode(probes + uses) if matched else None
    return (
        PatternFinding(
            pattern=Pattern.CARD_TESTING, name="card_testing", matched=matched,
            strength=strength, why=why,
            limitations=(f"Probes of ${TEST_PROBE_MAX:.0f}-$5 are not separable from ordinary "
                         "small online purchases, so card testing done with them is missed "
                         "by design; catching it would flag 2.4% of legitimate high-score "
                         "alerts."),
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
    # The episode is the activity that looks taken over, not every transaction
    # the cardholder made that week: online transactions from a device new to
    # the account, plus the flagged one.  The closed history's account-takeover
    # cases have a median of 2 transactions, so a 30-transaction episode would
    # be the window talking, not the fraud.
    flagged_id = str(ctx.txn.get("TransactionID"))
    suspect = [
        r for r in near
        if str(r["TransactionID"]) == flagged_id
        or (r.get("channel") == "online"
            and (r.get("id_15") == "New" or r.get("device_profile") == f.device_profile))
    ]
    ep = _episode(suspect) if matched and suspect else None
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
