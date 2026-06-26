"""
MCP server exposing graph knowledge-base tools to Kiro.

Run:  python tools/graph_mcp_server.py
Kiro picks it up via .kiro/settings/mcp.json
"""

import json
import sys
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.graph_tools import (
    search_code_semantics,
    get_code_time_travel_diff,
    trace_blast_radius,
    get_temporal_vulnerability_trace,
)

PORT = 7700

MANIFEST = {
    "schema_version": "v1",
    "name":           "graphdb",
    "description":    "Git-inspired knowledge graph tools for code intelligence",
    "tools": [
        {
            "name":        "search_code_semantics",
            "description": "Semantic search over active function implementations. Use to find relevant functions by behavior description.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Natural language description of what you're looking for"},
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
                    "state_node_id": {"type": "string", "description": "FunctionState node_id (e.g. state_auth_service_login_a1b2c3d)"},
                },
                "required": ["state_node_id"],
            },
        },
        {
            "name":        "trace_blast_radius",
            "description": "Find all functions that call a given function. Use to assess impact of changing it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "function_identity_id": {"type": "string", "description": "FunctionIdentity node_id (e.g. func_auth_service_login)"},
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
                    "target_func":     {"type": "string",  "description": "Function name to trace (e.g. 'create_user')"},
                    "timestamp_iso":   {"type": "string",  "description": "ISO 8601 cutoff timestamp (e.g. '2026-06-25T00:00:00+00:00')"},
                },
                "required": ["target_func", "timestamp_iso"],
            },
        },
    ],
}

TOOL_MAP = {
    "search_code_semantics":           lambda p: search_code_semantics(p["prompt"], p.get("k", 5)),
    "get_code_time_travel_diff":       lambda p: get_code_time_travel_diff(p["state_node_id"]),
    "trace_blast_radius":              lambda p: trace_blast_radius(p["function_identity_id"]),
    "get_temporal_vulnerability_trace": lambda p: get_temporal_vulnerability_trace(p["target_func"], p["timestamp_iso"]),
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
                result = fn(params)
                self._send_json(200, {"result": result})
            except Exception as e:
                self._send_json(500, {"error": str(e)})
        else:
            self._send_json(404, {"error": "not found"})


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), MCPHandler)
    print(f"Graph MCP server running on http://127.0.0.1:{PORT}")
    print("Tools: search_code_semantics | get_code_time_travel_diff | trace_blast_radius | get_temporal_vulnerability_trace")
    server.serve_forever()
