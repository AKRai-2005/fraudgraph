"""Shared fixtures.

The important one is ``isolated_records``. Several tests exercise the live
re-run and streaming paths, which by design write an investigation record --
and those tests run on the local mirror, so the record they write reports
``written_to_graph: false``.

Pointed at the real ``build/case_records/``, that meant **running the test
suite silently degraded the console**: the overview dropped from 20 cases
written to the graph to 18, because two cases had been quietly replaced by
local-mirror re-runs. The numbers were honest about the records on disk; the
records were the tests' leftovers.

So the tests get their own records directory, seeded from the real one.
"""
from __future__ import annotations

import dataclasses
import shutil

import pytest

from fraudgraph.config import PATHS


@pytest.fixture(scope="module")
def isolated_records(tmp_path_factory):
    """Redirect investigation records to a temp copy for the whole module.

    ``PATHS`` is a frozen dataclass imported by name into several modules, so
    every one of them has to be repointed; ``dataclasses.replace`` keeps the
    rest of the paths identical.
    """
    import fraudgraph.api.service as service_mod
    import fraudgraph.benchmark.run as run_mod

    target = tmp_path_factory.mktemp("case_records")
    if PATHS.records.exists():
        for src in PATHS.records.glob("*.json"):
            shutil.copy2(src, target / src.name)

    patched = dataclasses.replace(PATHS, records=target)
    originals = {}
    for mod in (service_mod, run_mod):
        originals[mod] = mod.PATHS
        mod.PATHS = patched
    try:
        yield target
    finally:
        for mod, original in originals.items():
            mod.PATHS = original
