# Dataset notes — verified findings

Everything here was derived from the shipped files, not assumed. Reproduce with
`python scripts/probe_card_key.py`.

## File inventory (verified row counts)

| File | Rows | Notes |
|---|---:|---|
| `transactions.csv` | 590,742 | 397 columns = 393 original Vesta + `customer_id`, `ts`, `channel`, `risk_score` |
| `identity.csv` | 144,432 | 41 columns, joins on `TransactionID`, online transactions only |
| `closed_cases_history.csv` | 5,565 | 4,665 `confirmed_fraud`, 900 `cleared` |
| `case_pack.csv` | 20 | the exam |

Distinct customers: **13,553**. `card1` is 1:1 with `customer_id`.

## Card identity — recovered, not assumed

The case pack and the closed cases reference cards as `C01234-K1`, but
`transactions.csv` only carries `customer_id`. The mapping was recovered by
testing candidate keys against the 14,955 known transaction→card links in
`closed_cases_history.csv`:

> **A Card is the tuple `(customer_id, card4, card6)`** — the customer, the card
> network (`visa` / `mastercard` / `discover` / `american express`) and the card
> type (`credit` / `debit`). Missing values participate in the key as the literal
> `NA`.
>
> **The `K` index is the rank of the `"card4|card6"` string in ascending
> lexicographic order within the customer.** So `discover|credit` → `K1`,
> `discover|debit` → `K2`; a card with both fields missing (`NA|NA`) sorts first.

Verification on the shipped data:

| Check | Result |
|---|---|
| every `card_id` maps to exactly one `(card4, card6)` key | **1.0000** |
| per customer, #distinct `card_id` == #distinct keys | **1.0000** |
| `K` == rank by key string | **1.0000** (rank by first-seen timestamp: 0.340; by volume: 0.341) |
| closed-case `card_id`s reproduced | **1913 / 1913** |
| case-pack `card_id`s reproduced | **20 / 20** |

Cards per customer across the whole dataset: 12,793 customers have 1 card,
755 have 2, 5 have 3.

This rule is implemented once, in `fraudgraph.ingest.entities.card_id_for`.

## Channel

`channel` is `in_person` for `ProductCD == 'W'` (no identity record) and
`online` otherwise. Confirmed against the identity join.

## Device profile

Per the README's suggested schema, a `DeviceProfile` is the tuple
`DeviceInfo | id_30 (OS) | id_31 (browser) | id_33 (screen)` from `identity.csv`.
`id_23` carries the proxy flag (`IP_PROXY:TRANSPARENT` 3,492, `IP_PROXY:ANONYMOUS`
1,185, `IP_PROXY:HIDDEN` 611, otherwise null) and `id_15` marks the device as
`New` / `Found` / `Unknown` for the account.

## Closed cases — what the history actually contains

| pattern | n | report_filed=Yes | median exposure | actions |
|---|---:|---:|---:|---|
| `card_not_present_fraud` | 1,404 | 14 | $100.02 | `CREATE_CASE\|BLOCK_CARD` |
| `account_takeover` | 1,205 | 187 | $252.40 | `CREATE_CASE\|BLOCK_CARD` |
| `card_not_present_new_device` | 1,076 | 56 | $178.43 | `CREATE_CASE\|BLOCK_CARD` |
| `out_of_region_use` | 955 | 124 | $234.93 | `CREATE_CASE\|BLOCK_CARD` |
| `none` (cleared) | 900 | 0 | $0.00 | `VERIFY_WITH_CUSTOMER\|CLOSE_NO_FRAUD` |
| `card_testing` | 16 | 7 | $677.72 | `CREATE_CASE\|BLOCK_CARD` |
| `undocumented` | 9 | 9 | $1,871.13 | `CREATE_CASE\|BLOCK_CARD\|FILE_REPORT` |

### The two undocumented typologies (read from the analyst notes)

The nine `undocumented` cases are **two distinct typologies**, not one:

1. **Shared-device ring (4 cases: CC-2649, CC-2971, CC-2985, CC-3035).**
   Online purchases from a *Samsung SM-G935F on Chrome for Android behind an
   anonymous proxy*, a device never seen on the account. Each case lists 23
   `connected_card_ids` — one ring spanning 24 cards. 2–3 transactions each,
   $108–$390 exposure.
2. **Sub-threshold structuring (5 cases: CC-3748, CC-3841, CC-3907, CC-4086,
   CC-4124).** *Four online purchases within forty minutes, each just under
   $500*, total ~$1,870–$1,923. The notes state the amounts appear chosen to
   stay under a $500 authorization threshold. No connected cards.

Both are detected deterministically in `fraudgraph.analysis.patterns`; neither is
hardcoded to a case id.

## Class-balance caveat (important for calibration)

The closed cases are **84% confirmed fraud**, because they are the alerts the
bank actually investigated July–October. The README states that for the exam
pack *"half the cases are legitimate"*. Any probability fitted on the history
therefore carries a prior shift and must be re-based before it is reported as
`fraud_probability`. See `docs/RISK_MODEL.md`.

## Rules honoured

- The public IEEE-CIS / Kaggle files are **not** used. `TransactionID`, `card1`,
  `TransactionDT` and `TransactionAmt` were disguised by the organisers and no
  attempt is made to invert that.
- Every ID emitted in an answer file is validated to exist in this dataset
  (`fraudgraph.benchmark.validate`).
