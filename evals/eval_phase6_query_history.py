"""
Phase 6 Eval: query_function_history tool.
Tests what can be tested without HelixDB. HelixDB-dependent tests are skipped.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

print("Phase 6 Eval: query_function_history")
print("=" * 60)

# ── Test 1: Function exists and returns valid shape ─────────────────────────
print("\n1. Function import and basic call")
from tools.graph_tools import query_function_history

# Call with a known func_id — will try HelixDB, may fail gracefully
result = query_function_history("src/test.py::some_func", "test topic")
print(f"   Return type: {type(result).__name__}")
assert isinstance(result, list), f"Expected list, got {type(result)}"

if result and "error" in result[0]:
    print(f"   HelixDB error (expected if not running): {result[0]['error'][:60]}")
else:
    print(f"   Results count: {len(result)}")

print("   query_function_history returns correct type - PASS")

# ── Test 2: Function signature ─────────────────────────────────────────────
print("\n2. Parameter handling")
import inspect
sig = inspect.signature(query_function_history)
params = list(sig.parameters.keys())
assert "func_id" in params
assert "topic" in params
print(f"   Parameters: {params} - PASS")

# ── Test 3: Empty result shape ─────────────────────────────────────────────
print("\n3. Empty result shape")
result = query_function_history("nonexistent_func_xyz::ghost", "anything")
assert isinstance(result, list)
if not result or "error" not in (result[0] if result else {}):
    print(f"   Empty list or no error: {len(result)} items - PASS")
else:
    print(f"   HelixDB error as expected: {result[0].get('error', '')[:60]}")

print()
print("Phase 6 Eval: CORE CHECKS PASSED")
print("NOTE: Full test requires HelixDB with annotated memories.")
print("Run after /annotate-commit has populated some FunctionState.memory fields.")
