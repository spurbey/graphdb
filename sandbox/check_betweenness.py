"""
Check 1: Are betweenness scores on the AMO CALLS graph meaningful?

If the top-10 by betweenness are recognizable architectural hubs
(memory_write, add_event, create_session, etc.) — scores are useful.
If they're generic utilities (_utc_now, _id, execute) — graph slice too thin.

Also checks: degree distribution, community structure, and whether
combining betweenness + degree gives better hub identification than either alone.
"""
import json
import random as _r
import numpy as np
import igraph as ig
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = ROOT / "sandbox"

print("Loading graph...")
with open(SANDBOX / "amo_nodes.json", encoding="utf-8") as f:
    nodes_data = json.load(f)
with open(SANDBOX / "amo_edges.json", encoding="utf-8") as f:
    edges_data = json.load(f)

node_id_set = {n["id"] for n in nodes_data}
edges_data = [e for e in edges_data if e["source"] in node_id_set and e["target"] in node_id_set]

G = ig.Graph(directed=True)
node_ids = [n["id"] for n in nodes_data]
G.add_vertices(len(node_ids))
id_to_idx = {nid: i for i, nid in enumerate(node_ids)}

for i, n in enumerate(nodes_data):
    G.vs[i]["name"]   = n["name"]
    G.vs[i]["file"]   = n["file"]
    G.vs[i]["status"] = n.get("status", "superseded")

edge_tuples = [(id_to_idx[e["source"]], id_to_idx[e["target"]]) for e in edges_data]
G.add_edges(edge_tuples)
for i, e in enumerate(edges_data):
    G.es[i]["type"] = e["type"]

# CALLS-only subgraph
calls_ids = [e.index for e in G.es if e["type"] == "CALLS"]
calls_only = G.subgraph_edges(calls_ids, delete_vertices=False)

# Active non-test functions only
def is_candidate(v):
    name = v["name"] or ""
    file = v["file"] or ""
    file_base = file.replace("\\", "/").split("/")[-1]
    return (
        v["status"] == "active"
        and not name.startswith("test_")
        and not file_base.startswith("test_")
    )

active_indices = [v.index for v in G.vs if is_candidate(v)]
n_active = len(active_indices)
print(f"Active non-test nodes: {n_active}")

# ── Betweenness centrality ────────────────────────────────────────────────────
print("\nComputing betweenness centrality on CALLS graph...")
# betweenness() on the full directed graph — only meaningful for active nodes
betweenness = calls_only.betweenness(directed=True)

# Get top-20 by betweenness for active nodes only
active_betweenness = [
    (betweenness[i], G.vs[i]["name"], G.vs[i]["file"].split("/")[-1])
    for i in active_indices
    if betweenness[i] > 0
]
active_betweenness.sort(reverse=True)

print("\nTop-20 by betweenness centrality (active, non-test):")
for score, name, fname in active_betweenness[:20]:
    print(f"  {score:>10.1f}  {name} [{fname}]")

# ── Degree (in+out) on CALLS ──────────────────────────────────────────────────
degree_in  = calls_only.indegree()
degree_out = calls_only.outdegree()
degree_total = [degree_in[i] + degree_out[i] for i in range(G.vcount())]

active_degree = [
    (degree_total[i], G.vs[i]["name"], G.vs[i]["file"].split("/")[-1])
    for i in active_indices
    if degree_total[i] > 0
]
active_degree.sort(reverse=True)

print("\nTop-20 by degree (in+out CALLS, active non-test):")
for score, name, fname in active_degree[:20]:
    print(f"  {score:>4}  {name} [{fname}]")

# ── Combined score: betweenness × degree ─────────────────────────────────────
# Normalize both to [0,1] then multiply
if active_betweenness:
    max_b = active_betweenness[0][0]
    max_d = active_degree[0][0]

    combined = []
    for i in active_indices:
        b = betweenness[i] / max_b if max_b > 0 else 0
        d = degree_total[i] / max_d if max_d > 0 else 0
        combined.append((b * d, G.vs[i]["name"], G.vs[i]["file"].split("/")[-1], b, d))
    combined.sort(reverse=True)

    print("\nTop-20 by betweenness × degree (combined architectural importance):")
    for score, name, fname, b_norm, d_norm in combined[:20]:
        print(f"  {score:.4f}  {name} [{fname}]  (b={b_norm:.3f}, d={d_norm:.3f})")

# ── Infomap communities ───────────────────────────────────────────────────────
_r.seed(42); np.random.seed(42)
communities = calls_only.community_infomap()
n_communities = len(set(communities.membership))
print(f"\nInfomap: {n_communities} communities")

# Community sizes
sizes = Counter(communities.membership)
top_communities = sorted(sizes.items(), key=lambda x: x[1], reverse=True)[:10]
print("Top-10 community sizes:")
for cid, size in top_communities:
    members = [G.vs[i]["name"] for i in range(G.vcount())
               if communities.membership[i] == cid
               and is_candidate(G.vs[i])][:5]
    print(f"  community {cid}: {size} nodes — {members}")

# ── Betweenness distribution insight ─────────────────────────────────────────
print("\nBetweenness distribution:")
b_values = [betweenness[i] for i in active_indices if betweenness[i] > 0]
if b_values:
    print(f"  Non-zero: {len(b_values)}/{n_active} ({100*len(b_values)//n_active}%)")
    print(f"  Max: {max(b_values):.0f}")
    print(f"  Median (non-zero): {sorted(b_values)[len(b_values)//2]:.1f}")
    print(f"  >1000: {sum(1 for b in b_values if b > 1000)}")
    print(f"  >100:  {sum(1 for b in b_values if b > 100)}")
    print(f"  >10:   {sum(1 for b in b_values if b > 10)}")

# ── Verdict ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("VERDICT")
print("=" * 60)
top5_names = [name for _, name, _ in active_betweenness[:5]]
expected_hubs = {"memory_write", "add_event", "create_session", "ingest_hook_payload",
                 "memory_import", "memory_export", "search_memories", "add_memory_unit",
                 "ingest_transcript", "memory_search"}
overlap = set(top5_names) & expected_hubs
if len(overlap) >= 2:
    print(f"Betweenness scores look MEANINGFUL — {len(overlap)}/5 top functions are expected hubs")
    print(f"Useful for test selection and commit review severity scoring.")
else:
    print(f"Betweenness scores may be UNRELIABLE — only {len(overlap)}/5 top functions are expected hubs")
    print(f"100-commit slice may be too thin. Consider using degree as primary metric instead.")
print()
print("For commit_review tool:")
print("  betweenness: rank which changed functions have broadest structural impact")
print("  degree: simpler proxy if betweenness is unreliable")
print("  combined b×d: robustness check — if both agree, high confidence in ranking")
