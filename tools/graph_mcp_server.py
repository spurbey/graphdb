"""
MCP server exposing graph knowledge-base tools to Kiro.

Run:  python tools/graph_mcp_server.py
Kiro picks it up via .kiro/settings/mcp.json
"""

import json
import sys
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.graph_tools import (
    search_code_semantics,
    search_code_semantics_helix,
    explain_coupling,
    pipeline_status,
    find_structural_siblings,
    commit_review,
    select_tests,
    annotate_commit,
    query_function_history,
    get_code_time_travel_diff,
    trace_blast_radius,
    get_temporal_vulnerability_trace,
    edit_code,
    trace_semantic_evolution,
)

# ── Optional --data-dir flag ────────────────────────────────────────────────
import argparse as _argparse
_MCP_PARSER = _argparse.ArgumentParser()
_MCP_PARSER.add_argument("--data-dir", type=str, default=None)
_MCP_ARGS, _ = _MCP_PARSER.parse_known_args()

def _warm_pipeline() -> None:
    """Warm graph + query embedder after the HTTP server is already discoverable."""
    try:
        from tools import graph_tools as _graph_tools
        ready = _graph_tools._ensure_pipeline()
        if not ready:
            print("[graph_mcp_server] igraph pipeline unavailable, will use fallback", flush=True)
            return
        try:
            from pipeline_api import _embed as _query_embed
            _query_embed("warmup")
        except Exception as _embed_error:
            print(f"[graph_mcp_server] query embedder warmup failed: {_embed_error}", flush=True)
        _REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _DATA_ROOT = _MCP_ARGS.data_dir or _REPO_ROOT
        print(f"[graph_mcp_server] igraph pipeline ready (data={_DATA_ROOT})", flush=True)
    except Exception as _e:
        print(f"[graph_mcp_server] igraph pipeline unavailable, will use fallback: {_e}", flush=True)

PORT = 7700

MANIFEST = {
    "schema_version": "v1",
    "name":           "graphdb",
    "description":    "Git-inspired knowledge graph tools for code intelligence",
    "tools": [
        {
            "name":        "search_code_semantics",
            "description": (
                "Semantic search over the ingested codebase using PPR graph traversal. "
                "Returns a subgraph: ranked nodes (id, name, file, summary, code, ppr_score) "
                "plus edges between them. Better than raw vector search — finds structurally "
                "adjacent functions even when their names don't match the query."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string",  "description": "Natural language description of what you're looking for"},
                    "k":      {"type": "integer", "description": "Number of results (default 10)", "default": 10},
                    "mode":   {"type": "string",  "description": "Consumer mode: general_retrieval (default), risk, pre_edit, why_coupled", "default": "general_retrieval"},
                },
                "required": ["prompt"],
            },
        },
        {
            "name":        "search_code_semantics_helix",
            "description": "Raw HelixDB vector search fallback. Returns flat list without graph structure. Use search_code_semantics instead unless HelixDB comparison is needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string",  "description": "Natural language description of what you're looking for"},
                    "k":      {"type": "integer", "description": "Number of results (default 5)", "default": 5},
                },
                "required": ["prompt"],
            },
        },
        {
            "name":        "get_code_time_travel_diff",
            "description": "Show how a function changed — returns current state and its previous version.",
            "parameters": {
                "type": "object",
                "properties": {
                    "state_node_id": {"type": "string", "description": "FunctionState node_id (e.g. repo_name:state_path_to_file_functionName_commitHash)"},
                },
                "required": ["state_node_id"],
            },
        },
        {
            "name":        "trace_blast_radius",
            "description": "Find all functions that call a given function, up to N hops deep. Single Rust traversal — no client-side looping.",
            "parameters": {
                "type": "object",
                "properties": {
                    "function_identity_id": {"type": "string",  "description": "FunctionIdentity node_id (e.g. repo_name:func_path_to_file_functionName)"},
                    "depth":                {"type": "integer", "description": "How many CALLS hops to traverse (default 3)", "default": 3},
                },
                "required": ["function_identity_id"],
            },
        },
        {
            "name":        "get_temporal_vulnerability_trace",
            "description": "Find callers of a function whose code was committed before a given date — useful for tracing stale dependencies.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_func":     {"type": "string",  "description": "Function name to trace"},
                    "timestamp_iso":   {"type": "string",  "description": "ISO 8601 cutoff timestamp"},
                },
                "required": ["target_func", "timestamp_iso"],
            },
        },
        {
            "name":        "edit_code",
            "description": "Patch a function in the working tree with new code, then re-ingest into the graph. Use after trace tools identify a stale/vulnerable function.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file":          {"type": "string", "description": "Repo-relative file path (e.g. 'src/auth/service.py')"},
                    "function_name": {"type": "string", "description": "Name of the function to replace"},
                    "new_code":      {"type": "string", "description": "Complete new function definition (def ... including body)"},
                },
                "required": ["file", "function_name", "new_code"],
            },
        },
        {
            "name":        "explain_coupling",
            "description": (
                "Explain why two functions historically change together. "
                "Returns co-change category (shared_dependency / shared_commit_only / structural_redundant / temporal_burst), "
                "occurrence count, and theme proportions showing the underlying reason for coupling."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "func_id_a": {"type": "string", "description": "First function node ID"},
                    "func_id_b": {"type": "string", "description": "Second function node ID"},
                },
                "required": ["func_id_a", "func_id_b"],
            },
        },
        {
            "name":        "commit_review",
            "description": "Review commit impact: betweenness centrality, GraphSAGE drift, blast radius, CO_CHANGE warnings, PPR territory. Returns functions sorted by severity with test_scope (critical/broad/local).",
            "parameters": {
                "type": "object",
                "properties": {
                    "changed_function_ids": {"type": "array", "items": {"type": "string"}, "description": "Full node IDs of changed functions"},
                },
                "required": ["changed_function_ids"],
            },
        },
        {
            "name":        "select_tests",
            "description": "Return minimum test set for a commit. Uses betweenness + drift to scope: critical/broad/local. Skips structurally-unreachable tests.",
            "parameters": {
                "type": "object",
                "properties": {
                    "changed_function_ids": {"type": "array", "items": {"type": "string"}, "description": "Full node IDs of changed functions"},
                },
                "required": ["changed_function_ids"],
            },
        },
        {
            "name":        "annotate_commit",
            "description": "Annotate semantic memory for functions changed in a commit. Reads diffs, classifies change type (REDESIGNED/FIXED/EXTENDED/REFACTORED), writes memory notes. Triggered by /annotate-commit slash command.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sha":   {"type": "string",  "description": "Commit hash (full or short, defaults to HEAD)"},
                    "debug": {"type": "boolean", "description": "Write JSON log to sandbox/out/ (default false)", "default": False},
                },
                "required": ["sha"],
            },
        },
        {
            "name":        "query_function_history",
            "description": "Search a function's semantic memory history by topic. Vector search on memory_vec across ALL historical states. Use before modifying a function to understand design decisions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "func_id": {"type": "string", "description": "Full function node ID"},
                    "topic":   {"type": "string", "description": "Natural language query e.g. 'session handling'"},
                },
                "required": ["func_id", "topic"],
            },
        },
        {
            "name":        "pipeline_status",
            "description": (
                "Check which search mode is active. "
                "Returns pipeline=igraph (full PPR pipeline) or pipeline=unavailable (HelixDB fallback). "
                "Call this if search_code_semantics results look unexpectedly flat or wrong."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
        {
            "name":        "find_structural_siblings",
            "description": (
                "Find functions that play the same architectural role as a given function. "
                "Uses GraphSAGE structural embeddings — finds functions at the same call depth "
                "with similar fan-out patterns, regardless of semantic similarity. "
                "Use this when refactoring a pattern across modules."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "func_id": {"type": "string", "description": "Full function node ID"},
                    "k":       {"type": "integer", "description": "Number of siblings to return (default 8)", "default": 8},
                },
                "required": ["func_id"],
            },
        },
        {
            "name":        "trace_semantic_evolution",
            "description": "Recursively trace the semantic evolution of a function. Finds its memory history, the commits that changed it, and what other functions changed in those same commits (coupled changes), along with their specific semantic edge types. Use this to reason about why a function evolved and what else was forced to change with it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "func_id": {"type": "string", "description": "Full node ID of the function"},
                },
                "required": ["func_id"],
            },
        },
    ],
}

