import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[0]
sys.path.insert(0, str(ROOT))
from pipeline_api import initialize, commit_review, select_tests, status

print("Initializing...")
initialize(data_root=ROOT)
st = status()
print(f"Pipeline: {st['pipeline']}, nodes: {st['nodes']}")

CHANGED = [
    "src/agent_memory_orchestrator/memory/ingest.py::ingest_hook_payload",
    "src/agent_memory_orchestrator/memory/ingest.py::process_event",
    "src/agent_memory_orchestrator/core/privacy.py::redact_secrets",
]

print("\n=== commit_review ===")
results = commit_review(CHANGED)
for r in results:
    if "error" in r:
        print(f"ERROR: {r}"); continue
    print(f"\n{r['name']} [{r['file']}]")
    print(f"  severity={r['severity']}  b={r['betweenness']}  drift={r['drift']}")
    print(f"  scope={r['test_scope']}  reason={r['reason']}")
    print(f"  callers({len(r['blast_radius'])}): {r['blast_radius'][:4]}")
    print(f"  territory({len(r['ppr_territory'])}): {r['ppr_territory'][:4]}")
    if r['co_change_warnings']: print(f"  WARN: {r['co_change_warnings']}")

print("\n=== select_tests ===")
sel = select_tests(CHANGED)
if "error" in sel: print(f"ERROR: {sel}")
else:
    print(sel["summary"])
    print(f"Critical: {len(sel['critical'])}  Recommended: {len(sel['recommended'])}  Skippable: {len(sel['skippable'])}")
    if sel["co_change_warnings"]: print(f"CO_CHANGE warnings: {sel['co_change_warnings']}")
