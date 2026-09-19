"""Unit tests for the pattern detectors, on hand-built SYNTHETIC fixtures.

Every fixture in this file is synthetic and constructed by hand.  None of it is
a benchmark case and none of it is drawn from the closed-case history.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fraudgraph.analysis import features as F
from fraudgraph.analysis import patterns as P

BASE_TS = "2016-12-01 12:00:00"


def txn(tid, ts, amt, channel="online", product="C", addr1=None, device=None,
        id_15=None, id_23=None, id_34=None, risk=0.2, card="C99999-K1"):
    return {
        "TransactionID": tid, "card_id": card, "customer_id": "C99999", "ts": ts,
        "TransactionAmt": amt, "ProductCD": product, "channel": channel,
        "risk_score": risk, "addr1": addr1, "addr2": 87.0, "dist1": None,
        "P_emaildomain": "gmail.com", "R_emaildomain": None,
        "device_profile": device, "id_15": id_15, "id_23": id_23, "id_34": id_34,
        "seq_in_card": 0, "gap_prev_s": None, "DeviceType": "desktop",
    }


def ctx_from(rows, flagged, profile=None, **kw):
    c = F.CaseContext(case_id="SYNTH", txn=flagged, card_id=flagged["card_id"],
                      customer_id=flagged["customer_id"])
    c.window = {"transactions": rows}
    c.wide_window = {"transactions": rows}
    c.profile = profile or {
        "n_txns": 200, "empty": False,
        "first_ts": "2016-07-01 00:00:00", "last_ts": "2016-11-30 00:00:00",
        "amount": {"min": 5, "p25": 20, "median": 40, "p75": 70, "p95": 120,
                   "max": 200, "mean": 50, "std": 30, "total": 10000},
        "channels": {"online": 180, "in_person": 20},
        "product_codes": [{"ProductCD": "C", "n": 200}],
        "regions": [{"region_id": 100.0, "n": 150}],
        "device_profiles": [{"device_profile": "known-device", "n": 100}],
        "purchaser_email_domains": [{"domain": "gmail.com", "n": 200}],
        "recipient_email_domains": [],
        "mean_risk_score": 0.2,
    }
    for k, v in kw.items():
        setattr(c, k, v)
    return c


# ------------------------------------------------------------ card testing
def test_card_testing_fires_on_small_run_then_purchase():
    """SYNTHETIC: three sub-$3 online authorisations then a $259 purchase."""
    rows = [
        txn(1, "2016-12-01 09:12:00", 1.10),
        txn(2, "2016-12-01 09:31:00", 2.40),
        txn(3, "2016-12-01 09:52:00", 0.95),
        txn(4, "2016-12-01 10:31:00", 259.98),
    ]
    ctx = ctx_from(rows, rows[3])
    f = F.compute(ctx)
    finding, ep = P.detect_card_testing(ctx, f)
    assert finding.matched and finding.strength >= 0.8
    assert ep is not None and len(ep.txn_ids) == 4
    assert ep.exposure == pytest.approx(264.43, abs=0.01)


def test_card_testing_does_not_fire_on_two_small_auths():
    rows = [txn(1, "2016-12-01 09:12:00", 1.10), txn(2, "2016-12-01 09:31:00", 2.40)]
    ctx = ctx_from(rows, rows[1])
    finding, ep = P.detect_card_testing(ctx, F.compute(ctx))
    assert not finding.matched and ep is None


def test_card_testing_ignores_small_auths_spread_over_days():
    rows = [
        txn(1, "2016-12-01 09:00:00", 1.10),
        txn(2, "2016-12-02 09:00:00", 2.40),
        txn(3, "2016-12-03 09:00:00", 0.95),
    ]
    ctx = ctx_from(rows, rows[2])
    finding, _ = P.detect_card_testing(ctx, F.compute(ctx))
    assert not finding.matched


# --------------------------------------------------------- structuring
def test_structuring_fires_on_four_just_under_500():
    """SYNTHETIC: the shape described in closed cases CC-3748 and siblings."""
    rows = [
        txn(1, "2016-12-01 20:00:00", 478.95),
        txn(2, "2016-12-01 20:10:00", 456.96),
        txn(3, "2016-12-01 20:24:00", 488.04),
        txn(4, "2016-12-01 20:30:00", 482.12),
    ]
    ctx = ctx_from(rows, rows[3])
    f = F.compute(ctx)
    finding, ep = P.detect_structuring(ctx, f)
    assert finding.matched and finding.strength >= 0.7
    assert len(ep.txn_ids) == 4
    assert ep.exposure == pytest.approx(1906.07, abs=0.01)
    assert "threshold" in finding.description.lower()


def test_structuring_ignores_amounts_well_below_the_threshold():
    rows = [txn(i, f"2016-12-01 20:{i:02d}:00", 120.0) for i in range(1, 5)]
    ctx = ctx_from(rows, rows[3])
    finding, _ = P.detect_structuring(ctx, F.compute(ctx))
    assert not finding.matched


def test_structuring_needs_three_in_the_window():
    rows = [
        txn(1, "2016-12-01 20:00:00", 478.95),
        txn(2, "2016-12-01 23:30:00", 456.96),
    ]
    ctx = ctx_from(rows, rows[1])
    finding, _ = P.detect_structuring(ctx, F.compute(ctx))
    assert not finding.matched


# ----------------------------------------------------------- device ring
def _ring_ctx(n_cards, new_frac=1.0, proxy="IP_PROXY:ANONYMOUS", marked="New"):
    dev = "SM-X Build/Y | Android 7.0 | chrome 62.0 for android | 1920x1080"
    flagged = txn(9, BASE_TS, 74.96, device=dev, id_15=marked, id_23=proxy)
    ring = {
        "n_cards": n_cards, "n_txns": n_cards * 2, "total_amount": 1000.0,
        "proxy_flags": [proxy] if proxy else [],
        "cards": [{"card_id": f"C{i:05d}-K1", "n_txns": 2, "amount": 50.0}
                  for i in range(n_cards)],
        "lifetime": {"n_cards": n_cards, "n_txns": 100,
                     "n_new_for_account": int(100 * new_frac),
                     "proxy_flags": proxy or ""},
    }
    return ctx_from([flagged], flagged, device_ring=ring), flagged


def test_device_ring_fires_on_many_cards_all_new_and_proxied():
    ctx, _ = _ring_ctx(28)
    f = F.compute(ctx)
    finding, ep = P.detect_device_ring(ctx, f)
    assert finding.matched and finding.strength >= 0.8
    assert finding.description and "ring" in finding.description.lower()
    assert len(finding.entity_ids) > 5


def test_device_ring_does_not_fire_on_a_common_fingerprint():
    """Many cards but the device is Found for the account and unproxied."""
    ctx, _ = _ring_ctx(40, new_frac=0.1, proxy=None, marked="Found")
    finding, _ = P.detect_device_ring(ctx, F.compute(ctx))
    assert not finding.matched


def test_device_ring_needs_enough_cards():
    ctx, _ = _ring_ctx(3)
    finding, _ = P.detect_device_ring(ctx, F.compute(ctx))
    assert not finding.matched


# -------------------------------------------------------- out of region
def test_out_of_region_stronger_when_home_activity_continues():
    """SYNTHETIC: a clone -- purchases in a new region while home spending continues."""
    rows = [
        txn(1, "2016-12-01 09:00:00", 40.0, channel="in_person", product="W", addr1=100.0),
        txn(2, "2016-12-01 12:00:00", 90.0, channel="in_person", product="W", addr1=777.0),
        txn(3, "2016-12-01 18:00:00", 35.0, channel="in_person", product="W", addr1=100.0),
    ]
    ctx = ctx_from(rows, rows[1], region_test={
        "region_id": 777.0, "prior_txns_in_region": 0, "prior_txns_total": 200,
        "distinct_prior_regions": 4, "home_region": 100.0, "first_seen_in_region": None,
    })
    f = F.compute(ctx)
    finding, ep = P.detect_out_of_region(ctx, f)
    assert finding.matched
    assert finding.strength >= 0.7
    assert f.home_activity_continues is True


def test_out_of_region_weaker_for_a_multi_day_trip():
    """SYNTHETIC: a trip -- several days in one new region, no home activity."""
    rows = [
        txn(1, "2016-11-29 12:00:00", 60.0, channel="in_person", product="W", addr1=777.0),
        txn(2, "2016-11-30 13:00:00", 70.0, channel="in_person", product="W", addr1=777.0),
        txn(3, "2016-12-01 12:00:00", 90.0, channel="in_person", product="W", addr1=777.0),
        txn(4, "2016-12-02 14:00:00", 55.0, channel="in_person", product="W", addr1=777.0),
    ]
    ctx = ctx_from(rows, rows[2], region_test={
        "region_id": 777.0, "prior_txns_in_region": 0, "prior_txns_total": 200,
        "distinct_prior_regions": 4, "home_region": 100.0, "first_seen_in_region": None,
    })
    f = F.compute(ctx)
    trip, _ = P.detect_out_of_region(ctx, f)
    assert f.home_activity_continues is False
    assert f.region_days_span >= 2.0
    assert trip.strength < 0.7


def test_out_of_region_ignores_a_region_with_history():
    rows = [txn(1, BASE_TS, 90.0, channel="in_person", product="W", addr1=777.0)]
    ctx = ctx_from(rows, rows[0], region_test={
        "region_id": 777.0, "prior_txns_in_region": 12, "prior_txns_total": 200,
        "distinct_prior_regions": 9, "home_region": 100.0, "first_seen_in_region": "2016-08-01",
    })
    finding, _ = P.detect_out_of_region(ctx, F.compute(ctx))
    assert not finding.matched


# -------------------------------------------------------- new device CNP
def test_cnp_new_device_requires_corroboration():
    """A new device on a normal-looking purchase is not enough on its own."""
    flagged = txn(1, BASE_TS, 45.0, device="brand-new", id_15="New")
    ctx = ctx_from([flagged], flagged, device_test={
        "prior_txns_on_device": 0, "prior_online_txns": 180, "distinct_prior_devices": 6,
    })
    finding, _ = P.detect_cnp_new_device(ctx, F.compute(ctx))
    assert not finding.matched, "new device alone must not match"


def test_cnp_new_device_fires_with_an_unprecedented_amount():
    flagged = txn(1, BASE_TS, 900.0, device="brand-new", id_15="New")
    ctx = ctx_from([flagged], flagged, device_test={
        "prior_txns_on_device": 0, "prior_online_txns": 180, "distinct_prior_devices": 6,
    })
    f = F.compute(ctx)
    assert f.amount_novel
    finding, _ = P.detect_cnp_new_device(ctx, f)
    assert finding.matched


# -------------------------------------------------------------- recurring
def test_recurring_charge_detected_on_a_monthly_subscription():
    """SYNTHETIC: the policy R7 shape."""
    rows = [
        txn(1, "2016-09-02 10:00:00", 49.00, addr1=100.0),
        txn(2, "2016-10-02 10:00:00", 49.00, addr1=100.0),
        txn(3, "2016-11-02 10:00:00", 49.00, addr1=100.0),
        txn(4, "2016-12-02 10:00:00", 49.00, addr1=100.0),
    ]
    ctx = ctx_from(rows, rows[3])
    f = F.compute(ctx)
    assert f.same_amount_before == 3
    assert f.looks_recurring is True
    finding = P.detect_recurring_charge(ctx, f)
    assert finding.matched


def test_recurring_charge_not_detected_on_irregular_repeats():
    rows = [
        txn(1, "2016-08-13 10:00:00", 49.00, addr1=100.0),
        txn(2, "2016-09-02 10:00:00", 49.00, addr1=200.0),
        txn(3, "2016-12-10 10:00:00", 49.00, addr1=300.0),
    ]
    ctx = ctx_from(rows, rows[2])
    f = F.compute(ctx)
    assert f.looks_recurring is False


# ----------------------------------------------------------- consistency
def test_consistent_with_history_fires_on_an_ordinary_transaction():
    flagged = txn(1, BASE_TS, 38.0, addr1=100.0, device="known-device")
    ctx = ctx_from([flagged], flagged,
                   region_test={"region_id": 100.0, "prior_txns_in_region": 150,
                                "prior_txns_total": 200, "distinct_prior_regions": 3,
                                "home_region": 100.0, "first_seen_in_region": "2016-07-01"},
                   device_test={"prior_txns_on_device": 100, "prior_online_txns": 180,
                                "distinct_prior_devices": 3})
    f = F.compute(ctx)
    finding = P.detect_consistent_with_history(ctx, f)
    assert finding.matched


def test_run_all_returns_every_detector():
    flagged = txn(1, BASE_TS, 50.0)
    ctx = ctx_from([flagged], flagged)
    findings, episodes = P.run_all(ctx, F.compute(ctx))
    names = {f.name for f in findings}
    assert names == {
        "card_testing", "sub_threshold_structuring", "shared_device_ring",
        "out_of_region_use", "cnp_new_device", "cnp_fraud", "account_takeover",
        "recurring_charge", "consistent_with_history",
    }
    for f in findings:
        assert 0.0 <= f.strength <= 1.0
        assert f.why
