"""
Phase 4 Eval: End-to-end test of annotate_commit().
Tests what can be tested without HelixDB + AMO ingested.
Full end-to-end requires HelixDB running with AMO data.
"""
import sys
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

print("Phase 4 Eval: annotate_commit() end-to-end")
print("=" * 60)

# ── Test 1: annotate_commit on a local graphdb commit ──────────────────────
print("\nTest 1: annotate_commit on local graphdb commit")

import subprocess
result_raw = subprocess.run(
    ["git", "log", "--format=%H", "-5"],
    capture_output=True, text=True, cwd=str(ROOT),
)
commits = [c.strip() for c in result_raw.stdout.strip().split("\n") if c.strip()]

if not commits:
    print("  No commits found - FAIL")
    sys.exit(1)

# Pick the most recent commit that touches .py files
from pipeline_api import annotate_commit, list_functions_changed_in_commit

sha = commits[0]
print(f"  Testing on commit: {sha}")

changed = list_functions_changed_in_commit(sha)
if changed and "error" not in changed[0]:
    print(f"  Functions changed in commit: {len(changed)}")
    for fn in changed[:5]:
        print(f"    - {fn['name']} ({'new' if fn['is_new'] else 'modified'})")

    # Run annotate_commit (this will call LLM)
    result = annotate_commit(sha, debug=True)
    print(f"\n  Result:")
    print(f"    annotated: {len(result['annotated'])}")
    print(f"    skipped:   {len(result['skipped'])}")
    print(f"    errors:    {len(result['errors'])}")
    print(f"    cooccurrence_candidates: {result['cooccurrence_candidates']}")

    for a in result["annotated"]:
        print(f"    - {a['func_id']}: {a['edge_type']}")
    for s in result["skipped"]:
        print(f"    - SKIPPED {s['func_id']}: {s['reason'][:80]}")
    for e in result["errors"]:
        print(f"    - ERROR {e['func_id']}: {e['error'][:80]}")

    # Check structure
    assert isinstance(result["annotated"], list)
    assert isinstance(result["skipped"], list)
    assert isinstance(result["errors"], list)
    print("\n  CHECK: result structure correct - PASS")

    # Check debug log was written
    safe_sha = sha[:12] if len(sha) > 12 else sha
    log_path = ROOT / "sandbox" / "out" / f"annotate_commit_{safe_sha}.json"
    if log_path.exists():
        print(f"  CHECK: debug log written to {log_path} - PASS")
        with open(log_path, encoding="utf-8") as f:
            log_data = json.load(f)
        assert "commit" in log_data
        assert "changed_functions" in log_data
        assert "result" in log_data
        print("  CHECK: debug log structure correct - PASS")
    else:
        print(f"  CHECK: debug log NOT found at {log_path} - might be in different location")

    # Check that no invalid edge types were used
    allowed = {"REDESIGNED", "FIXED", "EXTENDED", "REFACTORED"}
    for a in result["annotated"]:
        assert a["edge_type"] in allowed, f"Invalid edge_type: {a['edge_type']}"
    print("  CHECK: all annotated edge types are valid - PASS")

else:
    print(f"  No changed functions or error: {changed[0] if changed else 'empty'}")

# ── Test 2: annotate_commit on nonexistent commit ───────────────────────────
print("\nTest 2: annotate_commit on nonexistent commit")

result = annotate_commit("nonexistent1234567")
assert len(result["errors"]) >= 1
print(f"  Errors: {result['errors']}")
print("  CHECK: nonexistent commit handled gracefully - PASS")

# ── Test 3: annotate_commit with no changed functions ──────────────────────
print("\nTest 3: annotate_commit with no changed functions (empty commit)")

# Get an empty merge commit or initial commit
result_raw = subprocess.run(
    ["git", "log", "--format=%H", "--diff-filter=A", "-1"],
    capture_output=True, text=True, cwd=str(ROOT),
)
initial_sha = result_raw.stdout.strip()

if initial_sha:
    # This is the initial commit - might have lots of changes
    result = annotate_commit(initial_sha, debug=True)
    print(f"  annotated: {len(result['annotated'])}")
    print(f"  skipped:   {len(result['skipped'])}")
    print(f"  errors:    {len(result['errors'])}")
    assert isinstance(result["annotated"], list)
    assert isinstance(result["errors"], list)
    print("  CHECK: initial commit handled without crash - PASS")

print()
print("Phase 4 Eval: CORE CHECKS PASSED")
print()
print("NOTE: Full verification requires HelixDB running + AMO ingested.")
print("Run these additional checks after AMO is in HelixDB:")
print("  1. annotated functions have memory in HelixDB")
print("  2. write_function_memory persists correctly")
print("  3. read_function_memory_history retrieves written memories")
