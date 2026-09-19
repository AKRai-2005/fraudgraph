# Submission checklist

Status is recorded honestly: a box is only ticked when the thing has been run
and its output inspected. Regenerate the evidence with the commands shown.

## Data and graph

| Item | Status | Evidence |
|---|---|---|
| Dataset README read in full before designing ingestion | done | `docs/DATA_NOTES.md` records what was verified |
| Ingestion implemented and validated | done | `build/ingest_quality_report.json` — 0 errors, 0 warnings, 590,742 / 144,432 / 5,565 rows match the README |
| Card identity recovered rather than assumed | done | 1913/1913 closed-case and 20/20 case-pack card ids reproduced; `python scripts/probe_card_key.py` |
| Ingestion repeatable without duplicating the graph | done | parquet cache is rebuilt from source; TigerGraph loads upsert by primary id |
| Graph schema designed from the actual data | done | `src/fraudgraph/graph/gsql/schema.gsql` |
| GSQL queries implemented | done | `src/fraudgraph/graph/gsql/queries.gsql` (17 queries) |
| TigerGraph connected and loaded | **pending credentials** | `python -m fraudgraph.ingest.tg_load --all` once `.env` is filled |
| Graph traversal contributes actual evidence | done | `tests/test_integration.py::test_device_neighbors_traverses_to_other_cards` |

## Agent

| Item | Status | Evidence |
|---|---|---|
| Triggers work (risk score, customer report, analyst request, manual) | done | all three trigger types in the pack run; ad-hoc investigation via the console |
| Evidence gathering is targeted, not exhaustive | done | 9–13 graph calls per case, planned from what the alert makes relevant |
| Fraud patterns assessed (5 documented + 2 undocumented) | done | `tests/test_detectors.py` (18 tests) |
| Risk and uncertainty kept separate | done | `RiskAssessment` holds six distinct quantities |
| Additional evidence can be requested | done | `evidence_requests` with stated simulated responses |
| Recommendations change when evidence changes | done | `tests/test_scenarios.py::test_scenario_recommendation_changes_when_the_customer_denies` and `..._reverses_when_the_customer_confirms` |
| Stopping conditions work | done | `tests/test_scenarios.py::test_scenario_stopping_rules` |
| Decisions explained and recorded | done | timeline + tool ledger persisted per case in `build/case_records/` |
| LLM integrated for reasoning and narrative | **pending credentials** | runs on templates without a key; `FG_LLM_PROVIDER=gemini` + `GEMINI_API_KEY` turns it on |
| TigerGraph MCP integration | **not done** | see note below |

## Policy and cases

| Item | Status | Evidence |
|---|---|---|
| Actual fraud policy incorporated | done | `src/fraudgraph/policy/rules.py`, rules R1–R10 + 3a + 6 |
| Unauthorised actions prevented | done | `tests/test_policy.py::test_mock_service_refuses_unapproved_human_action` |
| Approval requirements enforced | done | 27 policy tests; routing table asserted against the README |
| Cases created and progressed | done | initial → evidence request → final, per case |
| Case memory persists | partial | local JSONL journal always; TigerGraph pending credentials |
| Historical cases inform new investigations | done | `similar_prior_cases` with `why_retrieved` |
| Audit records maintained | done | `build/case_records/*.json`, `build/action_audit_log.jsonl` |

## Frontend

| Item | Status |
|---|---|
| Overview with real statistics | done |
| Investigation queue with filter/sort | done |
| Case workspace | done |
| Evidence and graph relationships visible | done |
| Recommendations and approval requirements visible | done |
| Agent activity and case history visible | done |
| Approval controls that record a decision | done |
| Responsive and usable | done |

## Evaluation

| Item | Status | Command |
|---|---|---|
| Unit tests | done | `python -m pytest tests/test_policy.py tests/test_detectors.py` |
| Integration tests | done | `python -m pytest tests/test_integration.py` |
| Scenario tests | done | `python -m pytest tests/test_scenarios.py` |
| Failure-mode tests | done | `python -m pytest tests/test_failures.py` |
| All 20 benchmark cases processed | done | `python -m fraudgraph.benchmark.run` |
| Output files validated | done | `python -m fraudgraph.benchmark.validate` |
| Measured results documented | done | `docs/RISK_MODEL.md`, `build/risk_model.json` |
| Accuracy against the answer key | **not possible** | we do not have the key; no estimate is published |

## Deliverables

| Item | Status |
|---|---|
| Working fraud investigation agent | done |
| GitHub repository | **to do** — `git init` done, needs a remote |
| 20 answer files in `cases/` | done |
| Complete case records | done (`build/case_records/`) |
| Cases written to TigerGraph | **pending credentials** |
| SARs where policy requires | done |
| Next-best action before and after evidence | done |
| 3–5 minute demo video | **to do** — script in `docs/DEMO.md` |
| Technical blog post | **to do** — outline in `docs/BLOG_OUTLINE.md` |
| Social post tagging @TigerGraphDB | **to do** — draft in `docs/BLOG_OUTLINE.md` |

## Note on TigerGraph MCP

The challenge asks for TigerGraph MCP so the agent can call the graph as tools.
This implementation exposes the graph to the agent through a named query
catalogue with validated parameters and a call ledger — the same shape MCP
provides — but it does **not** currently speak the MCP protocol. Wiring
`tigergraph-mcp` in front of the existing `GraphStore` is a contained change
(the catalogue is already the tool surface), and it is listed here as
outstanding rather than quietly claimed.
