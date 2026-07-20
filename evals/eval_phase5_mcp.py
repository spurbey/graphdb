"""
Phase 5 Eval: MCP server manifest includes new tools.
Verifies the server file has correct imports, manifest entries, and tool map routing.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

print("Phase 5 Eval: MCP server manifest")
print("=" * 60)

# ── Check imports ──────────────────────────────────────────────────────────
print("\n1. Import verification")
from tools.graph_tools import (
    annotate_commit, query_function_history,
    commit_review, select_tests,
)
print("   annotate_commit imported - PASS")
print("   query_function_history imported - PASS")
print("   commit_review imported - PASS")
print("   select_tests imported - PASS")

# ── Check MCP server source for correct registration ───────────────────────
print("\n2. MCP server tool registration")
src = open(ROOT / "tools" / "graph_mcp_server.py", encoding="utf-8").read()

# Check that no lambda entries exist INSIDE the MANIFEST dict (before TOOL_MAP)
manifest_end = src.find('TOOL_MAP = {')
manifest_section = src[:manifest_end] if manifest_end > 0 else src

checks = [
    ("annotate_commit in MANIFEST", '"annotate_commit"' in src),
    ("query_function_history in MANIFEST", '"query_function_history"' in src),
    ("commit_review in MANIFEST", '"commit_review"' in src),
    ("select_tests in MANIFEST", '"select_tests"' in src),
    ("annotate_commit in TOOL_MAP", '"annotate_commit":' in src),
    ("query_function_history in TOOL_MAP", '"query_function_history":' in src),
    ("commit_review in TOOL_MAP", '"commit_review":' in src),
    ("select_tests in TOOL_MAP", '"select_tests":' in src),
    ("No lambda entries inside MANIFEST section", 'lambda' not in manifest_section),
]

all_pass = True
for label, ok in checks:
    status = "PASS" if ok else "FAIL"
    if not ok:
        all_pass = False
    print(f"   {status}: {label}")

# ── Check for syntax validity ──────────────────────────────────────────────
print("\n3. Syntax check")
try:
    compile(src, "graph_mcp_server.py", "exec")
    print("   Syntax OK - PASS")
except SyntaxError as e:
    print(f"   Syntax ERROR: {e} - FAIL")
    all_pass = False

print()
if all_pass:
    print("Phase 5 Eval: ALL PASSED")
else:
    print("Phase 5 Eval: SOME CHECKS FAILED")
