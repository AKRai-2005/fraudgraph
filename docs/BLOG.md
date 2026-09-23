# Our fraud classifier scored 0.963 AUC. We threw it away.

*What we learned building an agentic fraud investigator on TigerGraph for the
Hacker House Goa challenge — including the number we had to withdraw.*

Code: <https://github.com/AKRai-2005/fraudgraph> ·
Console: <https://akrai-2005.github.io/fraudgraph/>

---

The challenge gives you 590,742 card transactions, six months, no fraud
labels, 5,565 historical investigations that *do* have outcomes, and twenty
alerts to judge. Each alert carries a risk score from the bank's model.

The obvious first move is to fit a classifier on those 5,565 closed cases. We
did, on day two. **ROC AUC 0.963.** Then we looked at the coefficients:

| feature | fitted weight |
|---|---:|
| `bank_risk_score` | **−5.52** |
| `device_marked_new` | **−2.65** |
| `consistent_with_history` | **+1.40** |

Every sign is backwards. A model carrying those numbers clears a device ring
and blocks someone's holiday. The 0.963 is real, reproducible, and completely
useless — it is measuring *which alerts a bank chose to investigate*, not
fraud.

Here is why, and it is the single most useful thing we found in this dataset.
The closed cases are not a sample of alerts. They are a sample of
investigations somebody opened. All 900 cleared cases began as high-scoring
model alerts that turned out to be travel (716), a new phone (158), or a large
but intended purchase (26). So the "legitimate" class is *deliberately
enriched* with exactly the anomaly signals that indicate fraud:

| population | fires at least one strong detector |
|---|---:|
| confirmed fraud | **11.8%** |
| cleared alerts | **31.1%** |
| ordinary unalerted transactions | 7.4% |

Cleared alerts are nearly three times more anomalous than confirmed frauds. If
you train on this and ship it, you have built a machine that blocks careful
customers.

That finding set the design: **graph evidence cannot be the classifier. It is
the thing that is decisive when it fires.** Everything below follows from it.

## What the agent actually is

An investigation is a state machine, not a prompt. Twelve steps, each one
appending to a timeline and a query ledger:

```
alert → baseline retrieval → targeted retrieval → 9 pattern detectors
                                   │                       │
                        (the LLM may propose queries)  risk + uncertainty
                                                           │
   case memory ← write case ← policy engine ← evidence request ←┘
   (TigerGraph)              (R1–R10, routes)   (simulated, and labelled)
```

The probability is not fitted. It is stated:

```
log-odds(fraud) = trigger prior + Σ log(likelihood ratio) over detectors that fired
```

No global intercept, no term for the absence of a signal, every term clipped
at ±2.6 so no single detector can carry a case alone, and each likelihood
ratio measured on the history with its sample size recorded beside it. The
bank's score is reported next to every assessment and never folded in — a test
asserts that identical evidence at score 0.05 and at 0.95 produces an
identical probability.

**Where the LLM is, and is not.** Gemini (`gemini-flash-lite-latest`, free
tier) may propose extra retrieval — validated against the query catalogue
before anything executes — and it writes the case summary and the SAR
narrative. It does not decide the verdict, the probability, the pattern, the
actions, the routes, or whether a report is due. If a rewrite introduces an
id, an amount or a date that is not already in the retrieved evidence, it is
thrown away and the deterministic template stands. With no API key the system
runs identically and reports `tokens: 0`.

## One case, end to end

**HHG-014.** An analyst flagged a $74.96 online purchase by hand. The bank's
model scored it **0.05** — near zero. Nothing about the transaction is
remarkable.

Two hops in the graph:

```
Transaction ──FROM_DEVICE──▶ DeviceProfile ──▶ Transaction ──MADE_BY──▶ PaymentCard
```

Straight from the published answer file:

```json
{
  "claim": "Shared device ring: device profile shared by 28 unrelated cards within 30 days, marked New for 100% of the transactions it ever appears on, and consistently behind IP_PROXY:ANONYMOUS.",
  "ref": "query:device_neighbors(device_profile=SM-G935F Build/NRD90M | Android 7.0 | chrome 62.0 for android | 1920x1080)",
  "entity_ids": ["3460634", "3478561", "3489320"]
}
```

