# A fraud agent that argues with the bank's model — and mostly says "no"

*Built for the TigerGraph × Hacker House Goa challenge (IEEE-CIS edition).
Code: <https://github.com/AKRai-2005/fraudgraph> · Console:
<https://akrai-2005.github.io/fraudgraph/>*

The dataset hands you 590,742 card transactions, six months, no fraud label —
and twenty alerts to judge. Each alert carries a risk score from the bank's
model. The temptation is to build a better scorer.

We didn't, because the data says not to. Half the exam cases are legitimate
and most of them look suspicious: a high score usually means someone is
travelling or bought a new phone. The dataset README says it in one line, and
it became the thing our whole system is built around: **a risk score is a
reason to look, never a verdict.**

So the hard part isn't spotting anomalies. It's refusing to act on the ones
that don't hold up — and finding the ones the score missed. On the twenty exam
alerts, graph evidence moved our agent away from the bank's score **eighteen
times: nine escalated, nine cleared.**

---

## What it does

One investigation is a state machine, not a prompt:

```
alert → baseline retrieval → targeted retrieval → 9 pattern detectors
                                   │                      │
                          (the LLM may add queries)  risk + uncertainty
                                                          │
  case memory ← write case ← policy engine ← evidence request ←┘
  (TigerGraph)              (R1–R10, routes)  (simulated, and says so)
```

Twelve steps, each appending to a timeline and a query ledger. Every claim in
an answer file carries the graph query that produced it and the entity ids it
rests on — `query:device_neighbors(device_profile=…)` in an answer is a query
that actually ran, and a test asserts it.

**Where the LLM is, and isn't.** Gemini (`gemini-flash-lite-latest`, free
tier) proposes extra retrieval and writes prose. It does not decide the
verdict, the probability, the pattern, the actions, the approval routes or
whether a SAR is due — all of that is code. A rewrite that introduces an id,
amount or date not already in the evidence is thrown away and the
deterministic template is used. With no API key the system runs identically
and reports `tokens: 0`.

## The graph is the point

Schema: 11 vertex types, 18 edge types, loaded into a TigerGraph Savanna
workspace (4.2.5) — 590,742 `Transaction`, 14,318 `PaymentCard`, 13,553
`Customer`, 9,706 `DeviceProfile`, 5,565 `ClosedCase`. Historical cases and
the agent's own cases are deliberately separate vertex types, so ground truth
and conclusions can never be confused.

The pivot that matters is two hops:

```
Transaction ──FROM_DEVICE──▶ DeviceProfile ──▶ Transaction ──▶ PaymentCard
```

That is how case **HHG-014** — a $74.96 online purchase the bank's model
scored **0.05** — becomes a 28-card ring. One device fingerprint, always
behind an anonymising proxy, marked *New* for every account it touches,
spread across 28 unrelated cards in a month. Exactly **one profile out of
9,706** meets that test. Our agent closed it as fraud at **0.98**.

**One catalogue, three backends.** 17 named queries with declared parameters
(`graph/queries.py`) are implemented three times: installed GSQL over
pyTigerGraph, the same queries through the official `tigergraph-mcp` server,
and a local pandas mirror for testing. All three produce the same 20 answers.
That agreement is the check — a result that survives three independent code
paths isn't an artefact of one.

It also caught bugs we'd never have seen otherwise. The three backends agreed
on every *verdict* while disagreeing on the *evidence* underneath: different
quantile conventions, different tie-breaking when two prior cases scored
equally, an unsorted subset of a device ring. Checking only the verdicts hid
all three.

## Two typologies the challenge doesn't document

Nine closed cases are labelled only `undocumented`. Reading their analyst
notes, they're two distinct patterns, and we implemented detectors that key on
behaviour rather than on case ids:

1. **Coordinated shared-device ring** — the HHG-014 pattern above.
2. **Sub-threshold structuring** — several online purchases in one short
   window, each priced just under the $500 authorisation threshold, totalling
   far more. In the exam that's **HHG-006**: four purchases, $1,906.07.

Both are found by traversal from the flagged transaction.

## The most useful thing we built was a model that failed

The obvious move with 5,565 labelled closed investigations is to fit a
classifier. We did. **ROC AUC 0.963** — and every coefficient was backwards:

| feature | fitted weight |
|---|---:|
| `bank_risk_score` | **−5.52** |
| `device_marked_new` | **−2.65** |
| `consistent_with_history` | **+1.40** |

A model carrying those numbers clears ring activity and blocks ordinary
shopping. The 0.963 is real and meaningless: it is measuring *which alerts a
bank chose to investigate*, not fraud.

Why: the closed cases aren't a sample of alerts. All 900 cleared cases began
as high-scoring model alerts that turned out to be travel (716), a new phone
(158) or a large intended purchase (26). The negative class is **deliberately
enriched for exactly the anomaly signals that indicate fraud**. Measured:

