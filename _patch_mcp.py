"""Patch graph_mcp_server.py to add commit_review and select_tests."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[0]
mcp_path = ROOT / "tools" / "graph_mcp_server.py"
mcp = mcp_path.read_text(encoding="utf-8")

print("commit_review occurrences:", mcp.count("commit_review"))
print("select_tests occurrences:", mcp.count("select_tests"))

if "commit_review" in mcp and "select_tests" in mcp:
    print("Already patched — nothing to do")
    exit(0)

# Add to imports
old_import = "    find_structural_siblings,"
new_import = "    find_structural_siblings,\n    commit_review,\n    select_tests,"
if old_import in mcp:
    mcp = mcp.replace(old_import, new_import, 1)
    print("Import added")

# Add to TOOL_MAP
old_map = '"find_structural_siblings"'
if old_map in mcp:
    idx = mcp.find(old_map)
    line_end = mcp.find("\n", idx)
    insert = (
        '\n    "commit_review":                    lambda p: commit_review(p["changed_function_ids"]),'
        '\n    "select_tests":                     lambda p: select_tests(p["changed_function_ids"]),'
    )
    mcp = mcp[:line_end] + insert + mcp[line_end:]
    print("TOOL_MAP entries added")

# Add to manifest before pipeline_status
manifest_insert = '''        {
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
        {'''

old_manifest_anchor = '        {\n            "name":        "pipeline_status",'
if old_manifest_anchor in mcp:
    mcp = mcp.replace(old_manifest_anchor, manifest_insert + '\n            "name":        "pipeline_status",', 1)
    print("Manifest entries added")

mcp_path.write_text(mcp, encoding="utf-8")
print("Done")
