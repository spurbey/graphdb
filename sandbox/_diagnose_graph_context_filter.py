"""
Step 2 diagnostic: _filter_answer_grade_nodes miss mechanism.

Query: "drop noisy answer grade nodes from current graph context results"
Target: _filter_answer_grade_nodes [service.py]

Per PIPELINE_SPEC.md Step 2 requirements, this script must report:
1. rebuild_graph_cache's raw cosine rank and score
2. Its proportional weight in the reset vector for this query
3. Whether _filter_answer_grade_nodes is a direct CALLS callee of rebuild_graph_cache
4. How many total out-edges (CALLS) rebuild_graph_cache has
5. What the raw cosine rank of _filter_answer_grade_nodes itself is

Output of this script — the raw numbers — must be shown in the Result Log
before any conclusion is written into the Settled Decisions table.
"""
import json
import random as _random
import urllib.request
from pathlib import Path

import numpy as np
import igraph as ig

ROOT = Path(__file__).resolve().parents[1]

# ── Load graph ────────────────────────────────────────────────────────────────
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
    G.es[i]["type"] = e["type"]

calls_edge_ids = [e.index for e in G.es if e["type"] == "CALLS"]
calls_only = G.subgraph_edges(calls_edge_ids, delete_vertices=False)

# Fixed seed — same as running pipeline
_random.seed(42)
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


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    return float(np.dot(a, b) / (na * nb)) if na > 0 and nb > 0 else 0.0


# ── Embed the query via raw API ───────────────────────────────────────────────
preferred = ("llm_api_key_2", "llm_api_key2", "llm_api_key")
key = ""
for p in [ROOT / ".env", Path.cwd() / ".env"]:
    if not p.exists():
        continue
    vals = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        vals[k.strip().lower()] = v.strip()
    for name in preferred:
        if vals.get(name):
            key = vals[name]
            break
    if key:
        break

query = "drop noisy answer grade nodes from current graph context results"
print(f"Embedding query: '{query}'")
payload = json.dumps({"model": "nvidia/llama-nemotron-embed-vl-1b-v2:free",
                      "input": query}).encode()
req = urllib.request.Request(
    "https://openrouter.ai/api/v1/embeddings",
    data=payload,
    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
)
query_vec = np.array(json.loads(urllib.request.urlopen(req, timeout=20).read())
                     ["data"][0]["embedding"], dtype=np.float32)
print("  Done.\n")

# ── Score all candidates ──────────────────────────────────────────────────────
scores = []
for v in G.vs:
    if _is_candidate(v):
        sim = cosine(query_vec, v["embedding"])
        scores.append((sim, v.index, v["name"], v["file"].split("/")[-1]))

scores.sort(reverse=True)
total_candidates = len(scores)

print(f"Total candidates: {total_candidates}")
print()

# ── Report 1: Top-20 vector rankings ─────────────────────────────────────────
print("Top-20 by raw cosine similarity:")
targets = {"_filter_answer_grade_nodes", "rebuild_graph_cache"}
for rank, (sim, idx, name, fname) in enumerate(scores[:20], 1):
    marker = " <-- TARGET" if name == "_filter_answer_grade_nodes" else \
             " <-- SEED CANDIDATE" if name == "rebuild_graph_cache" else ""
    print(f"  {rank:3d}. [{sim:.4f}] {name} [{fname}]{marker}")

# ── Report 2: Exact ranks of both functions ───────────────────────────────────
print()
target_rank = seed_rank = None
target_score = seed_score = None
target_idx = seed_idx = None
for rank, (sim, idx, name, fname) in enumerate(scores, 1):
    if name == "_filter_answer_grade_nodes" and target_rank is None:
        target_rank, target_score, target_idx = rank, sim, idx
    if name == "rebuild_graph_cache" and seed_rank is None:
        seed_rank, seed_score, seed_idx = rank, sim, idx
    if target_rank and seed_rank:
        break

print(f"_filter_answer_grade_nodes: rank {target_rank}/{total_candidates}, score {target_score:.4f}")
print(f"rebuild_graph_cache:        rank {seed_rank}/{total_candidates}, score {seed_score:.4f}")