| population | any strong detector firing |
|---|---:|
| confirmed fraud | **11.8%** |
| cleared alerts | **31.1%** |
| ordinary unalerted transactions | 7.4% |

Cleared alerts are nearly three times more anomalous than confirmed frauds.

So we didn't fit the final model. We stated it:

```
log-odds(fraud) = trigger prior + Σ log(likelihood ratio) over detectors that fired
```

No global intercept, each term clipped at ±2.6 so no single detector carries a
case, and the bank's score reported beside every assessment but never folded
in — a test asserts that identical evidence at score 0.05 and 0.95 yields an
identical probability.

## Does it actually work?

We can't say anything about accuracy on the twenty: the key is withheld, and
no accuracy figure for them appears anywhere in our repo. So we measured where
outcomes exist — replaying the agent over the closed investigations as if each
were a live alert.

Three things would have made that a lie, and each had to be controlled:

* **Time.** Case memory is filtered by date, every forward transaction window
  is clamped at the moment the case opened, and the replay re-reads the bounds
  actually sent from the query ledger rather than trusting the clamp. Zero
  leakage violations across every run.
* **The trigger prior.** In this history every cleared case began as a model
  score and every investigated dispute was fraud, so replaying under real
  triggers mostly scores the prior. The headline run gives every case a
  neutral `analyst_request` prior of 0.50.
* **The sample.** Every cleared case is a high-scoring alert — the hardest
  negatives in the data. Specificity here is a lower bound.

Results, neutral prior, graph evidence alone:

| | caught | recall |
|---|---|---|
| **undocumented typologies** (all 9 in the dataset) | **9 / 9** | **100%** |
| card testing (all 16) | 5 / 16 | 31% |
| false fraud calls on 300 cleared alerts | 7 | 97.7% specificity |

ROC AUC 0.898. At the exam's own operating point: **zero** false fraud
verdicts on 300 high-scoring legitimate alerts.

**A number we withdrew.** The first version of that backtest time-boxed case
memory but not transactions — a replayed alert could count cards compromised
after its own investigation opened. Clamping the windows moved the
general-sample AUC from **0.718 to 0.691**. The 0.718 had already been
written down; it's marked withdrawn in `docs/BACKTEST.md`. The headline (9 of
9) didn't move.

**A detector the backtest found broken.** `card_testing` caught 0 of 16. The
README's definition — three small authorisations within an hour, then a larger
purchase — matches none of the real runs, which interleave probes and
purchases, are sparse, and last up to eleven days. Rebuilt around probes under
$2, with the threshold chosen by a rule fixed *before* looking at recall (the
variant closest to the README whose firing rate on cleared alerts is ≤1%,
selected on the negative class only): 0/16 → 5/16, no added false positives.
On the 13 cases nobody inspected by hand, 3 — **23% out of sample**, which is
the number to quote.

What this establishes is narrow and worth stating narrowly: the agent's edge
is **relational fraud found by traversal**, and its caution is real. Most
fraud in this history is a single unremarkable transaction reported by the
cardholder, with no graph structure to find. On those it says *uncertain*
rather than pretend.

## The console

A FastAPI service and a dependency-free front end, set like a case file rather
than a dashboard. It opens on the chart that is the whole argument: the bank's
score along the bottom, our assessment up the side, and the points that don't
sit on the diagonal.

Press **Watch it investigate** and the agent runs live over server-sent
events — each reasoning step and each graph query as it happens, with the time
it really took. Actions that need a person (`L1`, `L2`) wait for a named
approver and record a *simulated* execution; nothing reaches a real financial
system, and every screen says so.

310 automated tests, 45 of which drive the console in a real browser —
navigation, filters, every route into a case, approve and reject, the live
stream, the graph, no sideways scrolling at eight widths from 320 to 1920px,
and WCAG AA contrast for every piece of text it renders in both themes.

## What we'd tell the next team

* **Check the evidence, not just the answer.** Three backends agreeing on
  verdicts hid three bugs in what the evidence said.
* **Fit the obvious model early** — not to use it, but because its failure
  tells you what your data actually is.
* **Measure leakage as a failure, not a warning.** Ours exits non-zero.
* **Write down what you refuse to claim.** No accuracy on the twenty, a
  withdrawn AUC, 23% out-of-sample recall on card testing, and the fact that
  most fraud here is invisible to a graph — all of it is in the repo.

Everything runs on free tiers: a Savanna workspace and Gemini's free tier,
₹0 of infrastructure.

*Repo: <https://github.com/AKRai-2005/fraudgraph> — the backtest is in
`docs/BACKTEST.md`, the model card in `docs/RISK_MODEL.md`, and what the
system cannot do in `docs/LIMITATIONS.md`.*