Every claim in every answer file carries the query that produced it and the
ids it rests on. `query:device_neighbors(...)` is a query that actually ran; a
test asserts that every `ref` names a real query in the catalogue.

Exactly **one device profile out of 9,706** in this dataset meets that test —
shared across many unrelated cards, marked *New* every time, always behind an
anonymising proxy. Case memory then pulled four closed investigations from the
same device, all labelled by the bank's own analysts as matching no documented
typology.

The agent stopped with a reason, not a threshold:

```
Fraud probability 0.98 is at or above 0.85 with 3 independent pieces of evidence (policy 6).
```

and produced actions with routes attached: `BLOCK_CARD` (**L1**),
`FILE_REPORT` (**L2**), and four the agent may carry out itself. Neither L1 nor
L2 has happened. They wait for a named human, and when a human approves one,
what gets recorded is a *simulated* execution that states what a real
integration would have done.

Across the twenty alerts, graph evidence moved the verdict away from the
bank's score **eighteen times — nine escalated, nine cleared.** That is the
product in one sentence: it argues with the score in both directions.

**The challenge documents five fraud patterns. Nine closed cases match none of
them**, labelled only `undocumented`. Reading their analyst notes, they are
two distinct typologies, and we wrote detectors that key on behaviour rather
than on case ids. One is the device ring above. The other is **sub-threshold
structuring**: several online purchases inside one short window, each priced
just under the $500 authorisation threshold so no single charge triggers
review, totalling far more than it. In the exam that is HHG-006 — four
purchases, $1,906.07 — and the agent describes the pattern in its own words
because there is no label to reach for.

## The graph

11 vertex types, 18 edge types, on a TigerGraph Savanna workspace (4.2.5):
590,742 `Transaction`, 14,318 `PaymentCard`, 13,553 `Customer`, 9,706
`DeviceProfile`, 5,565 `ClosedCase`. Historical cases and the agent's own
cases are separate vertex types on purpose — ground truth and conclusions must
never be confusable.

One catalogue of **17 named GSQL queries** is the agent's entire tool surface,
and it is implemented three times: installed GSQL over pyTigerGraph, the same
queries through the official `tigergraph-mcp` server, and a pandas mirror for
offline testing. All three answer the same 20 cases.

That redundancy is not belt-and-braces. It found bugs nothing else would have:
**the three backends agreed on every verdict while disagreeing on the evidence
underneath** — different quantile conventions, different tie-breaking between
two equally-scored prior cases, an unsorted subset of a device ring. If we had
compared conclusions instead of evidence, all three would still be there.

## Does it actually work?

We publish **no accuracy figure for the twenty exam cases.** The answer key is
withheld; any number would be a guess, and none appears anywhere in the repo.

What we could measure is the 5,565 closed investigations that carry real
outcomes. The agent is replayed over them as if each were a live alert. Three
things had to be controlled or the result would have been a lie:

1. **Time.** Case memory is filtered by date, every forward-looking
   transaction window is clamped to the moment the case opened, and the replay
   re-reads the bounds *actually sent* from the query ledger rather than
   trusting the clamp. Leakage is a failure, not a warning: the runner exits
   non-zero. Zero violations across every run reported here.
2. **The trigger prior.** In this history every cleared case began as a model
   score and every investigated dispute was fraud, so replaying under real
   triggers mostly scores the prior. The headline run gives every case a
   neutral `analyst_request` prior of 0.50 — nothing on the scale, so anything
   that separates the classes came from the graph.
3. **The sample.** Every cleared case is a high-scoring alert that turned out
   fine: the hardest negatives in the data. Specificity measured here is a
   lower bound, not a typical-traffic number.

Results, neutral prior, graph evidence alone, run over the entire population
of both relational typologies against 300 cleared alerts:

