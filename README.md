# Agentic Fraud Investigation on TigerGraph

An investigation agent for the TigerGraph × Hacker House Goa challenge (IEEE-CIS
edition). It takes a fraud alert, investigates it against a knowledge graph of
590,742 transactions, decides what kind of fraud it is — if any — how far it
goes, and what the bank should do next under the challenge's Fraud Policy v1.0.

It is built around one conviction, which the dataset README states and the data
confirms: **a risk score is a reason to look, never a verdict.** Half the exam
cases are legitimate and most of them look suspicious, so the hard part is not
detecting anomalies — it is refusing to act on the ones that do not hold up.

---

## What it does

```
alert ──▶ baseline retrieval ──▶ targeted retrieval ──▶ 9 pattern detectors
                                        │                       │
                                 (LLM may add queries)           ▼
                                                        risk + uncertainty
                                                                │
   case memory ◀── write case ◀── policy engine ◀── evidence request ◀──┘
   (TigerGraph)                   (R1..R10, routes)   (simulated, stated)
```

* **Graph-grounded.** Every claim in an answer file carries the graph query that
  produced it and the entity ids it rests on. `query:device_neighbors(...)` in
  an answer is a query that actually ran; a test asserts it.
* **Deterministic where it matters.** The verdict, the probability, the pattern,
  the exposure, the actions, the approval routes and the SAR decision are
  computed by code. The LLM proposes extra retrieval and rewrites prose, and its
  rewrites are rejected if they introduce an id, amount or date that is not
  already in the retrieved evidence.
* **Honest about what it did not do.** Nothing reaches a real financial system;
  `L1`/`L2` actions are recorded as awaiting approval and never executed by the
  agent. Customer replies are simulated under a published rule and labelled as
  such. `written_to_graph` is set from what the graph accepted, never assumed.

## Two fraud typologies the challenge does not document

The nine `undocumented` closed cases are **two** distinct patterns, read out of
their analyst notes and implemented as detectors that key on behaviour, not on
case ids:

1. **Coordinated shared-device ring.** One device fingerprint — always behind an
   anonymising proxy, marked `New` for every account it touches — spread across
   many unrelated cards. In this dataset exactly one profile out of 9,706 meets
   that test, and it carries 52 cards.
2. **Sub-threshold structuring.** Several online purchases inside one short
   window, each priced just under a round authorisation threshold, totalling far
   more than it.

Both are found by graph traversal from the flagged transaction. Details and the
evidence in `docs/DATA_NOTES.md`.

---

## Quick start

```bash
pip install -r requirements.txt
pip install -e .          # puts fraudgraph on the path; every `python -m
                          # fraudgraph...` command below needs it
```

Point the code at the dataset (it already defaults to `data/raw/`):

```bash
cp .env.example .env     # then fill in credentials, all optional to start
```

Build the graph, end to end:

```bash
python -m fraudgraph.ingest.prepare      # 708 MB CSV -> parquet cache
python -m fraudgraph.ingest.entities     # derive cards, devices, regions; validate
python -m fraudgraph.ingest.tg_export    # write load-ready CSVs for TigerGraph
```

Fit the risk model on the closed cases and run the exam:

```bash
python -m fraudgraph.analysis.calibrate  # fit + cross-validate, writes a model card
python -m fraudgraph.benchmark.run       # writes cases/HHG-0NN.json
python -m fraudgraph.benchmark.validate  # checks all 20 against the Answer Format
```

Open the console:

```bash
python run_api.py        # http://127.0.0.1:8077
```

It opens on the chart below, which is the argument for the whole project:
where the bank's risk score and the agent's assessment part company. On the
20 exam alerts they disagree on 18 — nine escalated by graph evidence, nine
cleared by it. Pick a case and press **Watch it investigate** to see the
agent work: each reasoning step and each graph query streams in as it runs,
with the time it really took.

Run the tests:

```bash
python -m pytest -q                       # 310 tests
python -m pytest -q -m browser            # the 45 that drive the console in Chromium
python scripts/compare_backends.py local local      # determinism
python scripts/compare_backends.py local tigergraph # cross-backend agreement
python -m fraudgraph.analysis.backtest --all-modes  # are the verdicts right?
```

`tests/test_frontend.py` starts the console on a free port and drives it in
headless Chromium: every tab, filter, sort and route into a case, approve and
reject, the live stream, the graph, the theme, no sideways scrolling at
eight widths from 320 to 1920px, and WCAG AA contrast for every run of text
it renders, in both themes. It needs `pip install playwright` and
`python -m playwright install chromium`, and skips itself without them.
`tests/test_design_tokens.py` checks the colour tokens themselves and needs
no browser.

The last one is the important one, and it needs a running workspace. The
three backends are meant to be interchangeable; checking only the verdicts
hid three bugs that changed the *evidence* underneath. All three pairings
now agree field for field on all 20 cases — see
[`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) for what differs and why.

