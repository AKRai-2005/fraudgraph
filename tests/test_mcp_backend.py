"""TigerGraph MCP backend: contract checks that need no live server.

Everything here is about not repeating the mistakes that exercising a real
`tigergraph-mcp` actually surfaced: guessed tool-argument names, a parser that
swallowed a failure, and a graph that drifted from the answer files.
"""
from __future__ import annotations

import json

import pytest

from fraudgraph.graph import mcp_backend as M
from fraudgraph.graph.queries import CATALOGUE


def test_backend_implements_the_whole_catalogue():
    missing = [n for n in CATALOGUE if not hasattr(M.MCPGraphBackend, n)]
    assert missing == [], f"MCP backend is missing: {missing}"


def test_cases_helper_is_still_a_staticmethod():
    """Copied off TigerGraphBackend, it must be re-wrapped or `self` is passed
    as its first positional argument."""
    assert isinstance(M.MCPGraphBackend.__dict__["_cases"], staticmethod)
    assert M.MCPGraphBackend._cases([]) == []


def test_tool_names_match_what_the_server_documents():
    assert M.RUN_QUERY_TOOL == "tigergraph__run_installed_query"
    assert M.ADD_NODE_TOOL == "tigergraph__add_node"
    assert M.ADD_EDGE_TOOL == "tigergraph__add_edge"
    assert M.DELETE_NODE_TOOL == "tigergraph__delete_node"


def test_write_case_uses_the_servers_argument_names():
    """add_node takes vertex_type/vertex_id; add_edge takes source_/target_
    vertex pairs. The node_type/src_node_* names were a guess and every write
    failed on them."""
    src = (M.__file__ and open(M.__file__, encoding="utf-8").read()) or ""
    body = src[src.index("def _write_case"):]
    assert '"vertex_type"' in body and '"vertex_id"' in body
    assert '"source_vertex_type"' in body and '"target_vertex_id"' in body
    assert '"node_type"' not in body and '"src_node_id"' not in body


# ------------------------------------------------------------ payload parsing
def test_payload_is_parsed_out_of_a_markdown_fence():
    raw = '```json\n{"success": true, "data": {"n": 3}}\n```\n\n**Ran the query.**'
    assert M._parse_payload(raw) == {"success": True, "data": {"n": 3}}


def test_bare_json_still_parses():
    assert M._parse_payload('{"success": true}') == {"success": True}


def test_unparseable_payload_returns_none_rather_than_guessing():
    assert M._parse_payload("no json at all") is None


def test_a_failed_query_raises_instead_of_looking_empty():
    """The original fallback turned `success: false` into `{"text": ...}`,
    so an HTTP 500 read as an empty result."""
    payload = {"success": False, "error": "500, message='Internal Server Error'"}

    class Fake(M.MCPGraphBackend):
        def __init__(self):  # no session
            pass

        def _tool(self, tool, args):
            return payload

    with pytest.raises(RuntimeError) as exc:
        Fake()._query("txn_detail", {"txn_id": "1"})
    assert "txn_detail" in str(exc.value)


def test_a_stopped_workspace_explains_itself():
    msg = M._explain("500, message='Internal Server Error'")
    assert "workspace is stopped" in msg and "savanna.tgcloud.io" in msg
    plain = M._explain("something unrelated")
    assert "workspace is stopped" not in plain


# --------------------------------------------------------------- environment
def test_only_the_documented_variables_reach_the_server(monkeypatch):
    """An empty TG_PASSWORD inherited from .env broke the server's auth when a
    secret was also set."""
    src = open(M.__file__, encoding="utf-8").read()
    block = src[src.index("env = {k: v for k, v"):src.index("params = StdioServerParameters")]
    assert 'not k.startswith("TG_")' in block, "TG_* must be stripped before re-adding"
    assert '"TG_HOST"' in block and '"TG_GRAPHNAME"' in block and '"TG_SECRET"' in block


def test_case_vertex_is_cleared_before_rewrite():
    """Edge upserts are keyed on (from, to), so a re-run would accumulate
    citations unless the case vertex is dropped first."""
    src = open(M.__file__, encoding="utf-8").read()
    body = src[src.index("def _write_case"):]
    assert "DELETE_NODE_TOOL" in body
    assert body.index("DELETE_NODE_TOOL") < body.index("ADD_NODE_TOOL")


def test_edge_failures_are_recorded_not_swallowed():
    src = open(M.__file__, encoding="utf-8").read()
    body = src[src.index("def _write_case"):]
    assert "failed_edges" in body
    assert 'res.get("success") is False' in body