# ── Report 3: Seed window and proportional weights ────────────────────────────
TOP_K = 15
top_seeds = scores[:TOP_K]
print(f"\nTop-{TOP_K} seeds and their cluster assignments:")
cluster_counts: dict[int, int] = {}
for rank, (sim, idx, name, fname) in enumerate(top_seeds, 1):
    cid = G.vs[idx]["cluster_id"]
    cluster_counts[cid] = cluster_counts.get(cid, 0) + 1
    marker = " <-- rebuild_graph_cache" if name == "rebuild_graph_cache" else \
             " <-- _filter_answer_grade_nodes" if name == "_filter_answer_grade_nodes" else ""
    print(f"  {rank:2d}. {name} [{fname}]  cluster={cid}  score={sim:.4f}{marker}")

print(f"\nCluster distribution among seeds: {cluster_counts}")

# Proportional weight for rebuild_graph_cache
if seed_rank and seed_rank <= TOP_K:
    seed_cid = G.vs[seed_idx]["cluster_id"]
    seed_community_weight = cluster_counts.get(seed_cid, 0) / TOP_K
    seed_proportional_reset = seed_score * seed_community_weight
    # After normalization
    total_reset = sum(
        scores[i][0] * (cluster_counts.get(G.vs[scores[i][1]]["cluster_id"], 0) / TOP_K)
        for i in range(TOP_K)
    )
    seed_normalized_weight = seed_proportional_reset / total_reset if total_reset > 0 else 0
    print(f"\nrebuild_graph_cache in top-{TOP_K}: YES")
    print(f"  cluster_id: {seed_cid}")
    print(f"  seeds in same cluster: {cluster_counts.get(seed_cid, 0)}")
    print(f"  community weight (k/total): {seed_community_weight:.4f}")
    print(f"  proportional reset contribution: {seed_proportional_reset:.4f}")
    print(f"  normalized reset weight: {seed_normalized_weight:.4f} ({seed_normalized_weight*100:.1f}%)")
else:
    print(f"\nrebuild_graph_cache NOT in top-{TOP_K} (rank {seed_rank})")
    print("  Seed window is the problem — floor weighting cannot help this miss.")

if target_rank and target_rank <= TOP_K:
    print(f"\n_filter_answer_grade_nodes IS in top-{TOP_K} (rank {target_rank})")
    print("  Target is a seed itself — the miss is entirely in PPR/community scoping, not vector search.")
else:
    print(f"\n_filter_answer_grade_nodes NOT in top-{TOP_K} (rank {target_rank})")

# ── Report 4: Direct CALLS edge between rebuild_graph_cache → _filter_answer_grade_nodes ──
print("\n" + "="*60)
print("CALLS edge check (directed: source=caller, target=callee)")
print("="*60)

if seed_idx is not None and target_idx is not None:
    # Check direct edge rebuild_graph_cache → _filter_answer_grade_nodes
    direct_edge = G.get_eid(seed_idx, target_idx, directed=True, error=False)
    if direct_edge != -1 and G.es[direct_edge]["type"] == "CALLS":
        print(f"DIRECT CALLS edge exists: rebuild_graph_cache -> _filter_answer_grade_nodes")
        print("  PPR mass CAN flow directly to target from this seed.")
    else:
        print("NO direct CALLS edge: rebuild_graph_cache → _filter_answer_grade_nodes")
        print("  PPR mass cannot flow directly. Need intermediate nodes or different mechanism.")

    # Count all out-CALLS edges from rebuild_graph_cache
    out_calls = [e for e in G.es if e.source == seed_idx and e["type"] == "CALLS"]
    print(f"\nTotal out-CALLS edges from rebuild_graph_cache: {len(out_calls)}")
    if out_calls:
        print("  Callees (each gets ~1/N fraction of PPR mass from this node):")
        for e in out_calls[:15]:
            callee = G.vs[e.target]["name"]
            callee_file = G.vs[e.target]["file"].split("/")[-1]
            marker = " <-- TARGET" if callee == "_filter_answer_grade_nodes" else ""
            print(f"    {callee} [{callee_file}]{marker}")
        if len(out_calls) > 15:
            print(f"    ... and {len(out_calls)-15} more")

    # Also check if _filter_answer_grade_nodes is reachable within 2 hops
    print(f"\n2-hop reachability: rebuild_graph_cache → ? → _filter_answer_grade_nodes")
    intermediate_paths = []
    for e1 in G.es:
        if e1.source == seed_idx and e1["type"] == "CALLS":
            intermediate = e1.target
            e2 = G.get_eid(intermediate, target_idx, directed=True, error=False)
            if e2 != -1 and G.es[e2]["type"] == "CALLS":
                intermediate_name = G.vs[intermediate]["name"]
                intermediate_paths.append(intermediate_name)
    if intermediate_paths:
        print(f"  Reachable via: {intermediate_paths}")
    else:
        print(f"  NOT reachable within 2 hops")