| true typology | caught | recall | mean p |
|---|---|---|---|
| **undocumented** (device ring, structuring) | **9 / 9** | **100%** | 0.94 |
| card testing | 5 / 16 | 31% | 0.71 |
| false fraud calls on 300 cleared alerts | 7 | 97.7% specificity | |

ROC AUC **0.898**. At the exam's own operating point: **zero** false fraud
verdicts on those 300.

And the part that matters more than any of it — on a random sample of 300
frauds the same neutral run reaches **2.7% recall**. Most fraud in this
history is a single unremarkable transaction that a cardholder disputed. There
is no relational structure to find, and the agent says *uncertain* rather than
pretend. Its edge is narrow, specific, and real: **relational fraud, found by
traversal.**

## Four things that were wrong, and how we know

An honest answer to "does it work" is mostly a list of things that were
broken.

**The backtest leaked.** The first version time-boxed case memory but not
transactions — a replayed alert could count cards compromised *after* its own
investigation opened. Clamping every window moved the general-sample AUC from
**0.718 to 0.691**. The 0.718 had already been written down, so it is marked
withdrawn in `docs/BACKTEST.md`. The headline (9 of 9) did not move.

**A detector caught nothing.** `card_testing` scored **0 of 16**. Its
definition took the README literally — three small authorisations within an
hour, then a larger purchase — and the real runs break all three assumptions:
probes and purchases interleave, probes are sparse, runs last up to eleven
days. Rebuilt around probes under $2, with the threshold fixed by a rule
stated *before* recall was looked at (the variant closest to the README whose
firing rate on cleared alerts is ≤1%, selected on the negative class only):
0/16 → 5/16, no added false positives. On the 13 cases nobody had inspected by
hand it catches 3 — **23% out of sample**, and that is the number we quote.

**Its weight still measures negative.** The rebuilt detector fires on 31 of
4,665 confirmed frauds and 39 of 3,400 legitimate ones — a likelihood ratio of
0.58, which taken at face value argues for innocence. Traced on the agent's
own code path: all 39 legitimate firings fall on 5 cards, all 5 have a
confirmed-fraud case, and the detector fires on no card without fraud history.
The negative class is contaminated exactly where this detector looks, so the
measured ratio is not believed and the weight stays at its documented floor —
with the trace recorded as its basis.

**Our latency was understated.** The console said an investigation took
0.19 seconds, because the timer stopped before the LLM wrote the narrative. On
the free tier that narration has run from 7 to 58 seconds for the same case.
It is now measured over the whole run, and the live feed announces the wait
and reports it afterwards.

## Verify any of this in about two minutes

Nothing above needs to be taken on trust. The repository runs with no
credentials at all — the local mirror serves the same query catalogue:

```bash
pip install -r requirements.txt && pip install -e .
python -m fraudgraph.benchmark.validate    # checks all 20 against the answer format
python -m pytest -q                        # 310 tests, 45 of them in a browser
python -m fraudgraph.analysis.backtest --all-modes   # the numbers above
```

The 20 answer files are in `cases/`. The backtest artefact, including leakage
counters, is `build/backtest.json`. The model card with every weight's sample
size is `docs/RISK_MODEL.md`. What the system cannot do is
`docs/LIMITATIONS.md`, and it is the document we would read first.

## What we would tell the next team

* **Fit the obvious model early** — not to ship it, but because its failure
  tells you what your data actually is. Ours took a day and redirected the
  whole project.
* **Compare evidence, not conclusions.** Three backends agreeing on verdicts
  hid three bugs in what the evidence said.
* **Treat leakage as a build failure.** If a replay can see the future, every
  number after it is decoration.
* **Write down what you refuse to claim.** No accuracy on the twenty, a
  withdrawn AUC, 23% out-of-sample on card testing, and the plain fact that
  most fraud here is invisible to a graph.

A risk score is a reason to look, never a verdict. It turns out the same
applies to an AUC.

---

*Built on TigerGraph Savanna and Gemini's free tier — ₹0 of infrastructure.
The console, the backtest and all 20 answers:
<https://github.com/AKRai-2005/fraudgraph>*