Everything above works with **no credentials at all** — the local mirror serves
the same query catalogue and the narrative falls back to templates. Credentials
turn on the two mandatory integrations, below.

## Connecting TigerGraph

Create a free workspace at <https://savanna.tgcloud.io> (or install Community
Edition), then put the connection details in `.env`:

Savanna authenticates tools with a **database secret**, not a password:

```
TG_HOST=https://<workspace>.i.tgcloud.io   # Workspaces -> your workspace -> its URL
TG_SECRET=<database secret>                # Database Secrets -> Create Secret
TG_GRAPH=FraudInvestigation
```

The secret is passed to pyTigerGraph as `gsqlSecret`, so GSQL DDL (schema,
loading jobs, installing queries) authenticates with it too, and to
`tigergraph-mcp` as `TG_SECRET`. Username/password is the self-hosted
Community Edition path.

```bash
python -m fraudgraph.ingest.tg_load --schema
python -m fraudgraph.ingest.tg_load --job
python -m fraudgraph.ingest.tg_load --data      # ~90 MB of CSV
python -m fraudgraph.ingest.tg_load --queries
python -m fraudgraph.ingest.tg_load --check
```

Then `FG_GRAPH_BACKEND=tigergraph` makes it the system of record, or
`FG_GRAPH_BACKEND=mcp` routes the same queries through the official
`tigergraph-mcp` server instead (`pip install tigergraph-mcp`). Both have been
run over the full case pack and give identical answers. The console's
status strip always shows which backend answered, and the case records in
`build/case_records/` record it for every graph call.

## Write-up

The technical write-up — what the 0.963 AUC actually measured, the backtest,
and the number we withdrew: <https://dev.to/ashutosh_kumarrai_6335bf/our-fraud-classifier-scored-0963-auc-we-threw-it-away-1cl5>

## Deploying it

A static export of the console is published on GitHub Pages:
<https://akrai-2005.github.io/fraudgraph/>. `scripts/export_static.py` freezes
every GET response to a file and the page reads those instead of a server, so
the 20 cases, their evidence and graphs, case memory, the model card and the
backtest are all browsable. Live investigation and approvals need the agent
running, and the page says so. To run the container version on a host that has
one, see [`docs/DEPLOY.md`](docs/DEPLOY.md).

## Connecting the LLM

Gemini's free tier is enough:

```
FG_LLM_PROVIDER=gemini
FG_LLM_MODEL=gemini-flash-lite-latest
GEMINI_API_KEY=<key from https://aistudio.google.com/apikey>
```

With no key the system runs on deterministic templates and reports
`tokens: 0` honestly.

---

## Layout

| Path | What it is |
|---|---|
| `src/fraudgraph/ingest/` | CSV → parquet → derived entities → TigerGraph load |
| `src/fraudgraph/graph/` | query catalogue, GSQL, TigerGraph backend, local mirror |
| `src/fraudgraph/analysis/` | features, 9 detectors, risk model, calibration |
| `src/fraudgraph/policy/` | Fraud Policy v1.0 as data, the engine, mock actions |
| `src/fraudgraph/agent/` | orchestrator, evidence requests, narrative, LLM |
| `src/fraudgraph/memory/` | case retrieval and persistence |
| `src/fraudgraph/api/` | FastAPI service behind the console |
| `src/fraudgraph/benchmark/` | the 20-case runner and the format validator |
| `frontend/` | the analyst console (vanilla JS, no CDN, works offline) |
| `cases/` | the deliverable: 20 answer files |
| `docs/` | data notes, architecture, risk model, limitations |

## Documentation

* [`docs/DATA_NOTES.md`](docs/DATA_NOTES.md) — what was verified in the data,
  including how the `-K1` card ids were recovered
* [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — components and the flow
* [`docs/RISK_MODEL.md`](docs/RISK_MODEL.md) — the model card, and the two
  places where fitting the closed cases naively goes badly wrong
* [`docs/BACKTEST.md`](docs/BACKTEST.md) — the agent replayed over closed
  cases with known outcomes: 9 of 9 relational frauds caught from graph
  evidence alone, zero false fraud calls on 300 hard negatives, a detector it
  found broken and how that was rebuilt, and a number it had to withdraw
* [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) — what this does not do
* [`docs/SUBMISSION.md`](docs/SUBMISSION.md) — deliverable checklist
