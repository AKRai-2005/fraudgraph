# Social posts

The form asks every team member to post on any platform, tagging
**@TigerGraphDB** and **@247pmstudio**, and to paste every link.

Check both handles resolve on the platform before posting. LinkedIn uses
company pages rather than @handles: type `@TigerGraph` and `@247 PM Studio`
and pick from the dropdown, or the tag is just text.

Links used below:

* video — <https://youtu.be/eJTAnfc5e1g>
* repo — <https://github.com/AKRai-2005/fraudgraph>
* console — <https://akrai-2005.github.io/fraudgraph/>
* write-up — <https://dev.to/ashutosh_kumarrai_6335bf/our-fraud-classifier-scored-0963-auc-we-threw-it-away-1cl5>

X counts any link as 23 characters; the counts below are already adjusted and
all fit 280 without X Premium.

---

## X — team lead (274 characters)

> The bank's model scored this alert 0.05.
>
> Two hops in the graph — payment → device → other cards — and that device sits
> on 28 unrelated cards. One profile out of 9,706.
>
> Our agent: fraud, 0.98.
>
> Built on @TigerGraphDB for the @247pmstudio HHGoa task:
> https://youtu.be/eJTAnfc5e1g

## LinkedIn — team lead

> **A risk score is a reason to look, never a verdict.**
>
> That line from the challenge README shaped everything we built for the
> TigerGraph × Hacker House Goa task: an agent that investigates a fraud alert
> against a knowledge graph of 590,742 transactions and works out what kind of
> fraud it is, how far it goes, and what the bank should do — with the graph
> query behind every single claim.
>
> In the demo, an alert the bank's own model scored 0.05. Two hops later —
> transaction → device profile → other cards — that device turns out to sit on
> 28 unrelated cards in a month, always behind an anonymising proxy. Exactly
> one profile out of 9,706 in the dataset meets that test. The agent closes it
> as fraud at 0.98, cites the policy rule that told it to stop, and then waits:
> blocking a card needs a named human approver, and every execution is
> simulated.
>
> Across the 20 exam alerts, graph evidence moved the verdict away from the
> bank's score 18 times — 9 escalated, 9 cleared. Half of those alerts are
> legitimate, so refusing to act is as much of the job as catching anything.
>
> We claim no accuracy on those 20: the answer key is withheld. What we do
> publish is a replay over 5,565 closed investigations that have outcomes,
> with every forward-looking window clamped to the moment each alert opened.
>
> 3-minute demo: https://youtu.be/eJTAnfc5e1g
> Code: https://github.com/AKRai-2005/fraudgraph
>
> Thanks to @TigerGraph and @247 PM Studio for the task.
>
> #TigerGraph #GraphDatabase #FraudDetection #AIAgents #GSQL

---

## X — member 2 (273 characters)

> Nine closed cases in our HHGoa dataset were labelled only "undocumented".
>
> Reading the analyst notes: two patterns. A shared-device ring, and
> purchases priced just under the authorisation limit.
>
> Both are graph traversals. @TigerGraphDB @247pmstudio
> https://youtu.be/eJTAnfc5e1g

## LinkedIn — member 2

> We spent the TigerGraph × Hacker House Goa task building something that
> refuses to guess.
>
> Nine detectors run on every case, and each one states what it *cannot* rule
> out. Actions are routed by a written policy: the agent may monitor a card by
> itself, but blocking one needs a named approver and filing a regulatory
> report needs a second level. Nothing touches a real financial system — every
> execution is simulated and labelled as such.
>
> The part I'd want a reviewer to look at is the backtest. Replaying an agent
> over historical cases is easy to get wrong, and our first version leaked: it
> time-boxed case memory but not transaction windows, so a replayed alert could
> see cards compromised after its own investigation had opened. Fixing it moved
> one of our numbers from 0.718 to 0.691 — so we withdrew the 0.718 in writing
> rather than quietly keeping it.
>
> Demo: https://youtu.be/eJTAnfc5e1g
> Code: https://github.com/AKRai-2005/fraudgraph
>
> Thanks @TigerGraph and @247 PM Studio.
>
> #TigerGraph #GraphAnalytics #FraudDetection #MachineLearning

---

## X — member 3 (260 characters)

> One query catalogue, three backends: installed GSQL, @TigerGraphDB's MCP
> server, and a local mirror. All 20 cases run through each.
>
> They agreed on every verdict — and disagreed on the evidence underneath.
> Three bugs found. @247pmstudio
> https://youtu.be/eJTAnfc5e1g

## LinkedIn — member 3

> Lesson from our TigerGraph × Hacker House Goa build: test the evidence, not
> just the answer.
>
> We implemented one catalogue of 17 graph queries three times — installed GSQL
> over pyTigerGraph, the same queries through TigerGraph's MCP server, and a
> pandas mirror for offline testing — then ran all 20 challenge cases through
> each one. Every verdict matched. The evidence underneath didn't: different
> quantile conventions, different tie-breaking between two equally-scored prior
> cases, and an unsorted subset of a device ring. Three real bugs that comparing
> conclusions would never have surfaced.
>
> The analyst console on top of it is covered by 45 browser tests, including
> WCAG AA contrast on every piece of text it renders, in both themes.
>
> Demo: https://youtu.be/eJTAnfc5e1g
> Code: https://github.com/AKRai-2005/fraudgraph
>
> Thanks @TigerGraph and @247 PM Studio for the challenge.
>
> #TigerGraph #MCP #SoftwareTesting #GraphDatabase

---

## If you are a team of one

Post the lead's X and LinkedIn versions and paste both links into the form.
Do not post all three from one account — they read as filler, and the form
asks for one post per member, not three per person.
