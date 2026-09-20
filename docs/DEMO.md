# Demo script (3–5 minutes)

Run before recording:

```bash
python -m fraudgraph.benchmark.run        # 20 cases, ~10s
python -m fraudgraph.benchmark.validate   # should print PASSED
python run_api.py                         # http://127.0.0.1:8077
```

---

## 0:00 — The problem, in one number (20s)

Open the **Overview** tab.

> "590,742 card transactions, six months, no fraud label — just a risk score
> from the bank's model. Half the exam cases are legitimate and most of them
> look suspicious. So the hard part isn't spotting anomalies. It's refusing to
> act on the ones that don't hold up."

Point at the status strip: graph backend, closed-case count, and
**Actions: simulated** — nothing here touches a real financial system.

## 0:20 — The case the risk score missed (80s)

**Investigation queue** → open **HHG-014**.

> "An analyst flagged this one by hand. The bank's own model scored the
> transaction **0.05** — near zero. $74.96, an ordinary online purchase."

Scroll to the facts row.

> "Our assessment is 0.96. Here's why."

Scroll to **Evidence**, read the shared-origin claim.

> "The agent pivoted from the transaction to its device profile and asked what
> else that device touched. A Samsung on Chrome for Android, behind an
> anonymising proxy, marked *New* for every account it appears on — on **28
> distinct cards** in one month. One profile out of 9,706 in the dataset meets
> that test."

Scroll to the **graph**. Let it settle, hover the orange device node.

> "That's the ring. The card we were asked about is on the left. Every blue node
> around the device is a different customer."

Point at the purple nodes.

> "And these four are closed investigations from August and September that the
> bank's own analysts marked 'pattern not matched to a documented typology'.
> Case memory found them through the same device."

## 1:40 — Undocumented means undocumented (30s)

Scroll to the amber **Undocumented pattern** box.

> "The challenge documents five fraud patterns. This is not one of them, so the
> agent describes it in its own words and says how it found it. There's a second
> undocumented typology in the data too — sub-threshold structuring — which
> shows up in case HHG-006."

## 2:10 — Policy, not vibes (60s)

Scroll to **Next best action**.

> "Initial recommendation on the left, final on the right, and what changed
> between them. Every action cites the policy rule that produced it. Block card
> is **L1**. File report is **L2** — and neither has happened."

Scroll to **Approvals & execution**.

> "The agent may execute auto actions. L1 and L2 sit here until a human types
> their name. Approve one —"

Type a name, click **Approve**.

> "— and it's recorded as a *simulated* execution, with what a real integration
> would have done. The system never claims a card was blocked."

## 3:10 — The finding that shaped the design (60s)

**Model & policy** tab.

> "We fitted a classifier on the 5,565 closed investigations. ROC AUC 0.96, and
> completely useless: `bank_risk_score` came out at **minus 5.5**,
> `device_marked_new` at minus 2.7."

> "Because the closed cases aren't a sample of alerts — they're a sample of
> investigations the bank chose to open. All 900 cleared cases are high-scoring
> model alerts that turned out to be travel, a new phone, or a big intended
> purchase. The negative class is *enriched* for the exact signals that mean
> fraud."

Point at the firing-rate table.

> "Only 11.8% of confirmed frauds have any detector firing — against 31% of the
> cleared alerts. Most fraud here has no graph signature at all. So graph
> evidence isn't the classifier; it's the thing that's decisive when it fires.
> The model is a trigger prior plus the measured likelihood ratio of whatever
> actually fired. Every weight on this page has a countable basis."

## 4:10 — Close (20s)

> "Twenty answer files, validated against the format, every identifier checked
> to exist in the dataset. 94 tests, including failure modes: kill the database
> mid-investigation and the case escalates without a verdict rather than
> guessing."

```bash
python -m fraudgraph.benchmark.validate
python -m pytest -q
```

> "A tool failure is never evidence that fraud did or didn't happen."

---

### Things to avoid saying

* Any accuracy figure for the 20 exam cases — we don't have the answer key.
* "Blocked", "filed", "frozen" in the past tense. Everything is recommended or
  simulated.
* Claiming TigerGraph is live unless the status strip says so.
