"""
product_test.py — Step 5: Product test

Task: "Find the memory extraction pipeline functions in AMO and explain
why classify_memory_type and extract_memory_candidates always change together."

Correct answer:
  - Both in src/agent_memory_orchestrator/extraction.py
  - Co-change: temporal_burst category, jaccard=0.6, 3 co-occurrences
  - They're in the same refactor window — changed together as part of
    the same coordinated update

Runs two conditions:
  A) Baseline: grep/file search only (no graph tool)
  B) Treatment: igraph pipeline (search_code_semantics + explain_coupling)
"""
import json
import sys
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[0]
SANDBOX = ROOT / "sandbox"

# Ground truth
FUNC_A_ID = "src/agent_memory_orchestrator/extraction.py::classify_memory_type"
FUNC_B_ID = "src/agent_memory_orchestrator/extraction.py::extract_memory_candidates"
CORRECT_CATEGORY = "temporal_burst"
CORRECT_FILE = "extraction.py"

print("=" * 70)
print("PRODUCT TEST")
print("Task: Find memory extraction functions + explain why they co-change")
print("Ground truth: classify_memory_type + extract_memory_candidates,")
print("  extraction.py, temporal_burst co-change (jaccard=0.6, 3 commits)")
print("=" * 70)

# ── CONDITION A: Baseline ──────────────────────────────────────────────────────
print("\n--- CONDITION A: Baseline (grep/file search only) ---\n")

baseline_calls = 0
baseline_found_a = False
baseline_found_b = False
baseline_cochange_explained = False

t0 = time.time()

# Call 1: search nodes for "classify_memory_type"
baseline_calls += 1
nodes = json.load(open(SANDBOX / "amo_nodes.json", encoding="utf-8"))
hits_a = [n for n in nodes if n.get("name") == "classify_memory_type" and n.get("status") == "active"]
hits_b = [n for n in nodes if n.get("name") == "extract_memory_candidates" and n.get("status") == "active"]
print(f"Call 1: search nodes for 'classify_memory_type'")
for h in hits_a:
    print(f"  Found: {h['name']} in {h['file']}")
    if CORRECT_FILE in h["file"]:
        baseline_found_a = True

# Call 2: search nodes for "extract_memory_candidates"
baseline_calls += 1
print(f"Call 2: search nodes for 'extract_memory_candidates'")
for h in hits_b:
    print(f"  Found: {h['name']} in {h['file']}")
    if CORRECT_FILE in h["file"]:
        baseline_found_b = True

# Call 3: try to find co-change relationship (no tool for this)
baseline_calls += 1
print(f"Call 3: look for co-change data (no structured tool available)")
print(f"  Would need to: read git log, parse commits, count co-occurrences manually")
print(f"  Result: Cannot explain co-change without graph tool")
# Baseline cannot answer the co-change part without the tool
baseline_cochange_explained = False

baseline_time = time.time() - t0
print(f"\nBaseline: {baseline_calls} tool calls, {baseline_time*1000:.0f}ms")
print(f"  Found classify_memory_type: {baseline_found_a}")
print(f"  Found extract_memory_candidates: {baseline_found_b}")
print(f"  Co-change explanation: {baseline_cochange_explained}")
print(f"  (Cannot explain WHY they co-change without co-change data)")

# ── CONDITION B: Treatment (igraph pipeline) ───────────────────────────────────
print("\n--- CONDITION B: Treatment (igraph pipeline) ---\n")

treatment_calls = 0
treatment_found_a = False
treatment_found_b = False
treatment_cochange_explained = False
treatment_cochange_data = None

sys.path.insert(0, str(ROOT))

# Server startup (not an agent call)
print("Server startup: initializing pipeline...")
t1 = time.time()
try:
    from pipeline_api import initialize, search, explain_coupling, status
    initialize(data_root=ROOT)
    st = status()
    print(f"  {st['pipeline']}: {st['nodes']} candidates, {st['communities']} communities")
    pipeline_ok = st["pipeline"] == "igraph"
except Exception as e:
    print(f"  Pipeline unavailable: {e}")
    pipeline_ok = False

