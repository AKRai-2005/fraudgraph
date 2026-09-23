# Social posts

The form asks every team member to post on any platform, tagging
**@TigerGraphDB** and **@247pmstudio**, and to paste every link.

Check both handles on the platform before posting — they are written here as
the form gives them, and LinkedIn uses company pages rather than @handles, so
search for "TigerGraph" and "247 PM Studio" and pick from the mention
dropdown.

Fill in the two links before posting:

* repo — https://github.com/AKRai-2005/fraudgraph
* console — https://akrai-2005.github.io/fraudgraph/
* video — *(paste yours)*

---

## X / Twitter — team lead

> Built an agentic fraud investigator on @TigerGraphDB for the HHGoa task.
>
> The bank's model scored one alert 0.05. Two hops through the graph — payment
> → device → other cards — and that device turns out to be on 28 unrelated
> cards in a month. One profile out of 9,706 in the dataset.
>
> Verdict: fraud, 0.98.
>
> On 20 alerts the graph moved the answer 18 times: 9 escalated, 9 cleared.
> Half the work is refusing to act on high scores that don't hold up.
>
> Code + how it's measured 👇
> https://github.com/AKRai-2005/fraudgraph
> @247pmstudio

## LinkedIn — team lead

> **A risk score is a reason to look, never a verdict.**
>
> That line from the dataset README shaped everything we built for the
> TigerGraph × Hacker House Goa challenge: an agent that investigates a fraud
> alert against a knowledge graph of 590,742 transactions and decides what kind
> of fraud it is, how far it goes, and what the bank should do — with the graph
> query behind every single claim.
>
> The most useful result was a failure. We fitted a classifier on 5,565 closed
> investigations: ROC AUC 0.963, and every coefficient backwards — the bank's
> own risk score came out at −5.5. Those closed cases aren't a sample of
> alerts; they're a sample of investigations a bank chose to open, so the
> "legitimate" class is enriched with exactly the anomaly signals that indicate
> fraud. Cleared alerts fire a strong detector 31% of the time against 11.8%
> for confirmed frauds. Graph evidence can't be the classifier — it's the thing
> that's decisive when it fires.
>
> So we measured that instead. Replaying the agent over closed cases with known
> outcomes, with everything after each alert clamped off, it caught all nine
> undocumented-typology frauds in the dataset from graph structure alone, with
> zero false fraud verdicts on 300 high-scoring legitimate alerts at the exam's
> operating point.
>
> We publish no accuracy figure for the 20 exam cases — we don't have the key.
> We do publish the AUC we had to withdraw after finding a leak in our own
> backtest.
>
> Built on TigerGraph Savanna, with the query catalogue also running through
> TigerGraph MCP. Free tiers throughout.
>
> Repo: https://github.com/AKRai-2005/fraudgraph
> Console: https://akrai-2005.github.io/fraudgraph/
>
> #TigerGraph #GraphDatabase #FraudDetection #AIAgents

---

## X / Twitter — member 2

> Our HHGoa submission on @TigerGraphDB: nine closed cases in the dataset are
> labelled "undocumented". Reading the analyst notes, they're two patterns —
> a shared-device ring and sub-threshold structuring, purchases priced just
> under the authorisation limit.
>
> Both are graph traversals, not model features.
>
> https://github.com/AKRai-2005/fraudgraph @247pmstudio

## LinkedIn — member 2

> We spent the TigerGraph × Hacker House Goa task building something that
> refuses to guess.
>
> Our agent runs 9 detectors over graph evidence, and every one of them states
> what it *cannot* rule out. Actions are routed by a written policy: the agent
> may monitor a card by itself, but blocking one needs a named human approver
> and filing a regulatory report needs a second level. Nothing reaches a real
> financial system — every execution is simulated and labelled as such.
>
> The part I'd want a reviewer to look at is the backtest. Replaying an agent
> over historical cases is easy to get wrong: our first version leaked, because
> it time-boxed case memory but not transaction windows, and a replayed alert
> could see cards compromised after its own investigation opened. Fixing it
> moved one of our numbers from 0.718 to 0.691 — so we withdrew the 0.718 in
> writing.
>
> Repo: https://github.com/AKRai-2005/fraudgraph
>
> #TigerGraph #GraphAnalytics #FraudDetection

---

## X / Twitter — member 3

> Built on @TigerGraphDB for HHGoa: one query catalogue, three backends —
> installed GSQL, TigerGraph MCP, and a local mirror — all answering the same
> 20 cases.
>
> They agreed on every verdict while disagreeing on the evidence underneath.
> Three bugs we'd never have found by checking answers alone.
>
> https://github.com/AKRai-2005/fraudgraph @247pmstudio

## LinkedIn — member 3

> Something I learned building our TigerGraph × Hacker House Goa submission:
> test the evidence, not just the answer.
>
> We implemented one catalogue of 17 graph queries three times — installed GSQL
> over pyTigerGraph, the same queries through TigerGraph's MCP server, and a
> pandas mirror for offline testing — and ran all 20 cases through each. Every
> verdict matched. The evidence underneath didn't: different quantile
> conventions, different tie-breaking between equally-scored prior cases, and
> an unsorted subset of a device ring. Three real bugs, invisible if you only
> compare conclusions.
>
> The console that displays all this is covered by 45 browser tests, including
> WCAG AA contrast on every piece of text it renders, in both themes.
>
> Repo: https://github.com/AKRai-2005/fraudgraph
>
> #TigerGraph #MCP #SoftwareTesting
