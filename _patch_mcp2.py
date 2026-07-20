"""Patch graph_mcp_server.py to add annotate_commit and query_function_history."""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[0]
path = ROOT / "tools" / "graph_mcp_server.py"
src = path.read_text(encoding="utf-8")

# 1. Add to imports
assert "    select_tests,\n    get_code_time_travel_diff," in src, f"import pattern not found. Got: {src[400:600]}"
src = src.replace("    select_tests,\n    get_code_time_travel_diff,", "    select_tests,\n    annotate_commit,\n    query_function_history,\n    get_code_time_travel_diff,", 1)
print("imports updated")

# 2. Add to TOOL_MAP - find the last entry before closing brace
# The TOOL_MAP ends with:   "edit_code": ...,\n}
old_edit = '"edit_code":                        lambda p: edit_code(p["file"], p["function_name"], p["new_code"]),'
new_edit = (
    '"edit_code":                        lambda p: edit_code(p["file"], p["function_name"], p["new_code"]),\n'
    '    "annotate_commit":                  lambda p: annotate_commit(p["sha"], p.get("debug", False)),\n'
    '    "query_function_history":           lambda p: query_function_history(p["func_id"], p.get("topic", "")),\n'
)
assert old_edit in src, f"TOOL_MAP edit_code pattern not found"
src = src.replace(old_edit, new_edit, 1)
print("TOOL_MAP updated")

# 3. Add to manifest - add two new tool entries before pipeline_status
manifest_entry = '''        {
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
                    "func_id": {"type": "string", "description": "Full node ID e.g. 'src/.../ingest.py::ingest_hook_payload'"},
                    "topic":   {"type": "string", "description": "Natural language query e.g. 'session handling'"},
                },
                "required": ["func_id", "topic"],
            },
        },
        {
            "name":        "pipeline_status",'''

old_ps = '        {\n            "name":        "pipeline_status",'
assert old_ps in src, "pipeline_status pattern not found"
src = src.replace(old_ps, manifest_entry, 1)
print("manifest updated")

path.write_text(src, encoding="utf-8")

# Verify syntax
try:
    ast.parse(src)
    print("syntax OK")
except SyntaxError as e:
    print(f"SYNTAX ERROR: {e}")
