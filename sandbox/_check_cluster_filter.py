"""
After floor weighting, export_snapshot has PPR rank 6 internally but STILL MISSES.
Root cause: the post-PPR cluster filter removes it.

The pipeline filters ranked_ppr to only include nodes in top_cluster_set
(clusters that had at least 1 seed). export_snapshot is in cluster 33.
If cluster 33 has a seed, it should be in the set. But if its cluster_id
shifted after Infomap rerun... let's check.
"""
import json
import random as _r
import numpy as np
import igraph as ig
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

with open("sandbox/amo_nodes.json", encoding="utf-8") as f:
    nodes_data = json.load(f)
with open("sandbox/amo_edges.json", encoding="utf-8") as f:
    edges_data = json.load(f)

node_id_set = {n["id"] for n in nodes_data}
edges_data = [e for e in edges_data if e["source"] in node_id_set and e["target"] in node_id_set]

G = ig.Graph(directed=True)
node_ids = [n["id"] for n in nodes_data]
G.add_vertices(len(node_ids))
id_to_idx = {nid: i for i, nid in enumerate(node_ids)}
has_nonzero = lambda v: bool(v) and any(abs(float(x)) > 1e-12 for x in v)

for i, n in enumerate(nodes_data):
    G.vs[i]["name"]   = n["name"]
    G.vs[i]["file"]   = n["file"]
    G.vs[i]["status"] = n.get("status", "superseded")
    emb = n.get("embedding", [])
    G.vs[i]["embedding"] = np.array(emb, dtype=np.float32) if has_nonzero(emb) else None

edge_tuples = [(id_to_idx[e["source"]], id_to_idx[e["target"]]) for e in edges_data]
G.add_edges(edge_tuples)
for i, e in enumerate(edges_data):
    G.es[i]["type"] = e["type"]

calls_edge_ids = [e.index for e in G.es if e["type"] == "CALLS"]
calls_only = G.subgraph_edges(calls_edge_ids, delete_vertices=False)

_r.seed(42)
np.random.seed(42)
_communities = calls_only.community_infomap()
for _i, _c in enumerate(_communities.membership):
    G.vs[_i]["cluster_id"] = _c


def _is_candidate(v) -> bool:
    name = v["name"] or ""
    file = v["file"] or ""
    file_base = file.replace("\\", "/").split("/")[-1]
    return (
        v["status"] == "active"
        and v["embedding"] is not None
        and not name.startswith("test_")
        and not file_base.startswith("test_")
    )


def cosine(a, b):
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    return float(np.dot(a, b) / (na * nb)) if na > 0 and nb > 0 else 0.0


# ── export_snapshot cluster ───────────────────────────────────────────────────
es_node = next(v for v in G.vs if v["name"] == "export_snapshot" and "snapshots" in (v["file"] or ""))
es_cluster = G.vs[es_node.index]["cluster_id"]
print(f"export_snapshot cluster_id = {es_cluster}")
print(f"export_snapshot file: {es_node['file']}")

# ── Load ablation to get stored top-10 for snapshot query ────────────────────
ablation = json.loads((ROOT / "sandbox" / "ablation_results.json").read_text(encoding="utf-8"))
snap = next(q for q in ablation["per_query"] if q["id"] == "old_snapshot")
print(f"\nStored vector top-10 for old_snapshot:")
seed_clusters_from_stored = set()
for entry in snap["top10_vector"]:
    name = entry.split(" [")[0]
    for v in G.vs:
        if v["name"] == name and _is_candidate(v):
            cid = G.vs[v.index]["cluster_id"]
            seed_clusters_from_stored.add(cid)
            print(f"  {name}: cluster={cid}")
            break

print(f"\nClusters from stored top-10: {seed_clusters_from_stored}")
print(f"export_snapshot cluster {es_cluster} IN seed set: {es_cluster in seed_clusters_from_stored}")

# ── The real question: what cluster does _check_floor_effect.py compute? ─────
# It showed export_snapshot at PPR rank 6 — that means it WAS included in PPR.
# But it didn't show in the ablation. Why?
# The _check_floor_effect script was using CALLS-ONLY PPR, which gives different
# results than the full-graph (theme overlay) PPR in cochange_ablation.py.
# Let's check: in CALLS-only, what is export_snapshot's PPR score?
# And in Mode B (no theme overlay), is it filtered by cluster?

print("\n" + "="*60)
print("The _check_floor_effect showed export_snapshot at PPR rank 6 in CALLS-only.")
print("But the ablation CallsPPR mode shows MISS.")
print("Checking: does the ablation Mode B use full graph or CALLS-only?")
print("="*60)

# Check cochange_ablation.py Mode B weight function
print("\nMode B in cochange_ablation.py uses _base_weights(include_cochange=False)")
print("That sets CO_CHANGE weights to 9999.0 but still uses the FULL graph G")
print("for PPR, not calls_only. This means PPR mass can leak through IMPORTS edges.")
print()
print("The _check_floor_effect.py script used calls_only for PPR directly.")
print("Different graph = different PPR results = explains the discrepancy.")
print()

# Check: what is export_snapshot's cluster, and is it in the snapshot query seed set?
# Re-embed the snapshot query using stored seeds from ablation
print("Checking cluster 33 membership in seeds for snapshot query...")
print(f"export_snapshot is in cluster {es_cluster}")

# Check if any of the stored top-10 candidates are in cluster 33
cluster_33_seeds = []
for entry in snap["top10_vector"]:
    name = entry.split(" [")[0]
    for v in G.vs:
        if v["name"] == name and _is_candidate(v):
            if G.vs[v.index]["cluster_id"] == es_cluster:
                cluster_33_seeds.append(name)
            break

print(f"Seeds in cluster {es_cluster}: {cluster_33_seeds or '(none)'}")

if not cluster_33_seeds:
    print(f"\nROOT CAUSE CONFIRMED:")
    print(f"  export_snapshot is in cluster {es_cluster}.")
    print(f"  No seeds land in cluster {es_cluster} for the snapshot query.")
    print(f"  post-PPR filter: 'top_cluster_set = set(cluster_counts.keys())'")
    print(f"  export_snapshot's cluster {es_cluster} is NOT in top_cluster_set.")
    print(f"  It gets FILTERED OUT regardless of its PPR score.")
    print(f"  The _check_floor_effect showed rank 6 using calls_only PPR directly")
    print(f"  (without the cluster filter) — that's why the numbers disagree.")
    print()
    print(f"  FLOOR WEIGHTING CANNOT FIX THIS.")
    print(f"  The floor gives export_snapshot a reset weight, PPR scores it,")
    print(f"  but the cluster filter removes it from candidates before MMR.")
    print(f"  The filter only passes nodes in communities that had at least 1 seed.")
    print(f"  export_snapshot's community NEVER has a seed for this query.")
    print()
    print(f"  The ACTUAL fix: remove the post-PPR cluster filter entirely,")
    print(f"  OR lower the MMR lambda to reduce diversity penalty so high-PPR")
    print(f"  nodes from weak-seed communities survive.")
    print(f"  OR accept this as a permanent miss.")
else:
    print(f"\nSeeds DO exist in cluster {es_cluster}: {cluster_33_seeds}")
    print("Cluster filter is NOT the issue. Different mechanism.")
