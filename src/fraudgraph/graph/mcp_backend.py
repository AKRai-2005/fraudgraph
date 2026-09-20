"""TigerGraph MCP backend.

Reaches the graph through the official `tigergraph-mcp` server
(<https://github.com/tigergraph/tigergraph-mcp>) instead of a direct driver
call, so the agent's graph access is genuine MCP tool use.

How it maps
-----------
The investigation's query catalogue is already a tool surface: 17 named
operations with declared parameters.  Each one is executed by calling the MCP
server's ``tigergraph__run_installed_query`` tool against the GSQL installed by
``fraudgraph.ingest.tg_load --queries``.  Two MCP tools are used directly:

* ``tigergraph__run_installed_query`` -- every catalogue query
* ``tigergraph__get_vertex_count``    -- the health check

Writing a case uses ``tigergraph__add_node`` / ``tigergraph__add_edge``.

Only these tools are called.  Nothing here assumes a tool the server does not
document, and ``available_tools()`` reports what the running server actually
offers so a mismatch shows up as a recorded failure rather than a silent one.

The MCP SDK is async and the investigation is synchronous, so a single event
loop runs on a background thread for the lifetime of the backend.

Enable with ``FG_GRAPH_BACKEND=mcp``.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
from typing import Any

from ..config import TG
from .tigergraph import TigerGraphBackend, _first

MCP_SERVER_CMD = os.getenv("FG_MCP_CMD", "tigergraph-mcp")
MCP_HTTP_URL = os.getenv("FG_MCP_URL", "")
RUN_QUERY_TOOL = "tigergraph__run_installed_query"
VERTEX_COUNT_TOOL = "tigergraph__get_vertex_count"
ADD_NODE_TOOL = "tigergraph__add_node"
ADD_EDGE_TOOL = "tigergraph__add_edge"


class _Loop:
    """One event loop on a background thread, shared by the backend."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True, name="fg-mcp-loop")
        self.thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coro, timeout: float = 180.0):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)


class MCPGraphBackend:
    """Implements the query catalogue over TigerGraph MCP."""

    name = "mcp"

    def __init__(self) -> None:
        self._loop = _Loop()
        self._session = None
        self._exit_stack = None
        self._tools: list[str] = []
        self._loop.run(self._connect())

    # ------------------------------------------------------------- session
    async def _connect(self) -> None:
        from contextlib import AsyncExitStack

        from mcp import ClientSession

        self._exit_stack = AsyncExitStack()
        if MCP_HTTP_URL:
            from mcp.client.streamable_http import streamablehttp_client

            read, write, *_ = await self._exit_stack.enter_async_context(
                streamablehttp_client(MCP_HTTP_URL)
            )
        else:
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            env = dict(os.environ)
            # The three variables tigergraph-mcp documents for Savanna are
            # TG_HOST, TG_SECRET and TG_GRAPHNAME; username/password is the
            # self-hosted path.
            env.update({
                "TG_HOST": TG.host,
                "TG_GRAPHNAME": TG.graph,
                "TG_SSL_PORT": TG.rest_port,
                "TG_TGCLOUD": "true" if TG.use_tls else "false",
            })
            if TG.secret:
                env["TG_SECRET"] = TG.secret
            else:
                env["TG_USERNAME"] = TG.username or "tigergraph"
                env["TG_PASSWORD"] = TG.password
            if TG.token:
                env["TG_API_TOKEN"] = TG.token
            params = StdioServerParameters(command=MCP_SERVER_CMD, args=[], env=env)
            read, write = await self._exit_stack.enter_async_context(stdio_client(params))

        self._session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        listed = await self._session.list_tools()
        self._tools = [t.name for t in listed.tools]

    def available_tools(self) -> list[str]:
        return list(self._tools)

    def close(self) -> None:
        try:
            if self._exit_stack is not None:
                self._loop.run(self._exit_stack.aclose(), timeout=20)
        finally:
            self._loop.close()

    # ---------------------------------------------------------------- call
    async def _acall(self, tool: str, args: dict) -> Any:
        result = await self._session.call_tool(tool, arguments=args)
        chunks = []
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                chunks.append(text)
        raw = "\n".join(chunks).strip()
        if not raw:
            return getattr(result, "structuredContent", None) or {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"text": raw}

    def _tool(self, tool: str, args: dict) -> Any:
        if tool not in self._tools:
            raise RuntimeError(
                f"the connected tigergraph-mcp server does not offer {tool}; "
                f"it offers {len(self._tools)} tools"
            )
        return self._loop.run(self._acall(tool, args))

    def _query(self, name: str, params: dict) -> list:
        """Run one installed GSQL query through MCP and unwrap the response."""
        payload = self._tool(RUN_QUERY_TOOL, {
            "graph_name": TG.graph, "query_name": name,
            "params": {k: v for k, v in params.items() if v is not None},
        })
        if isinstance(payload, dict):
            if payload.get("success") is False:
                raise RuntimeError(
                    f"{name}: {payload.get('error') or 'MCP reported failure'}"
                )
            data = payload.get("data", payload)
            if isinstance(data, dict):
                for key in ("results", "result", "output"):
                    if key in data:
                        return data[key]
                return [data]
            if isinstance(data, list):
                return data
        return payload if isinstance(payload, list) else [payload]


