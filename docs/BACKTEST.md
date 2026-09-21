# End-to-end backtest

Everything else in this project measures a *part*: detector firing rates, the
risk model's cross-validated discrimination, answer-format compliance. None of
it answers the question the project actually stakes its claim on — **when the
agent disagrees with the bank's score, is it right?**

The 20 exam cases cannot answer it; we do not have their key, and no accuracy
figure for them appears anywhere in this repository. The 5,565 closed
investigations *can*: they carry real outcomes. So the agent is replayed over
them as if they were live alerts, and its verdicts are scored.

```bash
python -m fraudgraph.analysis.backtest --n 300 --all-modes
python -m fraudgraph.analysis.backtest --trigger neutral \
       --patterns undocumented,card_testing        # the full rare population
```

Results land in `build/backtest.json` and are shown in the console's
**Model & policy** tab.

---

## Three things that would have made this a lie

### 1. Time

A historical alert must not see investigations that closed after it, least of
all itself. Every case-memory query receives `as_of` = the case's own
`opened_at`; the case id is excluded by name; and the replay **verifies
afterwards** that nothing from the future was retrieved. Leakage is a
failure, not a warning — the runner exits non-zero.

This required threading `as_of` through the investigation path, which had
never carried it. Live, it is empty and nothing changes: the history ends
2016-11-06 and the exam pack starts 2016-11-22, so everything on disk is
genuinely in the past. In a backtest it is load-bearing.

**The first version of this backtest leaked anyway.** It time-boxed case
memory but not *transactions*: the agent looks 72 hours past the flagged
transaction for a burst, 30 days past it for an episode, and 30 days either
side for a device ring. A replayed alert could therefore count cards
compromised after its own investigation had opened. Every forward window is
now clamped at `as_of`, and the replay reads the bounds actually sent from the
tool ledger rather than trusting the clamp — `_window_leakage` in
`analysis/backtest.py`.

**It inflated the first report.** Isolated by re-running the old detector with
only the clamp added: the general-sample AUC fell from **0.718 to 0.691**. The
0.718 published in the first version of this document was measured with the
leak and is withdrawn. What did *not* move is the headline — 9 of 9
relational-fraud cases and 7 false fraud calls on 300 — so the claim the
project rests on survived; a secondary number did not.

All runs below report **0 leakage violations** of either kind.

#### Why calibration is not clamped the same way

The detector weights are measured by `scripts/measure_detectors.py`, which
still looks forward. That is deliberate, and it is not the same leak. The
weights exist to describe how informative a detector is *under the view the
deployed agent actually has* — and on the exam the agent looks ±72h and
+30 days, because an investigation after the fact legitimately scopes the
episode from what happened next. Clamping calibration at `opened_at` would
calibrate the detectors for a view the deployed agent never takes. The
backtest asks a stricter question — was the verdict right *at the moment the
case opened*? — so it alone is clamped.

### 2. The trigger prior almost *is* the label

In this history, all 900 cleared cases began as a model score, and
investigated customer disputes were confirmed fraud. The agent's trigger prior
was fitted on exactly that correlation. Replaying cases under their real
trigger type therefore scores the prior, not the investigation.

So the backtest runs three ways:

| mode | every case arrives as | prior | what it measures |
|---|---|---|---|
| **`neutral`** | `analyst_request` | **0.00 log-odds** (p=0.50) | **graph evidence alone** |
| `score` | `risk_score` | −1.60 (p=0.17) | the exam's operating point |
| `actual` | its real trigger | varies | the full system — *and the prior* |

**`neutral` is the headline.** Nothing is on either scale, so anything that
separates the classes came from the graph.

### 3. The sample is not a sample of alerts

It is a sample of investigations a bank chose to open. Every cleared case is a
**high-scoring** alert that turned out to be travel or a new phone — the
hardest negatives in the dataset, not average traffic. Specificity measured
here is a *lower bound*. Precision depends on a base rate this data does not
contain.

---

## Results

600 cases (300 per class), seed 20260920, local mirror, narrator off, every
window clamped at the moment the case opened. `uncertain` is counted as "no
fraud action", which is what it means operationally.

### The headline: graph evidence alone

Run over the **entire population** of the two relational typologies — all 9
`undocumented` and all 16 `card_testing` cases in the dataset — against 300
cleared alerts:

| true typology | caught | recall | mean p |
|---|---|---|---|
| **undocumented** (device ring, structuring) | **9 / 9** | **100%** | 0.94 |
| card_testing | 5 / 16 | 31% | 0.71 |

against **7 false fraud calls on 300** of the hardest negatives — 97.7%
specificity. AUC 0.898.

The agent finds **every instance in the dataset** of the two typologies the
challenge does not document, from graph structure alone, without a prior and
without seeing its own case.

### The same run over a random fraud sample

| | recall on fraud | specificity | false fraud calls | AUC |
|---|---|---|---|---|
| `neutral` | 2.67% (8/300) | 98.3% | 5 | 0.691 |
| `score` | 0.33% (1/300) | **100%** | **0** | 0.654 |
| `actual` | 99.3% (298/300) | 100% | 0 | 1.000 |

