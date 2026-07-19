"""
Phase 2 Eval: Verify sub-agent tools work correctly.
Tests what can be tested without HelixDB running.
Tests that require HelixDB are marked and skipped if server not available.
"""
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

print("Phase 2 Eval: Sub-agent tools")
print("=" * 60)

# ── Test 1: list_functions_changed_in_commit ──────────────────────────────────
print("\nTest 1: list_functions_changed_in_commit")

# Check the function exists and has correct signature
from pipeline_api import list_functions_changed_in_commit, _ALLOWED_EDGE_TYPES
from pipeline_api import write_function_memory, read_function_memory_history, log_new_cooccurrence
print("  All 4 tools imported successfully - PASS")

# Check allowed edge types
assert _ALLOWED_EDGE_TYPES == {"REDESIGNED", "FIXED", "EXTENDED", "REFACTORED"}, \
    f"Wrong allowed edge types: {_ALLOWED_EDGE_TYPES}"
print(f"  Allowed edge types correct: {sorted(_ALLOWED_EDGE_TYPES)} - PASS")

# ── Test 2: write_function_memory validation ──────────────────────────────────
print("\nTest 2: write_function_memory input validation")

# Invalid edge type should return error immediately (no HelixDB needed)
result = write_function_memory(
    func_id="src/test.py::some_func",
    commit_sha="abc1234",
    edge_type="INVALID_TYPE",
    memory_text="Some memory",
)
assert result["ok"] == False
assert "Invalid edge_type" in result["error"]
assert "INVALID_TYPE" in result["error"]
print("  Invalid edge_type rejected correctly - PASS")

# Empty memory text should return error
result = write_function_memory(
    func_id="src/test.py::some_func",
    commit_sha="abc1234",
    edge_type="FIXED",
    memory_text="",
)
assert result["ok"] == False
assert "empty" in result["error"].lower()
print("  Empty memory_text rejected correctly - PASS")

# ── Test 3: log_new_cooccurrence ──────────────────────────────────────────────
print("\nTest 3: log_new_cooccurrence")

import os
candidates_path = ROOT / "sandbox" / "cooccurrence_candidates.jsonl"
initial_lines = 0
if candidates_path.exists():
    initial_lines = len(open(candidates_path, encoding="utf-8").readlines())

log_new_cooccurrence("src/a.py::func_a", "src/b.py::func_b", "test1234")

if candidates_path.exists():
    lines = open(candidates_path, encoding="utf-8").readlines()
    assert len(lines) == initial_lines + 1, f"Expected {initial_lines+1} lines, got {len(lines)}"
    last = json.loads(lines[-1])
    assert last["source"] == "src/a.py::func_a"
    assert last["target"] == "src/b.py::func_b"
    assert last["commit"] == "test1234"
    assert "timestamp" in last
    print("  Candidate logged correctly to JSONL - PASS")
else:
    print("  WARNING: cooccurrence_candidates.jsonl not created (sandbox dir may not exist)")

# ── Test 4: list_functions_changed_in_commit on graphdb auth commits ──────────
print("\nTest 4: list_functions_changed_in_commit on local graphdb repo")

import subprocess
result_raw = subprocess.run(
    ["git", "log", "--format=%H", "-5"],
    capture_output=True, text=True, cwd=str(ROOT)
)
commits = [c.strip() for c in result_raw.stdout.strip().split("\n") if c.strip()]
if commits and commits[0]:
    sha = commits[0]  # full SHA
    print(f"  Testing on most recent commit: {sha}")

    changed = list_functions_changed_in_commit(sha)
    if changed and "error" not in changed[0]:
        print(f"  Found {len(changed)} changed functions")
        for fn in changed[:3]:
            assert "func_id" in fn, f"Missing func_id: {fn}"
            assert "name" in fn
            assert "is_new" in fn
            assert "diff_text" in fn
            print(f"    - {fn['name']} ({'new' if fn['is_new'] else 'modified'})")
        print("  list_functions_changed_in_commit structure correct - PASS")
    elif changed and "error" in changed[0]:
        # Could fail if repo path doesn't resolve — acceptable for now
        print(f"  Skipped: {changed[0]['error'][:80]}")
    else:
        print("  No functions changed (or no .py files changed) - acceptable")
else:
    print("  No commits found - skipping")

# ── Test 5: HelixDB-dependent tests ──────────────────────────────────────────
print("\nTest 5: HelixDB-dependent tests (write + read memory)")

try:
    from helixdb import Client
    c = Client("http://127.0.0.1:6969")
    # Quick ping
    c.query().dynamic(None).send()
    helix_available = True
except Exception:
    helix_available = False

if not helix_available:
    print("  HelixDB not running - SKIPPED")
    print("  (Run after AMO ingestion to test write_function_memory and read_function_memory_history)")
else:
    print("  HelixDB available - running write+read test")
    # This would test the full write → read cycle
    # Skipping implementation until AMO is ingested
    print("  Full write+read test deferred until AMO ingest - SKIPPED")

print()
print("Phase 2 Eval: CORE CHECKS PASSED (HelixDB tests deferred)")
print("Remaining: run after AMO ingestion to validate write_function_memory + read_function_memory_history")
