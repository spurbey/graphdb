"""Check CALLS connectivity for _filter_answer_grade_nodes."""
import json
from pathlib import Path
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

for i, n in enumerate(nodes_data):
    G.vs[i]["name"] = n["name"]
    G.vs[i]["file"] = n["file"]
    G.vs[i]["status"] = n.get("status", "superseded")

edge_tuples = [(id_to_idx[e["source"]], id_to_idx[e["target"]]) for e in edges_data]
G.add_edges(edge_tuples)
for i, e in enumerate(edges_data):
    G.es[i]["type"] = e["type"]

seed_candidates = [v for v in G.vs if v["name"] == "rebuild_graph_cache"]
target_candidates = [v for v in G.vs if v["name"] == "_filter_answer_grade_nodes"]

print(f"rebuild_graph_cache instances: {len(seed_candidates)}")
for v in seed_candidates:
    print(f"  [{v.index}] {v['file']}  status={v['status']}")

print(f"\n_filter_answer_grade_nodes instances: {len(target_candidates)}")
for v in target_candidates:
    print(f"  [{v.index}] {v['file']}  status={v['status']}")

# Use active instances
seed_v = next((v for v in seed_candidates if "service" in v["file"] and v["status"] == "active"), seed_candidates[0])
target_v = next((v for v in target_candidates if v["status"] == "active"), target_candidates[0])
seed_idx = seed_v.index
target_idx = target_v.index

print(f"\nUsing: rebuild_graph_cache [{seed_v['file']}]")
print(f"Using: _filter_answer_grade_nodes [{target_v['file']}]")

# Direct CALLS edge
direct_eid = G.get_eid(seed_idx, target_idx, directed=True, error=False)
has_direct = direct_eid != -1 and G.es[direct_eid]["type"] == "CALLS"
print(f"\nDirect CALLS edge rebuild_graph_cache -> _filter_answer_grade_nodes: {has_direct}")

# All out-CALLS from rebuild_graph_cache
out_calls = [e for e in G.es if e.source == seed_idx and e["type"] == "CALLS"]
print(f"Total out-CALLS from rebuild_graph_cache: {len(out_calls)}")
if out_calls:
    for e in out_calls:
        marker = " <-- TARGET" if e.target == target_idx else ""
        print(f"  -> {G.vs[e.target]['name']} [{G.vs[e.target]['file'].split('/')[-1]}] (status={G.vs[e.target]['status']}){marker}")

# _filter_answer_grade_nodes callers
callers = [G.vs[e.source]["name"] for e in G.es if e.target == target_idx and e["type"] == "CALLS"]
callees = [G.vs[e.target]["name"] for e in G.es if e.source == target_idx and e["type"] == "CALLS"]
print(f"\n_filter_answer_grade_nodes callers ({len(callers)}): {callers}")
print(f"_filter_answer_grade_nodes callees ({len(callees)}): {callees}")

# 2-hop check
print("\n2-hop paths: rebuild_graph_cache -> X -> _filter_answer_grade_nodes")
two_hop = []
for e1 in G.es:
    if e1.source == seed_idx and e1["type"] == "CALLS":
        e2 = G.get_eid(e1.target, target_idx, directed=True, error=False)
        if e2 != -1 and G.es[e2]["type"] == "CALLS":
            two_hop.append(G.vs[e1.target]["name"])
if two_hop:
    print(f"  Found via: {two_hop}")
else:
    print("  None found")

# Verdict
print("\n" + "="*60)
print("VERDICT")
print("="*60)
if has_direct:
    print(f"Mechanism: CALLEE DILUTION")
    print(f"  _filter_answer_grade_nodes is a direct callee of rebuild_graph_cache.")
    print(f"  rebuild_graph_cache has {len(out_calls)} total out-CALLS.")
    print(f"  Each callee gets ~1/{len(out_calls)} of PPR mass from this seed.")
    print(f"  seed floor weighting increases rebuild_graph_cache reset mass.")
    print(f"  More mass -> more flows to _filter_answer_grade_nodes.")
    print(f"  Step 1 floor weighting likely helps this miss.")
elif two_hop:
    print("Mechanism: 2-HOP DILUTION")
    print(f"  Not a direct callee but reachable in 2 hops via {two_hop}.")
    print(f"  Floor weighting may help but effect is weaker (mass diffuses twice).")
else:
    print("Mechanism: NOT REACHABLE FROM rebuild_graph_cache via CALLS")
    print("  Floor weighting alone cannot help. Different approach needed.")
    print("  Check if _filter_answer_grade_nodes has any callers that are seeds.")
    if callers:
        print(f"  Its callers: {callers}")