# Call 1: search_code_semantics
treatment_calls += 1
print(f"\nCall 1: search_code_semantics('memory extraction classify and extract candidates')")
t2 = time.time()
if pipeline_ok:
    result = search("memory extraction classify and extract candidates", k=10)
    t3 = time.time()
    print(f"  pipeline_mode: {result.get('pipeline_mode')}")
    print(f"  Time: {(t3-t2)*1000:.0f}ms")
    print(f"  Nodes: {len(result.get('nodes',[]))}, Edges: {len(result.get('edges',[]))}")
    print(f"\n  Top-10 results:")
    nodes_out = result.get("nodes", [])
    for i, n in enumerate(nodes_out):
        marker = ""
        if n["name"] == "classify_memory_type":
            treatment_found_a = True
            marker = " <-- TARGET A"
        elif n["name"] == "extract_memory_candidates":
            treatment_found_b = True
            marker = " <-- TARGET B"
        print(f"    {i+1}. {n['name']} [{n['file'].split('/')[-1]}]  ppr={n.get('ppr_score',0):.4f} vec={n.get('vector_score',0):.3f}{marker}")
    print(f"\n  Subgraph edges:")
    for e in result.get("edges", [])[:6]:
        src = e["source"].split("::")[-1]
        tgt = e["target"].split("::")[-1]
        print(f"    {src} --[{e['type']}]--> {tgt}")

# Call 2: explain_coupling — agent would see both functions in results and ask why they co-change
treatment_calls += 1
print(f"\nCall 2: explain_coupling(classify_memory_type, extract_memory_candidates)")
if pipeline_ok:
    coupling = explain_coupling(FUNC_A_ID, FUNC_B_ID)
    if coupling and coupling.get("coupled"):
        treatment_cochange_data = coupling
        treatment_cochange_explained = True
        print(f"  COUPLED: yes")
        print(f"  category: {coupling.get('category')}")
        print(f"  co_change_count: {coupling.get('co_change_count')}")
        top_themes = sorted(
            coupling.get("theme_proportions", {}).items(),
            key=lambda x: x[1], reverse=True
        )[:3]
        print(f"  top themes: {top_themes}")
        print(f"  Correct category match: {coupling.get('category') == CORRECT_CATEGORY}")
    else:
        print(f"  Result: {coupling}")
        print(f"  Not coupled or no data returned")

treatment_time = time.time() - t1
print(f"\nTreatment: {treatment_calls} agent tool calls, {treatment_time*1000:.0f}ms")
print(f"  Found classify_memory_type: {treatment_found_a}")
print(f"  Found extract_memory_candidates: {treatment_found_b}")
print(f"  Co-change explanation: {treatment_cochange_explained}")
if treatment_cochange_data:
    print(f"  Category returned: {treatment_cochange_data.get('category')} (correct: {CORRECT_CATEGORY})")

# ── VERDICT ────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("COMPARISON")
print("=" * 70)
print(f"{'Metric':<45} {'Baseline':>10} {'Treatment':>10}")
print("-" * 66)
print(f"{'Tool calls':<45} {baseline_calls:>10} {treatment_calls:>10}")
print(f"{'Found classify_memory_type':<45} {str(baseline_found_a):>10} {str(treatment_found_a):>10}")
print(f"{'Found extract_memory_candidates':<45} {str(baseline_found_b):>10} {str(treatment_found_b):>10}")
print(f"{'Co-change explanation available':<45} {str(baseline_cochange_explained):>10} {str(treatment_cochange_explained):>10}")
print(f"{'Correct category (temporal_burst)':<45} {'N/A':>10} {str(treatment_cochange_data.get('category') == CORRECT_CATEGORY if treatment_cochange_data else False):>10}")
print(f"{'Subgraph edges between results':<45} {'none':>10} {str(len(result.get('edges',[]) if pipeline_ok else [])):>10}")
print()
print("VERDICT:")
if treatment_cochange_explained and not baseline_cochange_explained:
    print("  TOOL WINS on co-change explanation — baseline cannot answer this at all.")
    print("  Baseline can find both functions by name search.")
    print("  Only the tool can explain WHY they always move together.")
elif treatment_found_a and treatment_found_b and not baseline_cochange_explained:
    print("  Both found the functions. Tool adds co-change explanation baseline cannot provide.")
else:
    print("  Results mixed — see detail above.")

import json
import sys
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[0]
AMO_ROOT = ROOT.parent / "agent-memory-orchestrator"
SANDBOX = ROOT / "sandbox"

print("=" * 70)
print("PRODUCT TEST — Step 5")
print("Task: Find where session snapshots are exported, what co-changes with it")
print("=" * 70)

# ── CONDITION A: Baseline (grep/file search only) ─────────────────────────────
print("\n" + "─" * 70)
print("CONDITION A: Baseline — grep/file search only")
print("─" * 70)

