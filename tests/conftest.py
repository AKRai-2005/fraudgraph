"""Shared fixtures.

The important one is ``isolated_records``. Tests that exercise the console
re-run investigations, record approvals and trigger simulated executions --
and every one of those writes state the demo reads back:

* ``build/case_records/``  -- the records the console renders
* ``build/approvals.json`` -- who approved what
* ``build/action_audit_log.jsonl`` -- simulated executions
* ``build/agent_cases.jsonl`` -- the journal the Memory view counts

Each of these was found polluted by tests at some point: the overview dropped
from 20 cases in the graph to 18 because two records had been replaced by
local-mirror re-runs; the audit log was 143 entries of test residue and no
real approvals; and the journal made the Memory view report 850 agent cases
and 12 in TigerGraph, where there are 20 and 20. So all four are redirected to
a temporary copy for the whole module. The name is kept for the tests that
already depend on it; it isolates all four, and yields the records directory.
"""
from __future__ import annotations

import dataclasses
import shutil

import pytest

from fraudgraph.config import PATHS


def _redirect(mp, root, records, journal_dir, approvals, audit):
    import fraudgraph.api.service as service_mod
    import fraudgraph.benchmark.run as run_mod
    import fraudgraph.memory.case_memory as memory_mod
    from fraudgraph.api.service import CaseService
    from fraudgraph.policy.actions import MockActionService

    mp.setattr(service_mod, "PATHS", dataclasses.replace(service_mod.PATHS, records=records))
    # The benchmark runner also writes the answer files and its summary. A test
    # of a crashing run rewrote build/benchmark_summary.json on every suite run,
    # and a run that did not crash would have rewritten cases/ -- the deliverable.
    (root / "build").mkdir(exist_ok=True)
    (root / "cases").mkdir(exist_ok=True)
    mp.setattr(run_mod, "PATHS", dataclasses.replace(
        run_mod.PATHS, records=records, build=root / "build", cases_out=root / "cases"))
    # the journal is the only thing case_memory keeps under build/
    mp.setattr(memory_mod, "PATHS", dataclasses.replace(memory_mod.PATHS, build=journal_dir))
    mp.setattr(CaseService, "approvals_path", property(lambda self: approvals))
    mp.setattr(MockActionService, "log_path", property(lambda self: audit))


@pytest.fixture(scope="session", autouse=True)
def _no_test_writes_demo_state(tmp_path_factory):
    """Every test, by default, writes to an empty scratch copy of all four files.

    Opt-in isolation leaked: the fixture below covered the console tests, but
    five other test files build an InvestigationAgent directly, and every
    investigation journals the case it closes. A default that has to be
    remembered is not a default, so this one is automatic.
    """
    root = tmp_path_factory.mktemp("scratch_state")
    (root / "case_records").mkdir()
    (root / "journal").mkdir()
    (root / "approvals.json").write_text("{}", encoding="utf-8")
    mp = pytest.MonkeyPatch()
    _redirect(mp, root, root / "case_records", root / "journal",
              root / "approvals.json", root / "action_audit_log.jsonl")
    try:
        yield root
    finally:
        mp.undo()


@pytest.fixture(scope="module")
def isolated_records(tmp_path_factory):
    """Redirect every file the console writes to a temp copy for the module.

    ``PATHS`` is a frozen dataclass imported by name into several modules, so
    each of them is repointed; ``dataclasses.replace`` keeps every other path
    -- the parquet cache, the risk model, the backtest -- exactly where it is.
    """
    root = tmp_path_factory.mktemp("console_state")
    records = root / "case_records"
    records.mkdir()
    if PATHS.records.exists():
        for src in PATHS.records.glob("*.json"):
            shutil.copy2(src, records / src.name)
    journal_dir = root / "journal"
    journal_dir.mkdir()
    if (PATHS.build / "agent_cases.jsonl").exists():
        shutil.copy2(PATHS.build / "agent_cases.jsonl", journal_dir / "agent_cases.jsonl")
    approvals = root / "approvals.json"
    approvals.write_text(
        (PATHS.build / "approvals.json").read_text(encoding="utf-8")
        if (PATHS.build / "approvals.json").exists() else "{}",
        encoding="utf-8",
    )
    audit = root / "action_audit_log.jsonl"

    mp = pytest.MonkeyPatch()
    _redirect(mp, root, records, journal_dir, approvals, audit)
    try:
        yield records
    finally:
        mp.undo()
