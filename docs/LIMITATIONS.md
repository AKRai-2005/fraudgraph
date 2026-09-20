# Limitations

Written so that nothing in this project has to be taken on trust.

## Things that are simulated, and say so

| Thing | Reality |
|---|---|
| Customer replies | Not provided by the challenge. Simulated under the published rule in `agent/evidence_requests.py`, recorded in `evidence_requests[].assumed_response` and labelled in the SAR narrative. |
| Analyst replies | Same. No analyst channel is connected. |
| `BLOCK_CARD`, `DECLINE_TRANSACTION`, `FILE_REPORT`, `BLOCK_ALL_CARDS` | Never executed against anything real. `policy/actions.py` writes an append-only audit log and refuses to act without an approver. The dashboard labels every execution "simulated". |
| Step-up authentication | Recommended, never performed. |

The agent may execute only `auto` actions, and even those hit the mock service.

## Things that are measured, not asserted

* Answer-file format compliance: `python -m fraudgraph.benchmark.validate`.
* Risk-model discrimination: out-of-fold cross-validation in
  `build/risk_model.json`, surfaced in the console's model card.
* Detector firing rates on confirmed fraud vs legitimate activity: same file.
* Ingestion integrity: `build/ingest_quality_report.json`.

## Things that are not measured

**Accuracy against the challenge's answer key.** We do not have it. Nothing in
this repository estimates a score against it, and no accuracy figure for the 20
exam cases appears anywhere — that would be fabrication. The measured numbers
above are against the *closed cases*, which is a different and easier problem.

## Known weaknesses in the modelling

1. **The closed cases are not a sample of alerts.** They are a sample of
   investigations the bank chose to open: all 900 cleared cases are
   high-scoring model alerts that turned out to be travel, a new phone or a
   large intended purchase, and the 4,665 confirmed frauds are overwhelmingly
   customer reports. Fitted naively, coefficients invert. The mitigations — a
   base-rate negative class, sign constraints and bounded priors — are described
   in `RISK_MODEL.md`. They reduce the problem; they do not remove it.

2. **The base-rate negative class carries label noise.** Transactions no closed
   case ever touched are treated as legitimate. Some will be undetected fraud.
   This biases coefficients toward zero, which is conservative but real.

3. **`customer_dispute` is not fitted.** Every disputed case in the history was
   confirmed fraud, so the coefficient would be unbounded. It is fixed by hand
   at +3.2 log-odds and can be overturned by the recurring-charge detector,
   which is what policy R7 anticipates. If the exam contains disputes that are
   in fact legitimate for a reason *other* than a recurring charge, this agent
   will call them fraud.

4. **Region novelty is weak in this data.** Cardholders here transact across
   dozens of billing regions routinely, so "a region this card has never used"
   fires on only about 2% of cases and fires almost equally on fraud and
   cleared alerts. The trip-vs-clone discriminator is therefore carried by a
   bounded prior taken from the dataset README rather than by a measurement.
   This is the one place a prior overrides the data, and it is recorded in
   `analysis/calibrate.py` next to the bound.

5. **No monthly recurring charge exists in the 20 exam cases.** Policy R7 is
   implemented and unit-tested against a synthetic subscription, but a scan of
   all 20 cases found no amount repeating at a 25–35 day cadence at the same
   merchant proxy. R7 therefore does not fire on this pack. That is a finding
   about the pack, not a gap in the implementation.

6. **Merchant identity is a proxy.** The dataset has no merchant column, so
   "same merchant" in R7 is approximated by `(ProductCD, billing region,
   purchaser email domain)`. This is stated wherever it is used.

7. **`V1..V339`, `C1..C14`, `D1..D15` are unnamed.** They are available as
   signals but the current detectors do not lean on them, because a claim like
   "V127 was elevated" is not evidence an analyst can act on. This leaves
   signal on the table.

## The LLM's free tier is the binding constraint

Gemini's free tier allows **20 `generateContent` requests per day per model**
(`GenerateRequestsPerDayPerProjectPerModel-FreeTier`). A 20-case run needs one
narrative call per case plus one per SAR, so it sits right at that ceiling.

Three things follow, all visible rather than hidden:

* The default model is `gemini-flash-lite-latest`. `gemini-2.0-flash` and
  `gemini-2.5-flash` are both refused to new API keys ("no longer available to
  new users").
* The LLM planner is consulted only when the deterministic baseline turned up
  no lead — no device ring, fewer than three transactions in the window, no
  prior case on the card or device. On the 20 exam cases the baseline always
  had a lead, so the planner spent no requests and added no queries. That is
  reported, not assumed.
* When a call is refused the narrator falls back to the deterministic template
  and the run **says so**: `benchmark_summary.json` carries
  `llm_stats.calls_rate_limited`, and the runner prints a warning. An earlier
  run silently produced template narratives for most cases while looking
  successful; that is what the counter exists to prevent.

The verdicts, probabilities, patterns, actions, routes and SAR decisions never
come from the LLM, so a quota exhaustion changes only the prose.

## Engineering limitations

* The local mirror holds the transaction index in memory (~590k rows). It is a
  development and testing backend, not a production one.
* Graph loading to a cloud workspace uploads ~90 MB of CSV over REST and takes
  a while; it is idempotent and resumable but not fast.
* The dashboard polls rather than streaming; a long investigation shows a
  spinner rather than incremental steps.
* The force-directed graph layout is computed in the browser with a simple
  O(n²) relaxation, capped at 160 nodes. It is readable, not a full graph
  explorer.

## Rules we are bound by, and followed

* The public IEEE-CIS / Kaggle files are **not** used, in any form, at any
  point. The organisers disguised `TransactionID`, `card1`, `TransactionDT` and
  `TransactionAmt`, and no attempt is made to invert that.
* No benchmark answer is hardcoded. The detectors key on behaviour; grepping
  the source for `HHG-` finds the case-pack loader and tests, not verdicts.
* Every id emitted in an answer file is checked to exist in the shipped dataset.
