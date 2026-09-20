# Architecture

## The shape of one investigation

```
                     ┌──────────────────────────────────────────────┐
  trigger ──────────▶│ InvestigationAgent.investigate()             │
  (risk_score |      │                                              │
   customer_report |  │  1 trigger intake        → investigation id  │
   analyst_request)  │  2 baseline retrieval     → txn, profile,     │
                     │                             ±72h, ±180d       │
                     │  3 targeted retrieval     → only what this    │
                     │                             alert makes       │
                     │                             relevant          │
                     │  4 agent-planned retrieval→ LLM proposals,    │
                     │                             validated         │
                     │  5 pattern analysis       → 9 detectors       │
                     │  6 risk + uncertainty     → 6 separate        │
                     │                             quantities        │
                     │  7 case memory            → prior cases, with │
                     │                             reasons           │
                     │  8 policy engine          → initial actions   │
                     │  9 evidence request       → simulated, stated │
                     │ 10 reassess + policy      → final actions     │
                     │ 11 stopping rule          → stop_reason       │
                     │ 12 narrative + persist    → summary, SAR,     │
                     │                             graph write       │
                     └──────────────────────────────────────────────┘
```

Each numbered step appends to a timeline and the graph store's ledger, which
together are the audit trail the dashboard shows and the answer file counts.

## Components

### `graph/` — one catalogue, three backends

`graph/queries.py` is the contract: 17 named queries with declared parameters
and a stated purpose. Three backends implement it:

* `graph/tigergraph.py` — installed GSQL queries, the system of record
* `graph/mcp_backend.py` — the same queries through the official
  `tigergraph-mcp` server, so the agent's graph access is genuine MCP tool use
* `graph/local_mirror.py` — pandas over the parquet cache

All three have been run over the full 20-case pack and produce identical
answers. That is the point of having them: a finding that survives three
independent code paths is not an artefact of one.

`graph/store.py` dispatches by name, records every call (name, params, backend,
duration, ok/error, result summary) and turns a query into the `ref` string that
appears in evidence. A failing call returns `{"error": ...}` and is recorded as
a gap; it never raises into the investigation and never reads as evidence.

Having three implementations of one contract is not redundancy for its own
sake: it lets the whole investigation be unit-tested without a database, keeps
development moving when a cloud workspace is asleep, and gives an independent
check on graph results. The answer files record which backend served each
investigation, and `write_case` on the mirror deliberately returns
`written=False` so a case can never claim graph persistence it did not get.

### `analysis/` — what the evidence means

* `features.py` — 50-odd typed signals, each a sentence an analyst could say.
  Nothing here calls an LLM.
* `patterns.py` — nine detectors: the five documented typologies, the two
  undocumented ones, and two *exculpatory* detectors (`recurring_charge`,
  `consistent_with_history`) that argue for legitimate activity. Every detector
  returns a strength, the transactions it thinks form the episode, and an
  explicit statement of what it cannot rule out.
* `risk.py` — combines detector strengths into a probability, and keeps the six
  quantities the challenge asks to be separated apart.
* `calibrate.py` — fits and cross-validates the combination on the closed cases.
  See `RISK_MODEL.md`; the interesting part is what it refuses to fit.

### `policy/` — Fraud Policy v1.0 as testable code

`rules.py` holds the action catalogue, the routing table and the thresholds as
data. `engine.py` maps an evidence state to ordered actions with routes and rule
citations. `actions.py` is a mock service that writes an append-only audit log
and **raises** if asked to execute an `L1`/`L2` action without an approver.

The engine is a pure function of a `DecisionState`. That is what makes the 27
policy tests possible, and it is why the LLM cannot reach the routing table.

### `agent/` — orchestration

* `orchestrator.py` — the state machine above.
* `evidence_requests.py` — controlled requests with **simulated** responses
  under a published rule (challenge provides no replies).
* `narrative.py` — deterministic summary and SAR text; an LLM may rewrite them,
  and the rewrite is rejected if it introduces an unsupported id, amount or date.
* `llm.py` — Gemini/Anthropic/null providers, plus catalogue-validated planning.

### `memory/` — case memory

Retrieval is explainable: each returned prior case carries `why_retrieved`.
Cases the agent closes are written to TigerGraph as `AgentCase` vertices with
`CaseEvidence` children and edges to the transactions, cards, devices and prior
cases involved — so the next investigation can find them. A local JSONL journal
always records what was attempted, whether or not the graph accepted it.

## Graph schema

Vertices: `Customer`, `PaymentCard`, `Transaction`, `DeviceProfile`,
`BillingRegion`, `EmailDomain`, `ClosedCase`, `AgentCase`, `CaseEvidence`,
`FraudPattern`, `PolicyRule`.

The card vertex is `PaymentCard`, not `Card`: vertex types are global in
TigerGraph, and a Savanna workspace that has run the shipped `Transaction_Fraud`
sample already owns a global `Card`.

Edges: `OWNS`, `MADE`, `FROM_DEVICE`, `BILLED_IN`, `PURCHASER_EMAIL`,
`RECIPIENT_EMAIL`, `NEXT_TXN`, `INVOLVES`, `ON_CARD`, `CONNECTED_TO`,
`CASE_INVESTIGATES`, `CASE_ON_CARD`, `CASE_CONNECTED_TO`,
`CASE_CONTAINS_EVIDENCE`, `CASE_MATCHES_PATTERN`, `CASE_FROM_DEVICE`,
`CASE_CITES_PRIOR`, `CASE_APPLIES_RULE`.

`ClosedCase` and `AgentCase` are separate types on purpose: historical ground
truth and the agent's own conclusions must never be confusable, in the graph or
in an answer file.

The pivot that matters is two hops:

```
Transaction ──FROM_DEVICE──▶ DeviceProfile ──DEVICE_USED_BY──▶ Transaction ──MADE_BY──▶ PaymentCard
```

That is how a $74.96 purchase scored 0.05 by the bank's model becomes a
28-card ring.

## Where the LLM is, and is not

| Decision | Made by |
|---|---|
| which baseline queries run | code |
| which *additional* queries run | LLM proposes, catalogue validates, code executes |
| whether a pattern matched | code |
| fraud probability | code |
| verdict, exposure, affected transactions | code |
| recommended actions and approval routes | code |
| whether a SAR is due | code |
| case summary wording | LLM, validated, template fallback |
| SAR narrative wording | LLM, validated, template fallback |

With no API key every row above still works; only the wording changes.