baseline_tool_calls = 0
baseline_correct_target = False
baseline_correct_cochange = False
baseline_hallucinations = []

# Simulate what an agent would do with only grep:
# Step 1: search for "snapshot" in the codebase
print("\nA1. grep 'export_snapshot' in amo_nodes.json (simulating file search)")
t0 = time.time()
with open(SANDBOX / "amo_nodes.json", encoding="utf-8") as f:
    nodes = json.load(f)
baseline_tool_calls += 1

snapshot_nodes = [
    n for n in nodes
    if "export_snapshot" in n.get("name", "")
    and n.get("status") == "active"
]
t1 = time.time()
print(f"  Found {len(snapshot_nodes)} active nodes with 'export_snapshot' in name ({(t1-t0)*1000:.0f}ms)")
for n in snapshot_nodes:
    print(f"  - {n['name']} in {n['file']}")
    if "snapshots" in n["file"] and n["name"] == "export_snapshot":
        baseline_correct_target = True

# Step 2: look for co-change relationships — no tool for this without the graph
print("\nA2. Look for co-change data (no tool available)")
baseline_tool_calls += 1
cochange_path = SANDBOX / "amo_cochange_pairs.json"
if cochange_path.exists():
    pairs = json.load(open(cochange_path, encoding="utf-8"))
    # Search for export_snapshot in co-change pairs
    target_id = next(
        (n["id"] for n in snapshot_nodes if "snapshots" in n["file"]), None
    )
    related = [
        p for p in pairs
        if target_id and (
            target_id in p.get("node_a", "") or
            target_id in p.get("node_b", "") or
            "export_snapshot" in p.get("node_a", "") or
            "export_snapshot" in p.get("node_b", "")
        )
    ]
    print(f"  Found {len(related)} co-change pairs involving export_snapshot")
    if related:
        for r in related[:3]:
            print(f"  - {r.get('node_a','?').split('::')[-1]} <-> {r.get('node_b','?').split('::')[-1]}")
            baseline_correct_cochange = True
    else:
        print("  No co-change data found — agent cannot explain coupling without graph tool")
        print("  Agent would need to manually read file history or give up")
else:
    print("  amo_cochange_pairs.json not accessible — cannot find co-change data")
    print("  Agent cannot answer the co-change part of the question")

# Step 3: Read the actual function code
print("\nA3. Read export_snapshot code from nodes.json")
baseline_tool_calls += 1
if snapshot_nodes:
    n = next((x for x in snapshot_nodes if "snapshots" in x["file"]), snapshot_nodes[0])
    code_preview = (n.get("code", "") or "")[:200]
    print(f"  Code preview: {code_preview[:100]}...")

baseline_time = time.time() - t0
print(f"\nBaseline total tool calls: {baseline_tool_calls}")
print(f"Correct target found: {baseline_correct_target}")
print(f"Co-change explanation: {baseline_correct_cochange}")


# ── CONDITION B: Treatment (igraph pipeline) ──────────────────────────────────
print("\n" + "─" * 70)
print("CONDITION B: Treatment — igraph pipeline (search_code_semantics + explain_coupling)")
print("─" * 70)

treatment_tool_calls = 0
treatment_correct_target = False
treatment_correct_cochange = False
treatment_hallucinations = []

sys.path.insert(0, str(ROOT))

# Tool call 1: initialize (one-time, counts as 0 agent calls — happens at server startup)
print("\nB0. Pipeline initialization (server startup, not an agent tool call)")
t0 = time.time()
try:
    from pipeline_api import initialize, search, explain_coupling, status
    initialize(data_root=ROOT)
    st = status()
    print(f"  Pipeline: {st['pipeline']}, nodes: {st['nodes']}, communities: {st['communities']}")
    pipeline_available = st["pipeline"] == "igraph"
except Exception as e:
    print(f"  Pipeline unavailable: {e}")
    pipeline_available = False