# ── Report 5: _filter_answer_grade_nodes callers ─────────────────────────────
print("\n" + "="*60)
print("_filter_answer_grade_nodes connectivity")
print("="*60)
if target_idx is not None:
    callers = [G.vs[e.source]["name"] for e in G.es
               if e.target == target_idx and e["type"] == "CALLS"]
    callees = [G.vs[e.target]["name"] for e in G.es
               if e.source == target_idx and e["type"] == "CALLS"]
    target_cluster = G.vs[target_idx]["cluster_id"]
    print(f"cluster_id: {target_cluster}")
    print(f"callers (in-edges): {callers[:10] or '(none)'}")
    print(f"callees (out-edges): {callees[:10] or '(none)'}")

    # Are any callers in the top-15 seeds?
    top_seed_names = {name for _, _, name, _ in top_seeds}
    caller_seeds = [c for c in callers if c in top_seed_names]
    print(f"Callers that ARE top-15 seeds: {caller_seeds or '(none)'}")

# ── Report 6: Cross-check with stored ablation ───────────────────────────────
print("\n" + "="*60)
print("Cross-check with stored ablation_results.json")
print("="*60)
ablation = json.loads((ROOT / "sandbox" / "ablation_results.json").read_text(encoding="utf-8"))
gq = next(q for q in ablation["per_query"] if q["id"] == "graph_context_filter")
print(f"Stored vector top-10: {gq['top10_vector']}")
print(f"Stored PPR top-10:    {gq['top10_calls_ppr']}")
stored_v_top10 = [r.split(" [")[0] for r in gq["top10_vector"]]
our_v_top10    = [name for _, _, name, _ in scores[:10]]
overlap = sum(1 for n in our_v_top10 if n in stored_v_top10)
print(f"Overlap with stored vector top-10: {overlap}/10 ({'consistent' if overlap >= 7 else 'MISMATCH'})")

# ── Summary verdict ───────────────────────────────────────────────────────────
print("\n" + "="*60)
print("MECHANISM VERDICT")
print("="*60)
if seed_rank and seed_rank <= TOP_K and target_rank and target_rank > TOP_K:
    if direct_edge != -1:
        print("Mechanism: CALLEE DILUTION")
        print(f"  rebuild_graph_cache is seed rank {seed_rank}, has {len(out_calls)} out-CALLS edges.")
        print(f"  _filter_answer_grade_nodes is a direct callee but gets 1/{len(out_calls)} of PPR mass.")
        print(f"  Seed floor weighting from Step 1 will give rebuild_graph_cache more teleportation")
        print(f"  mass, which proportionally increases what flows to _filter_answer_grade_nodes.")
        print(f"  Floor weighting likely helps this miss too.")
    else:
        print("Mechanism: INDIRECT/MULTI-HOP")
        print(f"  rebuild_graph_cache is seed rank {seed_rank} but no direct CALLS to target.")
        print(f"  PPR mass must diffuse through intermediate nodes — may not recover target.")
        print(f"  Seed floor weighting alone likely insufficient.")
elif seed_rank and seed_rank > TOP_K:
    print(f"Mechanism: SEED WINDOW MISS")
    print(f"  rebuild_graph_cache is rank {seed_rank} — outside the top-{TOP_K} seed window.")
    print(f"  Seed floor weighting cannot help. Investigate top_k_seeds expansion.")
elif target_rank and target_rank <= TOP_K:
    print(f"Mechanism: PPR SCOPING / COMMUNITY BOUNDARY")
    print(f"  _filter_answer_grade_nodes IS in top-{TOP_K} seeds (rank {target_rank}).")
    print(f"  The miss is in community scoping or PPR amplification, not vector search.")
    print(f"  Seed floor weighting may help if its community weight is low.")
else:
    print("Mechanism: UNCLEAR — review raw numbers above")