TOOL_MAP = {
    "search_code_semantics":            lambda p: search_code_semantics(p["prompt"], p.get("k", 10), p.get("mode", "general_retrieval")),
    "search_code_semantics_helix":      lambda p: search_code_semantics_helix(p["prompt"], p.get("k", 5)),
    "explain_coupling":                 lambda p: explain_coupling(p["func_id_a"], p["func_id_b"]),
    "pipeline_status":                  lambda p: pipeline_status(),
    "find_structural_siblings":         lambda p: find_structural_siblings(p["func_id"], p.get("k", 8)),
    "get_code_time_travel_diff":        lambda p: get_code_time_travel_diff(p["state_node_id"]),
    "trace_blast_radius":               lambda p: trace_blast_radius(p["function_identity_id"], p.get("depth", 3)),
    "get_temporal_vulnerability_trace": lambda p: get_temporal_vulnerability_trace(p["target_func"], p["timestamp_iso"]),
    "edit_code":                        lambda p: edit_code(p["file"], p["function_name"], p["new_code"]),
    "annotate_commit":                  lambda p: annotate_commit(p["sha"], p.get("debug", False)),
    "query_function_history":           lambda p: query_function_history(p["func_id"], p.get("topic", "")),
    "commit_review":                    lambda p: commit_review(p["changed_function_ids"]),
    "select_tests":                     lambda p: select_tests(p["changed_function_ids"]),
    "trace_semantic_evolution":         lambda p: trace_semantic_evolution(p["func_id"]),
}


class MCPHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # quiet

    def _send_json(self, code: int, body: object):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/manifest"):
            self._send_json(200, MANIFEST)
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}

        if self.path == "/call":
            name   = body.get("name", "")
            params = body.get("parameters", {})
            fn     = TOOL_MAP.get(name)
            if fn is None:
                self._send_json(404, {"error": f"unknown tool: {name}"})
                return
            try:
                started = time.perf_counter()
                print(f"[graph_mcp_server] tool start: {name}", flush=True)
                result = fn(params)
                elapsed = time.perf_counter() - started
                print(f"[graph_mcp_server] tool done: {name} ({elapsed:.2f}s)", flush=True)
                self._send_json(200, {"result": result})
            except Exception as e:
                print(f"[graph_mcp_server] tool error: {name}: {e}", flush=True)
                self._send_json(500, {"error": str(e)})
        else:
            self._send_json(404, {"error": "not found"})


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), MCPHandler)
    print(f"Graph MCP server running on http://127.0.0.1:{PORT}")
    print("Tools: search_code_semantics (igraph PPR) | search_code_semantics_helix (HelixDB) | get_code_time_travel_diff | trace_blast_radius | get_temporal_vulnerability_trace | edit_code")
    threading.Thread(target=_warm_pipeline, name="graphdb-pipeline-warmup", daemon=True).start()
    server.serve_forever()