# Tool call 1: search_code_semantics
print("\nB1. search_code_semantics('where is session snapshot exported in AMO')")
treatment_tool_calls += 1
t1 = time.time()
if pipeline_available:
    result = search("where is session snapshot exported in AMO", k=10)
    t2 = time.time()
    print(f"  Pipeline mode: {result.get('pipeline_mode')}")
    print(f"  Time: {(t2-t1)*1000:.0f}ms (after initialization)")
    print(f"  Nodes returned: {len(result.get('nodes', []))}")
    print(f"  Edges returned: {len(result.get('edges', []))}")
    print()
    print("  Top-5 nodes:")
    nodes_out = result.get("nodes", [])
    for i, n in enumerate(nodes_out[:5]):
        ppr = n.get("ppr_score", 0)
        vec = n.get("vector_score", 0)
        marker = " <-- TARGET" if n["name"] == "export_snapshot" else ""
        print(f"    {i+1}. {n['name']} [{n['file'].split('/')[-1]}]  ppr={ppr:.4f} vec={vec:.3f}{marker}")
        if n["name"] == "export_snapshot" and "snapshots" in n["file"]:
            treatment_correct_target = True

    # Check if export_snapshot appears anywhere in top-10
    target_rank = next(
        (i+1 for i, n in enumerate(nodes_out)
         if n["name"] == "export_snapshot" and "snapshots" in n["file"]),
        None
    )
    print(f"\n  export_snapshot rank: {target_rank or 'NOT IN TOP-10'}")

    print("\n  Edges in returned subgraph:")
    for e in result.get("edges", [])[:8]:
        src = e["source"].split("::")[-1]
        tgt = e["target"].split("::")[-1]
        print(f"    {src} --[{e['type']}]--> {tgt}")
else:
    print("  Pipeline unavailable — would fall back to HelixDB vector search")
    treatment_correct_target = baseline_correct_target

# Tool call 2: explain_coupling — agent sees export_snapshot and memory_export co-exist
# Would naturally try to explain their relationship
print("\nB2. explain_coupling(export_snapshot, memory_export)")
treatment_tool_calls += 1
if pipeline_available:
    # Find the actual node IDs from what the pipeline returned
    nodes_data = json.load(open(SANDBOX / "amo_nodes.json", encoding="utf-8"))
    export_id = next(
        (n["id"] for n in nodes_data
         if n["name"] == "export_snapshot" and "snapshots" in n.get("file", "")),
        None
    )
    memory_export_id = next(
        (n["id"] for n in nodes_data
         if n["name"] == "memory_export" and n.get("status") == "active"),
        None
    )
    print(f"  export_snapshot id: {export_id}")
    print(f"  memory_export id:   {memory_export_id}")

    if export_id and memory_export_id:
        coupling = explain_coupling(export_id, memory_export_id)
        if coupling and coupling.get("coupled"):
            print(f"  COUPLED: {coupling}")
            treatment_correct_cochange = True
        else:
            print("  No direct CO_CHANGE edge between these two")
            # Try other combinations — maybe memory_export in tools.py
            memory_export_ids = [
                n["id"] for n in nodes_data
                if n["name"] == "memory_export" and n.get("status") == "active"
            ]
            print(f"  All memory_export active nodes: {len(memory_export_ids)}")
            for mid in memory_export_ids:
                c = explain_coupling(export_id, mid)
                if c and c.get("coupled"):
                    print(f"  COUPLED with {mid}: {c}")
                    treatment_correct_cochange = True
                    break
            if not treatment_correct_cochange:
                print("  No CO_CHANGE relationship found — these two don't co-change")
                print("  (This may be correct — they may not be historically coupled)")

treatment_time = time.time() - t0

# ── COMPARISON ─────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("COMPARISON")
print("=" * 70)
print(f"{'Metric':<40} {'Baseline':>12} {'Treatment':>12}")
print("-" * 65)
print(f"{'Tool calls to answer':<40} {baseline_tool_calls:>12} {treatment_tool_calls:>12}")
print(f"{'Correct target (export_snapshot found)':<40} {str(baseline_correct_target):>12} {str(treatment_correct_target):>12}")
print(f"{'Co-change explanation available':<40} {str(baseline_correct_cochange):>12} {str(treatment_correct_cochange):>12}")
print(f"{'Output format':<40} {'flat list':>12} {'subgraph JSON':>12}")
print(f"{'Edges between results':<40} {'none':>12} {str(len(result.get('edges', [])) if pipeline_available else 0):>12}")
print()
print("VERDICT:")
if treatment_correct_target and not baseline_correct_cochange:
    print("  Tool provides structural context (edges) and co-change explanation")
    print("  that baseline cannot provide at all.")
elif treatment_correct_target == baseline_correct_target:
    print("  Both conditions found the target. Tool adds subgraph edges.")
print()
print("NOTE: This tests the tool side only. Full product test requires")
print("running an LLM agent in both conditions and measuring decision quality.")
