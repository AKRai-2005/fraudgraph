# Demo video script (3–5 minutes)

Read the **bold** lines aloud; everything else is stage direction. Timings are
cumulative and assume a normal speaking pace (~140 words a minute). Target
finish: **4:20**.

Every figure below is in the build as it stands. If you re-record after
changing anything, check the numbers still match before you say them.

---

## Before you record

```bash
python -m fraudgraph.benchmark.validate   # prints PASSED
python run_api.py                         # http://127.0.0.1:8077
```

* **Start the Savanna workspace** ten minutes early. With it running, the
  status strip says `Graph tigergraph` and the live investigation writes its
  case back to the graph on camera. Asleep, the console says `local (fallback)`
  — true, and visibly weaker. Either works; the strip never lies.
* **Gemini's free tier allows 20 requests a day.** One live investigation uses
  two. A rehearsal plus a take is four. Check you have room.
* **The narration wait is unpredictable — 7 to 58 seconds** for the same case.
  The script turns that into a point rather than dead air (0:55). Trim it in
  the edit if it runs long.
* A live re-run marks that case as a working copy in the console. To put the
  published answers back afterwards, with the workspace running:
  `python -m fraudgraph.benchmark.run`.
* Open on the **Overview** tab, browser at 1440px or wider, and have
  **HHG-014** ready in the case picker.

---

## 0:00 — The argument, in one chart (30s)

The Overview opens on it. Don't scroll.

> **"Six months of card payments — 590,742 transactions — and twenty alerts to
> judge. Along the bottom is the bank model's risk score. Up the side is what
> our agent concluded after investigating each one in the graph. If the score
> were enough, every point would sit on the diagonal."**

Point at the lede above the chart.

> **"It doesn't. On eighteen of the twenty, graph evidence moved the answer:
> nine escalated, nine cleared. The hard part of this dataset isn't spotting
> anomalies — half these alerts are legitimate and most of them look
> suspicious. It's refusing to act on the ones that don't hold up."**

Point top-left, then bottom-right.

> **"The bank scored this one 0.05. We closed it as fraud at 0.98. It scored
> that one 0.87; we cleared it at 0.02."**

Point at the status strip.

> **"And every screen says the same thing: actions are simulated. Nothing here
> touches a real financial system."**

## 0:30 — Watch it investigate (55s)

Click the **HHG-014** point on the chart, then **Watch it investigate**.

> **"This isn't a replay. The agent is running now, and each line appears as it
> happens — every graph query with the time it actually took."**

Let it stream. When `device_neighbors` appears:

> **"There's the turn. It pivoted from the transaction to the device that made
> it, and asked what else that device has touched. Twenty-eight cards."**

When the `stop` line appears:

> **"Fraud at 0.98, on three independent signals, and it stops — with the rule
> that told it to stop."**

The feed then says the LLM is writing. While it does:

> **"The last line is the language model writing the summary. The verdict, the
> probability and the actions were all fixed before it started, and it can't
> change them. If it invents an id or an amount that isn't in the evidence, the
> sentence is thrown away and a template is used."**

## 1:25 — The evidence behind the verdict (50s)

The case renders when the run finishes. Scroll to **Evidence**.

> **"An analyst flagged this by hand: a $74.96 online purchase the bank's own
> model scored 0.05. Every claim here carries the graph query that produced it
> and the ids it rests on."**

Read the shared-device claim.

> **"One device fingerprint — always behind an anonymising proxy, marked New
> for every account it appears on — across twenty-eight unrelated cards in a
> month. One profile out of 9,706 in this dataset meets that test."**

Scroll to **The graph around the alert**. Hover the orange device node.

> **"That's the ring. The card we were asked about is one of these; every other
> node on the device is a different customer."**

Point at the case-coloured nodes.

> **"And these are closed investigations the bank's own analysts marked as
> matching no documented typology. Case memory found them through the same
> device — that's the past informing this decision."**

## 2:15 — Undocumented means undocumented (20s)

Scroll to the **Undocumented pattern** box.

> **"The challenge documents five fraud patterns. This is not one of them, so
> the agent describes it in its own words. There's a second undocumented
> typology in the data as well — sub-threshold structuring, purchases priced
> just under an authorisation limit — and it's case HHG-006."**

## 2:35 — Policy, and who has to approve (45s)

Scroll to **Decision** in the right column.

> **"Every action cites the policy rule that produced it. Monitoring is
> automatic. Blocking the card is L1. Filing the report is L2 — and neither has
> happened."**

Type your name in the field, click **Approve** on `BLOCK_CARD`.

> **"An approval needs a name. It's recorded as a simulated execution, saying
> what a real integration would have done. The system never claims a card was
> blocked."**

Scroll to **Suspicious activity report**.

> **"Where policy requires it, the filing is written to stand on its own — two
> of the twenty. And the recommendation is allowed to change: four cases asked
> for more evidence first, and the console shows the actions before and after."**

## 3:20 — Does it actually work? (45s)

Open **Model & policy**.

> **"We can't claim accuracy on the twenty — the answer key is withheld, and no
> accuracy figure for them appears anywhere in this project. So we measured on
> the 5,565 closed investigations that do have outcomes, replaying the agent as
> if each were a live alert, with everything after the alert clamped off."**

Point at the backtest table.

> **"Under a neutral prior — nothing on the scale — it caught all nine
> undocumented-typology frauds in the dataset from graph structure alone, at
> seven false fraud calls in three hundred of the hardest negatives. At the
> exam's own operating point: zero."**

> **"We also fitted a plain classifier on those closed cases. It scored 0.96 and
> was useless — the bank's risk score came out at minus five and a half. The
> closed cases aren't a sample of alerts; they're a sample of investigations the
> bank chose to open, so the negatives are enriched for the exact signals that
> mean fraud. That's why graph evidence here is decisive when it fires, not a
> classifier."**

## 4:05 — Close (15s)

> **"Twenty answer files, validated against the format, every id checked to
> exist in the dataset, every case written back into TigerGraph — and the whole
> catalogue also runs through TigerGraph's MCP server, with identical answers.
> Three hundred and ten tests, forty-five of them driving this console in a real
> browser. A tool failure is never evidence that fraud did or didn't happen."**

---

## Things not to say

* **Any accuracy figure for the 20 exam cases.** There is no answer key.
* **"Blocked", "filed", "frozen" in the past tense.** Everything is recommended
  or simulated.
* **That TigerGraph is live** unless the status strip says `tigergraph`.
* **That the agent found the patterns by itself.** It applies detectors we
  wrote after reading the closed cases' analyst notes.

## If you need to cut to three minutes

Drop 2:15 (undocumented) and the second half of 3:20 (the classifier finding).
Keep the chart, the live run, the approval, and the backtest result.