# The result shapes are identical to the direct TigerGraph backend, so the
# adapters are reused rather than duplicated: only the transport differs.
for _name in (
    "txn_detail", "card_window", "card_timeline", "card_profile", "customer_cards",
    "device_neighbors", "region_neighbors", "email_neighbors",
    "region_history_for_card", "device_history_for_card",
    "closed_cases_for_card", "closed_cases_for_customer", "closed_cases_for_device",
    "closed_cases_for_region", "similar_closed_cases", "read_cases",
):
    setattr(MCPGraphBackend, _name, getattr(TigerGraphBackend, _name))
setattr(MCPGraphBackend, "_cases", TigerGraphBackend._cases)
setattr(MCPGraphBackend, "_run", lambda self, q, p=None: self._query(q, p or {}))


def _ping(self) -> dict:
    try:
        res = self._run("graph_health")
        return {
            "ok": True, "backend": self.name, "transport": "mcp",
            "mcp_tools": len(self._tools),
            "transactions": _first(res, "transactions", 0),
            "cards": _first(res, "cards", 0),
            "customers": _first(res, "customers", 0),
            "device_profiles": _first(res, "device_profiles", 0),
            "closed_cases": _first(res, "closed_cases", 0),
            "agent_cases": _first(res, "agent_cases", 0),
            "graph": TG.graph,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "backend": self.name, "error": str(exc)[:300]}


def _write_case(self, case: dict) -> dict:
    """Persist an AgentCase through MCP's node/edge tools."""
    gid = case["graph_case_id"]
    try:
        self._tool(ADD_NODE_TOOL, {
            "graph_name": TG.graph, "node_type": "AgentCase", "node_id": gid,
            "attributes": {
                "case_id": case.get("case_id", ""),
                "status": case.get("status", ""), "verdict": case.get("verdict", ""),
                "fraud_probability": float(case.get("fraud_probability") or 0.0),
                "pattern": case.get("pattern", ""),
                "pattern_description": (case.get("pattern_description") or "")[:4000],
                "exposure_usd": float(case.get("exposure_usd") or 0.0),
                "summary": (case.get("summary") or "")[:4000],
                "stop_reason": (case.get("stop_reason") or "")[:2000],
                "trigger_type": case.get("trigger_type", ""),
                "opened_at": case.get("opened_at", ""), "closed_at": case.get("closed_at", ""),
                "card_id": case.get("card_id", ""), "customer_id": case.get("customer_id", ""),
                "first_suspicious_txn_id": case.get("first_suspicious_txn_id", ""),
                "actions_initial": "|".join(case.get("actions_initial", [])),
                "actions_final": "|".join(case.get("actions_final", [])),
                "report_filed": bool(case.get("report_filed")),
                "n_affected_txns": len(case.get("affected_txn_ids", [])),
                "source": "agent",
            },
        })
        edges = [("CASE_ON_CARD", "PaymentCard", case.get("card_id", ""))]
        edges += [("CASE_INVESTIGATES", "Transaction", str(t))
                  for t in case.get("affected_txn_ids", [])]
        edges += [("CASE_CONNECTED_TO", "PaymentCard", str(c))
                  for c in case.get("connected_card_ids", [])]
        edges += [("CASE_FROM_DEVICE", "DeviceProfile", str(d))
                  for d in case.get("connected_device_profiles", [])]
        edges += [("CASE_CITES_PRIOR", "ClosedCase", str(p))
                  for p in case.get("similar_prior_cases", [])]
        n_ev = 0
        for i, ev in enumerate(case.get("evidence", [])):
            eid = f"{gid}-E{i:02d}"
            self._tool(ADD_NODE_TOOL, {
                "graph_name": TG.graph, "node_type": "CaseEvidence", "node_id": eid,
                "attributes": {
                    "claim": (ev.get("claim") or "")[:4000],
                    "ev_source": ev.get("source", ""),
                    "ref": (ev.get("ref") or "")[:1000],
                    "entity_ids": "|".join(str(x) for x in ev.get("entity_ids", []))[:4000],
                },
            })
            edges.append(("CASE_CONTAINS_EVIDENCE", "CaseEvidence", eid))
            n_ev += 1
        written_edges = 0
        for etype, tgt_type, tgt in edges:
            if not tgt:
                continue
            try:
                self._tool(ADD_EDGE_TOOL, {
                    "graph_name": TG.graph, "src_node_type": "AgentCase", "src_node_id": gid,
                    "edge_type": etype, "tgt_node_type": tgt_type, "tgt_node_id": tgt,
                })
                written_edges += 1
            except Exception:  # noqa: BLE001 - a missing endpoint must not fail the case
                continue
        return {"written": True, "graph_case_id": gid, "backend": self.name,
                "evidence_vertices": n_ev, "edges": written_edges, "transport": "mcp"}
    except Exception as exc:  # noqa: BLE001
        return {"written": False, "backend": self.name, "error": str(exc)[:300]}


setattr(MCPGraphBackend, "ping", _ping)
setattr(MCPGraphBackend, "write_case", _write_case)
