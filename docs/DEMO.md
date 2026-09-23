# Demo video script (3–5 minutes)

Read the **bold** lines aloud; everything else is stage direction. The spoken
lines are 641 words — **4:35 of speech** at 140 words a minute — and the
section budgets total **4:50**, which leaves about fifteen seconds for the
clicks. That is inside the five-minute limit but not loose: if you speak
slowly, use the three-minute cut at the end rather than overrunning.

Every figure below matches the build as it stands. If you change anything and
re-record, check the numbers before you say them.

---

## Before you record

```bash
python -m fraudgraph.benchmark.validate   # prints PASSED
python run_api.py                         # http://127.0.0.1:8077
```

* **Start the Savanna workspace ten minutes early.** With it running, the
  status strip says `Graph tigergraph` and the live investigation writes its
  case back to the graph on camera. Asleep, the console says `local (fallback)`
  — true, and visibly weaker. Either works; the strip never lies.
* **Gemini's free tier allows 20 requests a day.** One live investigation uses
  two, so a rehearsal plus a take is four. Check you have room.
* **The narration wait is unpredictable — 7 to 58 seconds** for the same case.
  The script turns that wait into a point rather than dead air (at 0:45). Trim
  it in the edit if it runs long.
* A live re-run marks that case as a working copy in the console. To restore
  the published answers afterwards, with the workspace running:
  `python -m fraudgraph.benchmark.run`.
* Open on the **Overview** tab, browser at 1440px or wider.

---

## 0:00 — The argument, in one chart (45s)

The Overview opens on it. Don't scroll.

> **"590,742 card payments, twenty alerts to judge. Along the bottom, the
> bank's risk score. Up the side, what our agent concluded after investigating
> in the graph. If the score were enough, every point would sit on the
> diagonal."**

Point at the sentence above the chart.

> **"It doesn't. On eighteen of the twenty, graph evidence moved the answer —
> nine escalated, nine cleared. Half of these alerts are legitimate. The hard
> part isn't spotting anomalies; it's refusing to act on the ones that don't
> hold up."**

Point top-left, then bottom-right.

> **"The bank scored this one 0.05; we closed it as fraud at 0.98. It scored
> that one 0.87; we cleared it at 0.02."**

## 0:45 — Watch it investigate (55s)

Click the **HHG-014** point, then **Watch it investigate**.

> **"This isn't a replay — the agent is running now, and every line appears as
> it happens, with the time each query took."**

When `device_neighbors` appears:

> **"There's the turn: it pivoted from the transaction to the device that made
> it, and asked what else that device has touched. Twenty-eight cards."**

When the `stop` line appears:

> **"Fraud at 0.98 on three independent signals — and it stops, citing the rule
> that told it to."**

The feed then says the LLM is writing. While it does:

> **"That last line is the language model writing the summary. The verdict, the
> probability and the actions were fixed before it started. It cannot change
> them, and if it invents an id or an amount, the sentence is thrown away."**

## 1:40 — The evidence behind the verdict (45s)

The case renders when the run finishes. Scroll to **Evidence**.

> **"An analyst flagged this by hand: a $74.96 online purchase the bank scored
> 0.05. Every claim carries the query that produced it."**

Read the shared-device claim.

> **"One device fingerprint, always behind an anonymising proxy, marked New for
> every account it touches — twenty-eight unrelated cards in a month. One
> profile out of 9,706 meets that test."**

Scroll to **The graph around the alert**; hover the orange device node.

> **"That's the ring. Every node on this device is a different customer."**

Point at the case-coloured nodes.

> **"And these are closed investigations the bank's analysts marked as matching
> no documented typology. Case memory found them through the same device."**

## 2:25 — Undocumented means undocumented (15s)

Scroll to the **Undocumented pattern** box.

> **"The challenge documents five patterns. This is none of them, so the agent
> describes it in its own words. There's a second undocumented typology too —
> sub-threshold structuring — in HHG-006."**

## 2:40 — Policy, and who has to approve (35s)

Scroll to **Decision**.

> **"Every action cites the rule that produced it. Monitoring is automatic;
> blocking the card is L1, filing the report is L2 — and neither has
> happened."**

Type your name, click **Approve** on `BLOCK_CARD`.

> **"An approval needs a name, and it's recorded as a simulated execution,
> saying what a real integration would have done. Nothing here touches a real
> financial system."**

Scroll to **Suspicious activity report**.

> **"Where policy requires one, the report is written to stand on its own — two
> of the twenty."**

## 3:15 — When the evidence changes the answer (20s)

Overview → the bottom-right point (**HHG-007**). Scroll to **Decision** →
**Before the evidence request**.

> **"The bank scored this one 0.87. The agent first recommended step-up
> authentication and a call to the cardholder. It asked — the reply is
> simulated, and labelled as such — the cardholder confirmed, and both actions
> came off."**

## 3:35 — Does it actually work? (55s)

Open **Model & policy**.

> **"We can't claim accuracy on the twenty — the key is withheld, and no
> accuracy figure appears anywhere in this project. So we measured on the 5,565
> closed investigations that do have outcomes, replaying each as a live
> alert."**

Point at the backtest table.

> **"Under a neutral prior it caught all nine undocumented-typology frauds in
> the dataset from graph structure alone, at seven false fraud calls in three
> hundred of the hardest negatives. At the exam's operating point: zero."**

Scroll to the logistic fit.

> **"We also fitted a plain classifier on these cases: 0.96, and useless — the
> bank's risk score came out at minus five and a half. These aren't a sample of
> alerts; they're the investigations the bank chose to open. Graph evidence is
> decisive when it fires, not a classifier."**

## 4:30 — Close (20s)

> **"Twenty answer files, validated, every id checked to exist in the dataset,
> every case written back into TigerGraph — and the same query catalogue runs
> through the MCP server with identical answers. Three hundred and ten tests. A
> tool failure is never evidence that fraud did or didn't happen."**

---

## Things not to say

* **Any accuracy figure for the 20 exam cases.** There is no answer key.
* **"Blocked", "filed", "frozen" in the past tense.** Everything is recommended
  or simulated.
* **That TigerGraph is live** unless the status strip says `tigergraph`.
* **That the agent discovered the patterns by itself.** It applies detectors we
  wrote after reading the closed cases' analyst notes.

## If you need to cut to three minutes

Drop 2:25 (undocumented) and the classifier finding at 3:35. Keep the chart,
the live run, the approval and the backtest result.
