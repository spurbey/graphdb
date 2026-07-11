"""Verify cross-cutting query target functions exist and check their communities."""
import json
import random as _r
from pathlib import Path

import numpy as np
import igraph as ig

ROOT = Path(__file__).resolve().parents[1]

with open("sandbox/amo_nodes.json", encoding="utf-8") as f:
    nodes = json.load(f)
with open("sandbox/amo_edges.json", encoding="utf-8") as f:
    edges = json.load(f)

node_id_set = {n["id"] for n in nodes}
edges = [e for e in edges if e["source"] in node_id_set and e["target"] in node_id_set]

G = ig.Graph(directed=True)
node_ids = [n["id"] for n in nodes]
G.add_vertices(len(node_ids))
id_to_idx = {nid: i for i, nid in enumerate(node_ids)}
has_nz = lambda v: bool(v) and any(abs(float(x)) > 1e-12 for x in v)

for i, n in enumerate(nodes):
    G.vs[i]["name"]   = n["name"]
    G.vs[i]["file"]   = n["file"]
    G.vs[i]["status"] = n.get("status", "superseded")
    emb = n.get("embedding", [])
    G.vs[i]["embedding"] = np.array(emb, dtype=np.float32) if has_nz(emb) else None

edge_tuples = [(id_to_idx[e["source"]], id_to_idx[e["target"]]) for e in edges]
G.add_edges(edge_tuples)
for i, e in enumerate(edges):
    G.es[i]["type"] = e["type"]

calls_ids = [e.index for e in G.es if e["type"] == "CALLS"]
calls_only = G.subgraph_edges(calls_ids, delete_vertices=False)
_r.seed(42)
np.random.seed(42)
cm = calls_only.community_infomap()
for _i, _c in enumerate(cm.membership):
    G.vs[_i]["cluster_id"] = _c

targets = [
    "normalize_agent", "_normalize_agent",
    "build_compact_session_graph", "_graph_nodes_from_session",
    "add_memory_unit", "add_memory",
    "write_compact_session_graph",
]

print("Checking candidate cross-cutting targets:")
print()
for name in targets:
    hits = [v for v in G.vs
            if v["name"] == name
            and v["status"] == "active"
            and v["embedding"] is not None]
    if not hits:
        print(f"{name}: NOT FOUND (no active embedding)")
        continue
    for v in hits:
        idx = v.index
        fname = v["file"].split("/")[-1]
        cid = G.vs[idx]["cluster_id"]
        callers = [G.vs[e.source]["name"] for e in G.es
                   if e.target == idx and e["type"] == "CALLS"]
        callees = [G.vs[e.target]["name"] for e in G.es
                   if e.source == idx and e["type"] == "CALLS"]
        # Check if callers/callees span multiple communities (cross-cutting signal)
        caller_clusters = set(G.vs[e.source]["cluster_id"] for e in G.es
                              if e.target == idx and e["type"] == "CALLS")
        callee_clusters = set(G.vs[e.target]["cluster_id"] for e in G.es
                              if e.source == idx and e["type"] == "CALLS")
        print(f"{name} [{fname}]")
        print(f"  cluster_id : {cid}")
        print(f"  callers ({len(callers)}): {callers[:5]}")
        print(f"  callees ({len(callees)}): {callees[:5]}")
        print(f"  caller clusters: {caller_clusters}")
        print(f"  callee clusters: {callee_clusters}")
        all_connected = caller_clusters | callee_clusters
        cross_cutting = len(all_connected) > 1 or (cid not in caller_clusters and len(caller_clusters) > 0)
        print(f"  cross-cutting: {cross_cutting} (spans {len(all_connected)} neighbor clusters)")
        print()
