# Risk model card

## The finding that shaped the design

The obvious thing to do with 5,565 labelled closed investigations is to fit a
classifier. We did, and it was worth doing, because what it produced is the most
useful result in the project.

**First attempt — plain logistic regression on the closed cases.**
ROC AUC 0.963. The coefficients:

| feature | fitted weight |
|---|---:|
| `bank_risk_score` | **−5.52** |
| `device_marked_new` | **−2.65** |
| `consistent_with_history` | **+1.40** |

Every sign is backwards. A model carrying those numbers would clear ring
activity and block ordinary shopping. The 0.963 is real and meaningless: it is
measuring the *trigger*, not fraud.

**Why.** The closed cases are not a sample of alerts. They are a sample of
investigations the bank chose to open:

* all 900 cleared cases began as a high-scoring model alert, and their analyst
  notes say what happened — 716 confirmed travel, 158 confirmed a new phone, 26
  confirmed a large but intended purchase;
* the 4,665 confirmed frauds are overwhelmingly customer reports.

So the negative class is *deliberately enriched* for exactly the anomaly signals
that indicate fraud. The measured consequence:

| population | has any strong detector firing |
|---|---:|
| confirmed fraud | **11.8%** |
| cleared alerts | **31.1%** |
| unalerted ordinary transactions | 7.4% |

Cleared alerts are nearly three times more anomalous than confirmed frauds.
`cnp_new_device` fires on 28.9% of cleared cases and 7.1% of frauds.

**What that means.** In this dataset most fraud has no graph-visible signature
at all — it is a single, unremarkable transaction, discovered because the
cardholder complained. Graph evidence is **high-precision and low-recall**. It
cannot be the classifier. It can be decisive when it fires.

## What the model actually is

```
log-odds(fraud) = trigger prior  +  Σ log(LR) over detectors that fired
```

No global intercept, and no term for the absence of a signal.

### Trigger prior

The prior belongs to the alert's origin, not to the model.

| trigger | log-odds | p with no other evidence | basis |
|---|---:|---:|---|
| `risk_score` | −1.60 | 0.17 | all 900 cleared investigations began as a model score; the README states most high scores are legitimate |
| `customer_report` | +1.00 | 0.73 | every investigated dispute in the history was confirmed fraud; capped because the README states half the exam is legitimate and policy R7 must be able to overturn it |
| `analyst_request` | 0.00 | 0.50 | an analyst has already looked and escalated |

### Detector weights

Each weight is `log(LR)` where LR is the Laplace-smoothed ratio of the rate at
which the detector fires on confirmed fraud to the rate on legitimate activity,
measured by `scripts/measure_detectors.py` over the confirmed frauds, the 900
cleared cases and a sample of transactions no closed case ever touched. Each
term is clipped to ±2.6 so no single detector can carry a case alone.

Three detectors have too few examples to estimate from — the history holds 16
card-testing, 5 structuring and 4 ring cases. For those a documented floor is
applied, taken from the challenge's own statement that they are fraud
typologies, and the sample size is recorded beside the number in
`build/risk_model.json` under `weight_basis`. That is the only place a prior
overrides a measurement in the detector weights, and it is visible in the
console's model card.

`prior_fraud_on_card` is capped at +0.40 regardless of its measured value
(+1.23). The README is explicit: *"Do not automatically classify a new
transaction as fraudulent merely because a related entity appeared in a
historical fraud case."*

### Two inputs that are deliberately excluded

**The bank's `risk_score`.** Fitted on the history it takes a −5.5 coefficient,
for the reason above. It is reported alongside every assessment as a separate
quantity — which is what the README asks for — and never folded into one. A test
asserts that identical evidence at score 0.05 and score 0.95 produces an
identical probability.

**A `customer_dispute` feature.** Every disputed case in the history was
confirmed fraud, so its fitted coefficient would be unbounded and would encode
the sampling rather than the phenomenon. It is carried by the trigger prior
instead, where it is a stated number that the recurring-charge detector can
overturn — which is what policy R7 exists for.

## Measured results

From `build/risk_model.json` and `build/detector_rates.json`; regenerate with
`python -m fraudgraph.analysis.calibrate` and
`python scripts/measure_detectors.py`.

The logistic fit is retained in `build/risk_model_logistic.json` and reported in
the console, because its *failure* is the evidence for the current design:

| measurement (out-of-fold, 5-fold) | value |
|---|---:|
| ROC AUC, all classes | 0.590 |
| ROC AUC vs cleared hard negatives | 0.402 |
| ROC AUC vs unalerted base rate | 0.633 |

Those numbers are why the system does not present itself as a fraud classifier.

## What is calibrated, and what is not

`fraud_probability` is reported honestly as what this model says, and the
reliability table in the model card shows how out-of-fold predictions tracked
outcomes on the history. It is **not** calibrated against the exam's answer key,
which we do not have. No accuracy figure for the 20 exam cases appears anywhere
in this repository.

## Uncertainty, kept separate from probability

`RiskAssessment` carries six distinct quantities, per the challenge's
instruction not to treat them as interchangeable:

1. `bank_risk_score` — the model's number, untouched
2. `graph_indicators` — which detectors fired, and how strongly
3. `historical_evidence` — what case memory contributed
4. `agent_fraud_probability` — the agent's own assessment
5. `confidence` — how well-supported that assessment is
6. `uncertainty_notes` / `conflicting_evidence` — what remains unresolved

`confidence` rises with the number of independent signals and a thick baseline,
and falls when the baseline is thin or when inculpatory and exculpatory findings
disagree. `independent_signal_count` is what policy R1 turns on, and it is
constructed so two detectors firing on the same observation are not counted
twice.
