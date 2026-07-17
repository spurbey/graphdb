"""Patch graph_tools.py to add commit_review and select_tests."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[0]
path = ROOT / "tools" / "graph_tools.py"
content = path.read_text(encoding="utf-8")

OLD = """            return _find(func_id, k=k)
        except Exception as e:
            return [{"error": str(e)}]
    return [{"error": "igraph pipeline unavailable"}]


# ── Tool 2: Time-travel diff"""

NEW = """            return _find(func_id, k=k)
        except Exception as e:
            return [{"error": str(e)}]
    return [{"error": "igraph pipeline unavailable"}]


def commit_review(changed_function_ids: list[str]) -> list[dict]:
    \"\"\"
    Review impact of a commit that changed the given functions.
    Combines 5 layers:
      - betweenness centrality (architectural importance)
      - GraphSAGE drift (structural role change)
      - blast radius (who calls these functions)
      - CO_CHANGE warnings (what historically moves with them)
      - PPR territory (what they orchestrate downstream)

    Returns list sorted by severity. Each entry has:
      name, file, severity, betweenness, drift, blast_radius,
      ppr_territory, co_change_warnings, test_scope, reason

    changed_function_ids: full node IDs like
      'src/agent_memory_orchestrator/memory/ingest.py::ingest_hook_payload'
    \"\"\"
    if _ensure_pipeline():
        try:
            from pipeline_api import commit_review as _cr
            return _cr(changed_function_ids)
        except Exception as e:
            return [{"error": str(e)}]
    return [{"error": "igraph pipeline unavailable"}]


def select_tests(changed_function_ids: list[str]) -> dict:
    \"\"\"
    Given changed functions, return the minimum test set to run.
    Uses betweenness + GraphSAGE drift to scope test selection:
      critical (high betweenness or high drift) -> all blast radius tests
      broad -> tests within 2 hops
      local (leaf functions) -> only direct tests

    Returns:
      critical: tests that must run
      recommended: tests that should run
      skippable: tests that can be skipped
      co_change_warnings: functions that should have changed but didn't
      summary: human-readable summary with reduction percentage
    \"\"\"
    if _ensure_pipeline():
        try:
            from pipeline_api import select_tests as _st
            return _st(changed_function_ids)
        except Exception as e:
            return {"error": str(e)}
    return {"error": "igraph pipeline unavailable"}


# ── Tool 2: Time-travel diff"""

assert OLD in content, "Pattern not found in graph_tools.py"
content = content.replace(OLD, NEW, 1)
path.write_text(content, encoding="utf-8")
print("graph_tools.py patched OK")

# Also add to imports and TOOL_MAP in graph_mcp_server.py
mcp_path = ROOT / "tools" / "graph_mcp_server.py"
mcp = mcp_path.read_text(encoding="utf-8")

# Add to imports
OLD_IMPORT = "    find_structural_siblings,"
NEW_IMPORT = "    find_structural_siblings,\n    commit_review,\n    select_tests,"
assert OLD_IMPORT in mcp, "Import pattern not found"
mcp = mcp.replace(OLD_IMPORT, NEW_IMPORT, 1)

# Add to TOOL_MAP
OLD_MAP = '    "find_structural_siblings":         lambda p: find_structural_siblings(p["func_id"], p.get("k", 8)),'
NEW_MAP = (
    '    "find_structural_siblings":         lambda p: find_structural_siblings(p["func_id"], p.get("k", 8)),\n'
    '    "commit_review":                    lambda p: commit_review(p["changed_function_ids"]),\n'
    '    "select_tests":                     lambda p: select_tests(p["changed_function_ids"]),'
)
assert OLD_MAP in mcp, "TOOL_MAP pattern not found"
mcp = mcp.replace(OLD_MAP, NEW_MAP, 1)

# Add to manifest
OLD_MANIFEST = '            "name":        "pipeline_status",'
NEW_TOOL_ENTRIES = '''        {
            "name":        "commit_review",
            "description": (
                "Review the impact of a commit. Combines betweenness centrality, "
                "GraphSAGE drift, blast radius, CO_CHANGE warnings, and PPR territory. "
                "Returns functions sorted by severity with test_scope (critical/broad/local) "
                "and co-change warnings for functions that should have changed but didn't."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "changed_function_ids": {
                        "type": "array", "items": {"type": "string"},
                        "description": "Full node IDs of changed functions e.g. ['src/.../ingest.py::ingest_hook_payload']"
                    },
                },
                "required": ["changed_function_ids"],
            },
        },
        {
            "name":        "select_tests",
            "description": (
                "Given changed functions, return the minimum test set to run. "
                "Uses betweenness + GraphSAGE drift to scope: critical functions run all "
                "blast radius tests, local (leaf) functions run only direct tests. "
                "Reduces CI test suite by skipping structurally-unreachable tests."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "changed_function_ids": {
                        "type": "array", "items": {"type": "string"},
                        "description": "Full node IDs of changed functions"
                    },
                },
                "required": ["changed_function_ids"],
            },
        },
        {
            "name":        "pipeline_status",'''
assert OLD_MANIFEST in mcp, "Manifest pattern not found"
mcp = mcp.replace(OLD_MANIFEST, NEW_TOOL_ENTRIES, 1)

mcp_path.write_text(mcp, encoding="utf-8")
print("graph_mcp_server.py patched OK")
