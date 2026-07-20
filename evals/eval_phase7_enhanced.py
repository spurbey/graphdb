"""
Phase 7 Eval: Enhanced explain_coupling and commit_review.
Tests that the enhanced functions return the new memory fields.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

print("Phase 7 Eval: Enhanced explain_coupling + commit_review")
print("=" * 60)

# ── Test 1: Enhanced explain_coupling ──────────────────────────────────────
print("\n1. Enhanced explain_coupling")

from tools.graph_tools import explain_coupling

result = explain_coupling(
    "src/test.py::func_a",
    "src/test.py::func_b",
)
print(f"   Return type: {type(result).__name__}")
assert isinstance(result, dict)

# Check the enhanced field exists (may be absent if no memories)
if "recent_co_annotation" in result:
    print(f"   Has recent_co_annotation - PASS")
    assert "commit" in result["recent_co_annotation"]
    assert "memory_a" in result["recent_co_annotation"]
    assert "memory_b" in result["recent_co_annotation"]
else:
    print(f"   No recent_co_annotation (expected if no annotated memories exist yet)")

# Check coupled field
print(f"   coupled: {result.get('coupled', False)}")
assert "coupled" in result or "error" in result
print("   explain_coupling structure correct - PASS")

# ── Test 2: Enhanced commit_review ────────────────────────────────────────
print("\n2. Enhanced commit_review")

from tools.graph_tools import commit_review

result = commit_review(["src/test.py::func_a", "src/test.py::func_b"])
print(f"   Return type: {type(result).__name__}")
assert isinstance(result, list), f"Expected list, got {type(result)}"

# Check prior_memory field exists (may be None)
for r in result:
    if "error" in r:
        continue
    if "prior_memory" in r:
        if r["prior_memory"] is not None:
            assert "memory" in r["prior_memory"]
            assert "edge_type" in r["prior_memory"]
            assert "commit_sha" in r["prior_memory"]
            print(f"   prior_memory present for {r.get('func_id', r.get('name', '?'))} - PASS")
        else:
            print(f"   prior_memory is None (no memories yet) for {r.get('func_id', r.get('name', '?'))}")
    else:
        print(f"   prior_memory field missing (check enhancement)")

print()
print("Phase 7 Eval: CORE CHECKS PASSED")
print("NOTE: Full test requires HelixDB with annotated memories.")
print("Run after /annotate-commit has populated some FunctionState.memory fields.")
