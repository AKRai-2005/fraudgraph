"""Backend-agnostic access to the graph, with an audit ledger.

Every call is recorded: query name, parameters, which backend served it, how
long it took, and whether it succeeded.  That ledger is what the answer file's
``tool_calls`` count and the dashboard's agent-activity panel are built from,
and it is what makes an evidence ``ref`` verifiable.
"""
from __future__ import annotations

import time
from typing import Any, Protocol

from ..config import RUNTIME, TG
from ..schemas import ToolCall
from .queries import spec


class GraphBackend(Protocol):  # pragma: no cover - structural type
    name: str

    def ping(self) -> dict: ...


class GraphUnavailable(RuntimeError):
    pass


def _summarise(name: str, result: Any) -> str:
    if not isinstance(result, dict):
        return type(result).__name__
    for key in ("n", "n_cards", "n_txns"):
        if key in result:
            bits = [f"{k}={result[k]}" for k in ("n", "n_cards", "n_txns", "n_customers")
                    if k in result]
            return ", ".join(bits)
    if "transactions" in result:
        return f"{len(result['transactions'])} transactions"
    if "cases" in result:
        return f"{len(result['cases'])} cases"
    if result.get("found") is False:
        return "not found"
    return f"{len(result)} fields"


class GraphStore:
    """Dispatches named queries to a backend and logs every call."""

    def __init__(self, backend: GraphBackend | None = None, prefer: str | None = None):
        self.ledger: list[ToolCall] = []
        self._step = 0
        self.backend = backend or self._resolve(prefer or RUNTIME.graph_backend)

    # ------------------------------------------------------------ resolution
    @staticmethod
    def _resolve(prefer: str) -> GraphBackend:
        from .local_mirror import get_local_backend

        if prefer == "local":
            return get_local_backend()
        if prefer == "mcp":
            from .mcp_backend import MCPGraphBackend

            be = MCPGraphBackend()
            health = be.ping()
            if not health.get("ok"):
                raise GraphUnavailable(
                    f"TigerGraph MCP backend requested but not usable: {health.get('error')}"
                )
            return be
        if prefer in ("tigergraph", "auto"):
            try:
                from .tigergraph import TigerGraphBackend

                be = TigerGraphBackend()
                if be.ping().get("ok"):
                    return be
            except Exception as exc:  # noqa: BLE001 - any failure falls back
                if prefer == "tigergraph":
                    raise GraphUnavailable(
                        f"TigerGraph backend requested but unavailable: {exc}"
                    ) from exc
            if prefer == "auto":
                return get_local_backend()
        return get_local_backend()

    @property
    def backend_name(self) -> str:
        return getattr(self.backend, "name", "unknown")

    @property
    def is_tigergraph(self) -> bool:
        return self.backend_name == "tigergraph"

    # ----------------------------------------------------------------- call
    def call(self, name: str, **params) -> dict:
        """Run a catalogue query, logging it. Failures are recorded, not raised.

        A tool failure must never read as evidence either way, so a failed call
        returns ``{"error": ...}`` and the orchestrator records the gap.
        """
        qs = spec(name)
        self._step += 1
        ref = qs.ref(**params)
        fn = getattr(self.backend, name, None)
        t0 = time.perf_counter()
        if fn is None:
            self.ledger.append(ToolCall(
                step=self._step, name=name, params=params, backend=self.backend_name,
                ref=ref, ok=False, error=f"backend {self.backend_name} does not implement {name}",
                duration_ms=0.0, result_summary="unavailable",
            ))
            return {"error": f"query {name} not available on backend {self.backend_name}"}
        try:
            result = fn(**params)
            ok, err = True, None
        except Exception as exc:  # noqa: BLE001 - surfaced as a recorded gap
            result, ok, err = {"error": str(exc)}, False, f"{type(exc).__name__}: {exc}"
        dt = (time.perf_counter() - t0) * 1000.0
        self.ledger.append(ToolCall(
            step=self._step, name=name, params={k: str(v)[:120] for k, v in params.items()},
            backend=self.backend_name, ref=ref, ok=ok, error=err, duration_ms=round(dt, 2),
            result_summary=_summarise(name, result) if ok else (err or "failed"),
        ))
        return result

    def ref(self, name: str, **params) -> str:
        return spec(name).ref(**params)

    @property
    def call_count(self) -> int:
        return len(self.ledger)

    def failures(self) -> list[ToolCall]:
        return [t for t in self.ledger if not t.ok]

    def reset(self) -> None:
        self.ledger.clear()
        self._step = 0

    def health(self) -> dict:
        try:
            h = dict(self.backend.ping())
        except Exception as exc:  # noqa: BLE001
            h = {"ok": False, "error": str(exc)}
        h["backend"] = self.backend_name
        h["tigergraph_configured"] = TG.configured
        return h
