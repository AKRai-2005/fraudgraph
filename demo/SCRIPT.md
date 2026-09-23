# Narration script

Read the **bold** lines. Timestamps match `reference/fraudgraph-demo-reference.mp4`
exactly, so you can record voice-over straight onto it. ~530 words, 3:45–4:00
spoken at a normal pace.

Two rules while recording: never say an action *happened* (everything is
recommended or simulated), and never claim accuracy on the 20 exam cases.

---

### 0:00 — Title card (12s)

> **"A bank's fraud model gives every alert a score. This project takes the
> position that a score is a reason to look, and never a verdict — so it
> investigates the alert in a graph of 590,742 transactions and decides for
> itself."**

### 0:12 — Section card: where the graph disagrees (4s)

*Silence, or:*

> **"Start with the argument for the whole thing."**

### 0:16 — The disagreement chart (33s)

> **"Along the bottom is the bank model's risk score. Up the side is what our
> agent concluded after investigating each alert in the graph. If the score
> were enough, every point would sit on the diagonal."**

> **"It doesn't. On eighteen of the twenty alerts the graph moved the answer —
> nine escalated, nine cleared. Half of these alerts are legitimate and most
> of them look suspicious, so the hard part isn't spotting anomalies. It's
> refusing to act on the ones that don't hold up."**

> **"The bank scored this one 0.05; we closed it as fraud at 0.98. It scored
> that one 0.87; we cleared it at 0.02. And every screen carries this: actions
> are simulated — nothing here touches a real financial system."**

### 0:49 — Section card: watch it investigate (4s)

*Silence.*

### 0:53 — The live investigation (37s)

> **"This is the agent running now, not a replay. Every line appears as it
> happens, with the time each graph query actually took."**

> **"It pivots from the transaction to the device that made it, and asks what
> else that device has touched — twenty-eight cards. Three detectors match, it
> reaches fraud at 0.98 on three independent signals, and then it stops, citing
> the policy rule that told it to stop."**

> **"Thirteen graph calls. The verdict, the probability and the actions are all
> computed in code. The language model only writes the summary, and if it
> invents an id or an amount that isn't in the evidence, the sentence is thrown
> away."**

### 1:30 — Evidence and the graph (37s)

> **"Here is the case. The agent's probability sits beside the bank's score —
> never folded into it — with what remains uncertain stated underneath."**

> **"Every claim carries the query that produced it and the ids it rests on.
> This one: a device profile shared by twenty-eight unrelated cards in a month,
> marked New for every account it touches, always behind an anonymising proxy.
> Exactly one profile out of 9,706 in this dataset meets that test."**

> **"That's the ring, two hops out. The challenge documents five fraud
> patterns; this is none of them, so the agent describes it in its own words."**

### 2:07 — Section card: policy and approval (4s)

*Silence.*

### 2:11 — Policy and approval (28s)

> **"Every action cites the rule that produced it. Monitoring is automatic.
> Blocking the card is L1, filing the report is L2 — and neither has
> happened."**

> **"An approval needs a name. Once it has one, what gets recorded is a
> simulated execution saying what a real integration would have done. The
> system never claims a card was blocked."**

### 2:39 — When the evidence changes the answer (23s)

> **"This is the alert the bank scored 0.87. The agent first recommended
> step-up authentication and a call to the cardholder. It asked — the reply is
> simulated, and labelled as simulated, because the challenge provides none —
> the cardholder confirmed, and both of those actions came off the
> recommendation."**

### 3:02 — Section card: does it actually work (4s)

*Silence.*

### 3:06 — What we measured (35s)

> **"We claim no accuracy on the twenty exam cases — the answer key is
> withheld. So we measured on the 5,565 closed investigations that do have
> outcomes, replaying each as a live alert with everything after it clamped
> off."**

> **"Under a neutral prior it caught all nine undocumented-typology frauds in
> the dataset from graph structure alone, at seven false fraud calls in three
> hundred of the hardest negatives. At the exam's own operating point: zero."**

> **"We also fitted a plain classifier on those closed cases. It scored 0.96
> and was useless — the bank's risk score came out at minus five and a half —
> because they're a sample of investigations a bank chose to open, not a sample
> of alerts."**

### 3:41 — End card (10s)

> **"Twenty answer files, validated, every case written back into TigerGraph,
> and the same query catalogue running through TigerGraph's MCP server. Three
> hundred and ten tests. It's all reproducible — thank you."**

---

## Lines to avoid

* "Blocked", "filed", "frozen" in the past tense — everything is recommended
  or simulated.
* Any accuracy figure for the 20 exam cases.
* "TigerGraph is live" unless your status strip says `tigergraph`.
* "The agent discovered the patterns" — it applies detectors written after
  reading the closed cases' analyst notes.

## If you need to reach 3:00

Cut 2:39 (evidence changes the answer) and the last paragraph at 3:06. Keep
the chart, the live run, the approval and the backtest result.
