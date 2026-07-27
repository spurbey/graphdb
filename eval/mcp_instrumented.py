"""
Wraps graph_mcp_server.py with per-call instrumentation logging.
Starts the server, logs every tool call + timing + size, writes to file.
"""

import atexit
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

LOG_PATH = Path(__file__).resolve().parent / "report" / "mcp_instrumented_log.jsonl"
LOG_ENTRIES = []


def instrument():
    """Patch graph_mcp_server's TOOL_MAP with logging wrappers."""
    import tools.graph_mcp_server as server

    original_map = server.TOOL_MAP
    instrumented = {}

    for name, fn in original_map.items():
        def make_wrapped(n, f):
            def wrapped(params):
                t0 = time.time()
                error = None
                try:
                    result = f(params)
                except Exception as e:
                    result = {"error": str(e)}
                    error = str(e)
                elapsed = time.time() - t0

                def est_tokens(obj):
                    text = json.dumps(obj) if not isinstance(obj, str) else obj
                    return len(text) // 4

                entry = {
                    "tool": n,
                    "params_sent": params,
                    "result_summary": _summarize(result, n),
                    "latency_s": round(elapsed, 4),
                    "tokens_sent": est_tokens(params),
                    "tokens_received": est_tokens(result),
                    "error": error,
                    "timestamp": time.time(),
                }
                LOG_ENTRIES.append(entry)
                with open(LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
                return result
            return wrapped
        instrumented[name] = make_wrapped(name, fn)

    server.TOOL_MAP = instrumented
    print(f"[instrument] Wrapped {len(instrumented)} MCP tools with logging")


def _summarize(result, tool_name):
    if result is None:
        return "None"
    if isinstance(result, dict) and "error" in result:
        return f"error: {result['error'][:60]}"
    if tool_name == "search_code_semantics":
        nodes = len(result.get("nodes", []))
        edges = len(result.get("edges", []))
        return f"{nodes} nodes, {edges} edges"
    if tool_name == "explain_coupling":
        if isinstance(result, dict):
            cat = result.get("category", "?")
            cnt = result.get("co_change_count", 0)
            return f"category={cat}, count={cnt}"
    if tool_name == "commit_review":
        if isinstance(result, list):
            return f"{len(result)} functions"
    return str(type(result).__name__)


if __name__ == "__main__":
    instrument()
    import tools.graph_mcp_server as server
    print(f"[instrument] Logging to {LOG_PATH}")
    server.server.serve_forever()
