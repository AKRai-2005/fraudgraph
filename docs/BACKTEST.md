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

All four runs below report **0 leakage violations**.

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

600 cases (300 per class), seed 20260920, local mirror, narrator off.
`uncertain` is counted as "no fraud action", which is what it means
operationally.

### The headline: graph evidence alone

Run over the **entire population** of the two relational typologies — all 9
`undocumented` and all 16 `card_testing` cases in the dataset — against 300
cleared alerts:

| true typology | caught | recall | mean p |
|---|---|---|---|
| **undocumented** (device ring, structuring) | **9 / 9** | **100%** | 0.94 |
| card_testing | 0 / 16 | 0% | 0.59 |

against **7 false fraud calls on 300** of the hardest negatives — 97.7%
specificity. AUC 0.870.

The agent finds **every instance in the dataset** of the two typologies the
challenge does not document, from graph structure alone, without a prior and
without seeing its own case.

### The same run over a random fraud sample

| | recall on fraud | specificity | false fraud calls | AUC |
|---|---|---|---|---|
| `neutral` | 2.67% (8/300) | 98.3% | 5 | 0.718 |
| `score` | 0.33% (1/300) | **100%** | **0** | 0.655 |
| `actual` | 99.3% (298/300) | 100% | 0 | 1.000 |

Read `actual` with the caveat above: it is mostly the trigger prior, and is
reported for completeness rather than as evidence.

The honest reading of `neutral` is in the typology breakdown:

| true typology | caught | recall |
|---|---|---|
| undocumented | 1/1 | 100% |
| card_not_present_new_device | 4/60 | 6.7% |
| card_not_present_fraud | 3/101 | 3.0% |
| account_takeover | 0/73 | 0% |
| out_of_region_use | 0/64 | 0% |

---

## What this establishes, and what it does not

**Established.**

* The agent's edge is real and it is *specific*: relational fraud, found by
  traversal. 9 of 9, with no prior helping it.
* Its caution is real. Under the exam's operating point it produced **zero**
  false fraud verdicts on 300 high-scoring legitimate alerts. The project's
  central claim — that a score is a reason to look, not a verdict — is not
  just rhetoric in the write-up; the agent behaves that way under measurement.
* Case memory is genuinely time-boxed. 0 leakage violations across 1,850
  replayed investigations.

**Not established, and worth saying plainly.**

* **Most fraud in this history is invisible to the graph.** Single-transaction
  customer disputes have no relational structure to find, and they are the
  bulk of the confirmed frauds. Under a neutral prior the agent reaches
  "uncertain" on 292 of 300 — it does not pretend, but nor does it help.
* **The `card_testing` detector does not fire on card-testing cases.** 0 of
  16. Its weight (+1.20) is a documented floor set because the history had too
  few cases to estimate from; we now know the detector's definition does not
  match what these analysts labelled card testing. That is a measured defect,
  not a tuning choice.
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
