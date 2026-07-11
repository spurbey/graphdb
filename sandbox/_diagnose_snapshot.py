"""Diagnose why export_snapshot misses across all retrieval modes."""
import json
import random as _random
from pathlib import Path
import numpy as np
import igraph as ig

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
    G.vs[i]["id"]     = n["id"]
    G.vs[i]["file"]   = n["file"]
    G.vs[i]["name"]   = n["name"]
    G.vs[i]["status"] = n.get("status", "superseded")
    emb = n.get("embedding", [])
    G.vs[i]["embedding"] = np.array(emb, dtype=np.float32) if has_nonzero(emb) else None

edge_tuples = [(id_to_idx[e["source"]], id_to_idx[e["target"]]) for e in edges_data]
G.add_edges(edge_tuples)
for i, e in enumerate(edges_data):
    G.es[i]["type"]            = e["type"]
    G.es[i]["co_change_count"] = e.get("co_change_count", 0)
    G.es[i]["category"]        = e.get("category", "")

calls_edge_ids = [e.index for e in G.es if e["type"] == "CALLS"]
calls_only = G.subgraph_edges(calls_edge_ids, delete_vertices=False)

_random.seed(42)
np.random.seed(42)
_communities = calls_only.community_infomap()
for _i, _c in enumerate(_communities.membership):
    G.vs[_i]["cluster_id"] = _c
print(f"Infomap: {len(set(_communities.membership))} communities")


def _is_candidate(v) -> bool:
    name      = v["name"] or ""
    file      = v["file"] or ""
    file_base = file.replace("\\", "/").split("/")[-1]
    return (
        v["status"] == "active"
        and v["embedding"] is not None
        and not name.startswith("test_")
        and not file_base.startswith("test_")
    )


# ── 1. export_snapshot nodes ──────────────────────────────────────────────────
print("\n=== export_snapshot nodes ===")
export_nodes = [v for v in G.vs if v["name"] == "export_snapshot"]
print(f"Count: {len(export_nodes)}")
for v in export_nodes:
    idx = v.index
    print(f"  [{idx}] {v['file']}")
    print(f"    status={v['status']}  has_emb={v['embedding'] is not None}"
          f"  cluster={G.vs[idx]['cluster_id']}  is_candidate={_is_candidate(v)}")

# ── 2. Snapshot-named candidates ─────────────────────────────────────────────
print("\n=== Active nodes with 'snapshot' in name ===")
for v in G.vs:
    if "snapshot" in (v["name"] or "").lower():
        file_base = (v["file"] or "").replace("\\", "/").split("/")[-1]
        print(f"  {v['name']} [{file_base}] status={v['status']} candidate={_is_candidate(v)}")

# ── 3. Connectivity of export_snapshot (snapshots.py) ────────────────────────
print("\n=== Connectivity of export_snapshot ===")
for v in export_nodes:
    if "snapshots" not in v["file"]:
        continue
    idx = v.index
    callers   = [G.vs[e.source]["name"] for e in G.es if e.target == idx and e["type"] == "CALLS"]
    callees   = [G.vs[e.target]["name"] for e in G.es if e.source == idx and e["type"] == "CALLS"]
    cochanges = [
        (G.vs[e.source if e.target == idx else e.target]["name"], e["category"])
        for e in G.es
        if (e.source == idx or e.target == idx) and e["type"] == "CO_CHANGE"
    ]
    print(f"  cluster   : {G.vs[idx]['cluster_id']}")
    print(f"  callers   : {callers[:10] or '(none)'}")
    print(f"  callees   : {callees[:10] or '(none)'}")
    print(f"  co-changes: {cochanges[:10] or '(none)'}")

# ── 4. What clusters do the stored vector top-10 seeds land in? ───────────────
ablation = json.loads((ROOT / "sandbox" / "ablation_results.json").read_text(encoding="utf-8"))
snap_q = next(q for q in ablation["per_query"] if q["id"] == "old_snapshot")
print(f"\n=== Seed community analysis for: '{snap_q['query']}' ===")
print(f"Stored vector top-10: {snap_q['top10_vector']}")
print(f"Stored rank: {snap_q['rank_vector_only']}")

cluster_counts: dict[int, int] = {}
for entry in snap_q["top10_vector"]:
    name = entry.split(" [")[0]
    matches = [v for v in G.vs if v["name"] == name and _is_candidate(v)]
    for v in matches:
        cid = G.vs[v.index]["cluster_id"]
        cluster_counts[cid] = cluster_counts.get(cid, 0) + 1
        print(f"  seed {name}: cluster {cid}")

for v in export_nodes:
    if "snapshots" in v["file"]:
        es_cluster = G.vs[v.index]["cluster_id"]
        print(f"\nexport_snapshot cluster : {es_cluster}")
        print(f"Seed clusters           : {cluster_counts}")
        print(f"export_snapshot in seed clusters: {es_cluster in cluster_counts}")
        if es_cluster not in cluster_counts:
            weight = 0.0
        else:
            weight = cluster_counts[es_cluster] / sum(cluster_counts.values())
        print(f"Soft reset weight for export_snapshot cluster: {weight:.4f}")
        if weight == 0:
            print("=> ROOT CAUSE: export_snapshot's community has 0 seeds.")
            print("   Soft reset assigns it zero teleportation mass.")
            print("   No PPR mass flows in => rank stays near bottom.")