Read `actual` with the caveat above: it is mostly the trigger prior, and is
reported for completeness rather than as evidence.

The honest reading of `neutral` is in the typology breakdown:

| true typology | caught | recall |
|---|---|---|
| undocumented | 1/1 | 100% |
| card_not_present_fraud | 4/101 | 4.0% |
| card_not_present_new_device | 3/60 | 5.0% |
| account_takeover | 0/73 | 0% |
| out_of_region_use | 0/64 | 0% |

---

## card_testing: found broken, rebuilt, measured again

The first version of this backtest measured the `card_testing` detector at
**0 of 16**. Its definition took the README literally — three or more small
authorisations inside one hour, *then* a larger purchase — and the real runs
break all three assumptions: probes and purchases **interleave** (test, buy,
test, buy), probes are **sparse** (one or two at a time, days apart), and runs
last up to eleven days where the rule looked at ±72 hours.

What does separate these runs is the amount. Online authorisations under $2
are close to absent from ordinary traffic.

**Thresholds were fixed by a rule stated before recall was looked at:** the
variant closest to the README's "often under $5" whose firing rate on cleared
alerts is at most 1%. Selection used the negative class only.

| probe | probes | cleared | other fraud | card testing | |
|---|---|---|---|---|---|
| < $5 | ≥ 1 | 2.44% | 7.23% | 15/16 | fails the rule |
| < $5 | ≥ 2 | 1.33% | 3.79% | 13/16 | fails the rule |
| < $2 | ≥ 1 | 0.11% | 0.52% | 7/16 | |
| **< $2** | **≥ 2** | **0.11%** | **0.26%** | **5/16** | **chosen** |

The README's own $5 fails: at that size a probe is an ordinary purchase. At $2
one probe and two tied on cleared alerts; the tie went to fewer misattributions
on other fraud — also the lower-recall option, so it was not picked to flatter.

**Result in the replay: 0/16 → 5/16, with no added false positives** on the 300
hard negatives. Three of the sixteen cases were read by hand while designing
it; on the **13 never inspected, it catches 3 — 23%**. That is the
out-of-sample figure, and the one to quote.

### Its weight still measures negative, and why that is not believed

Measured the standard way, the new detector fires on 31 of 4,665 confirmed
frauds and **39 of 3,400 legitimate cases** — a likelihood ratio of 0.58, below
1. Taken at face value that says the detector argues for innocence.

It was traced on the agent's own code path rather than accepted:

* all 39 legitimate firings fall on **5 cards**, one of them accounting for 22;
* **all 5 have a confirmed-fraud case**, and 3 a confirmed card-testing case;
* the detector fires on **no card without fraud history** — zero;
* 38 of the 39 are from the "unalerted" base-rate sample: transactions no
  closed case happened to include, on cards that were being tested.

The negative class is contaminated exactly where this detector looks. So the
measured ratio is not believed, and the weight stays at its documented floor
of +1.20 — now with the trace recorded as its basis in
`analysis/build_model.py`, rather than "too few cases to estimate from".

The same label noise sits under every detector's measurement. It shows up here
because card testing is so rare in clean traffic that a handful of mislabelled
transactions dominate the count.

### What it still misses, by design

Card testing done with $2–$5 probes is not separable from ordinary small online
purchases by amount and timing. Catching it costs false fraud calls on 2.4% of
legitimate high-score alerts, which this agent's design does not accept. 11 of
16 remain uncaught.

---

## What this establishes, and what it does not

**Established.**

* The agent's edge is real and it is *specific*: relational fraud, found by
  traversal. 9 of 9, with no prior helping it.
* Its caution is real. Under the exam's operating point it produced **zero**
  false fraud verdicts on 300 high-scoring legitimate alerts. The project's
  central claim — that a score is a reason to look, not a verdict — is not
  just rhetoric in the write-up; the agent behaves that way under measurement.
* Nothing leaks, of either kind. 0 violations across every replayed
  investigation, with case memory and transaction windows both checked from
  the tool ledger rather than trusted.

**Not established, and worth saying plainly.**

* **Most fraud in this history is invisible to the graph.** Single-transaction
  customer disputes have no relational structure to find, and they are the
  bulk of the confirmed frauds. Under a neutral prior the agent reaches
  "uncertain" on most of them — it does not pretend, but nor does it help.
* **card_testing recall is 23% out of sample.** Better than zero, and bought
  without false positives, but most card testing in this history uses probes
  too large to tell from ordinary purchases.
* **Nothing here transfers to the 20 exam cases**, whose outcomes are unknown.
* The bank score's AUC on this sample (0.056) is an artefact of the same
  selection — every cleared case is a high score — and is not a statement
  about the bank's model on real traffic. No comparison with it is meaningful.
* For confirmed frauds the replay starts at the first *fraudulent*
  transaction, a more informative starting point than a real alert always
  gives. Cleared cases carry exactly one transaction each, so burst-shaped
  detectors have less to find on the negative class than on real traffic.

## Reproducing

Deterministic given the seed. `tests/test_backtest.py` covers the leak
surface — self-retrieval, future retrieval, a case closing exactly at the
alert instant — and the scoring arithmetic, and asserts that the recorded
artefact is leak- and error-free.
