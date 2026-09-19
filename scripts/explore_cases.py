"""Manual-investigation pass over the 20 exam cases.

The dataset README asks you to investigate a case by hand before writing agent
code.  This script does that systematically for all 20 so the detectors in
``fraudgraph.analysis`` are designed against what is actually in the data.
"""
from __future__ import annotations

import pandas as pd

from fraudgraph.config import PATHS
from fraudgraph.graph.local_mirror import get_local_backend


def recurring_check(b, card_id: str, amount: float, ts: str) -> dict:
    """Does the flagged amount match an established recurring charge? (policy R7)"""
    hist = b.card_timeline(card_id, to_ts=ts)["transactions"]
    same = [t for t in hist if abs(float(t["TransactionAmt"]) - amount) < 0.01
            and str(t["ts"]) != str(ts)]
    gaps = []
    if len(same) >= 2:
        d = sorted(pd.Timestamp(t["ts"]) for t in same)
        gaps = [round((d[i + 1] - d[i]).days) for i in range(len(d) - 1)]
    return {"n_same_amount_before": len(same), "day_gaps": gaps[-6:]}


def main() -> None:
    b = get_local_backend()
    cp = pd.read_csv(PATHS.case_pack_csv)
    for _, c in cp.iterrows():
        t = b.txn_detail(int(c.flagged_txn_id))
        card, ts, amt = t["card_id"], t["ts"], float(t["TransactionAmt"])
        prof = b.card_profile(card, ts)
        win = b.card_window(card, ts, hours_before=72, hours_after=72)
        print("=" * 100)
        print(f"{c.case_id}  {c.trigger_type:16s} card={card} txn={t['TransactionID']} "
              f"${amt:.2f} {t['channel']} risk={t['risk_score']} ts={ts}")
        print(f"  region={t['addr1']} country={t['addr2']} product={t['ProductCD']} "
              f"email={t['P_emaildomain']}/{t['R_emaildomain']}")
        if t.get("device_profile"):
            print(f"  device={t['device_profile']}")
            print(f"    id_15={t['id_15']} proxy={t['id_23']} match={t.get('id_34')}")
        # history baseline
        if prof.get("empty"):
            print("  HISTORY: none before this transaction")
        else:
            a = prof["amount"]
            print(f"  HISTORY: {prof['n_txns']} txns since {prof['first_ts']}  "
                  f"amt med=${a['median']:.2f} p95=${a['p95']:.2f} max=${a['max']:.2f}  "
                  f"channels={prof['channels']}")
            print(f"    regions={[ (r['region_id'], r['n']) for r in prof['regions'][:5] ]}")
            print(f"    products={[ (r['ProductCD'], r['n']) for r in prof['product_codes'] ]}")
            print(f"    devices={len(prof['device_profiles'])} distinct, top="
                  f"{[ (r['device_profile'][:44], r['n']) for r in prof['device_profiles'][:3] ]}")
        # novelty tests
        if t.get("addr1") is not None:
            rh = b.region_history_for_card(card, t["addr1"], ts)
            print(f"  REGION TEST: prior txns in region {t['addr1']} = {rh['prior_txns_in_region']} "
                  f"(home={rh['home_region']}, {rh['distinct_prior_regions']} regions seen)")
        if t.get("device_profile"):
            dh = b.device_history_for_card(card, t["device_profile"], ts)
            print(f"  DEVICE TEST: prior txns on this device = {dh['prior_txns_on_device']} "
                  f"({dh['distinct_prior_devices']} devices seen, {dh['prior_online_txns']} online txns)")
            dn = b.device_neighbors(t["device_profile"],
                                    from_ts=str(pd.Timestamp(ts) - pd.Timedelta(days=30)),
                                    to_ts=str(pd.Timestamp(ts) + pd.Timedelta(days=30)))
            print(f"  DEVICE RING: {dn['n_cards']} cards / {dn['n_txns']} txns in +/-30d, "
                  f"proxy={dn['proxy_flags']}, lifetime_cards="
                  f"{(dn['lifetime'] or {}).get('n_cards')}")
        # burst window
        near = [x for x in win["transactions"]]
        print(f"  WINDOW +/-72h: {len(near)} txns")
        for x in near[:14]:
            mark = "  <<< FLAGGED" if x["TransactionID"] == t["TransactionID"] else ""
            print(f"    {x['ts']}  ${float(x['TransactionAmt']):8.2f} {x['channel']:9s} "
                  f"{x['ProductCD']} r={x['risk_score']:.2f} reg={x['addr1']}{mark}")
        if len(near) > 14:
            print(f"    ... {len(near) - 14} more")
        # recurring charge test
        rc = recurring_check(b, card, amt, ts)
        print(f"  RECURRING: same amount seen {rc['n_same_amount_before']}x before, "
              f"day gaps={rc['day_gaps']}")
        # memory
        cc = b.closed_cases_for_customer(t["customer_id"])
        print(f"  PRIOR CASES on customer: {cc['n']} "
              f"{[(x['case_id'], x['outcome'], x['pattern']) for x in cc['cases'][:4]]}")


if __name__ == "__main__":
    main()
