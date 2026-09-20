# Demo script (3–5 minutes)

Run before recording:

```bash
pip install -e .                          # once
python -m fraudgraph.benchmark.run        # 20 cases, ~10s
python -m fraudgraph.benchmark.validate   # should print PASSED
python run_api.py                         # http://127.0.0.1:8077
```

Start the Savanna workspace first if you want the graph backend live — it
suspends itself when idle, and the console will otherwise say, correctly and
visibly, that it is serving from the local mirror. Either way the demo works;
the status strip tells the truth about which one answered.

---

## 0:00 — The whole argument, in one chart (35s)

The **Overview** opens on it. Do not scroll past it.

> "590,742 card transactions, six months, no fraud label — just a risk score
> from the bank's model on each alert. This chart is that score along the
> bottom, and what our agent concluded after investigating it in the graph, up
> the side. If a score were enough, every point would sit on the diagonal."

Point at the counters.

> "They don't. On 18 of the 20 alerts the graph moves the answer — nine
> escalated, nine cleared. Two agree. The hard part of this problem isn't
> spotting anomalies, it's refusing to act on the ones that don't hold up."

Point top-left, then bottom-right.

> "That one up there the model scored **0.05**. We closed it as fraud at 0.98.
> That one on the right it scored **0.87**; we cleared it at 0.02. Both of
> those are a click away from the evidence."

Point at the status strip: graph backend, closed-case count, and
**Actions: simulated** — nothing here touches a real financial system.

## 0:35 — Watch it work (45s)

Click the top-left point (**HHG-014**), then **Watch it investigate**.

> "This isn't a replay. The agent is running now, and every line is a real
> step: each graph query with the time it actually took."

Let it stream. When `device_neighbors` lands:

> "There. It pivoted from the transaction to the device that made it, and
> asked what else that device touched. **28 cards.** That's the moment the
> case turns — and it's a graph traversal, not a model score."

## 1:20 — The evidence behind it

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
