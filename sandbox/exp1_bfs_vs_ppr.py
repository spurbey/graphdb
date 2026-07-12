"""
Experiment 1: BFS vs PPR — Core Architecture Question

Hypothesis: BFS from top-5 vector seeds (2 hops outward in CALLS graph)
will match or beat PPR on HIT@10 across 13 queries, without god-node
contamination and without cluster filter side effects.

Three BFS variants:
  BFS-out: seed -> what it calls -> what those call (follow out-edges)
  BFS-in:  seed -> what calls it -> what calls those (follow in-edges)
  BFS-both: 1 hop in both directions from seed

Comparison modes:
  A) Vector only (top-k by cosine, existing baseline)
  B) Current PPR (existing baseline, k=20 seeds)
  C) BFS-out (2 hops, top-5 seeds, max 15 nodes)
  D) BFS-in  (2 hops, top-5 seeds, max 15 nodes)
  E) BFS-both (1 hop each direction, top-5 seeds, max 15 nodes)
"""
import json
import random as _r
import urllib.request
from pathlib import Path
from collections import deque

import numpy as np
import igraph as ig

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = ROOT / "sandbox"

# ── Load graph ────────────────────────────────────────────────────────────────
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

has_nz = lambda v: bool(v) and any(abs(float(x)) > 1e-12 for x in v)
for i, n in enumerate(nodes_data):
    G.vs[i]["name"]   = n["name"]
    G.vs[i]["file"]   = n["file"]
    G.vs[i]["status"] = n.get("status", "superseded")
    emb = n.get("embedding", [])
    G.vs[i]["embedding"] = np.array(emb, dtype=np.float32) if has_nz(emb) else None

edge_tuples = [(id_to_idx[e["source"]], id_to_idx[e["target"]]) for e in edges_data]
G.add_edges(edge_tuples)
for i, e in enumerate(edges_data):
    G.es[i]["type"] = e["type"]

# Pre-build adjacency for fast BFS
calls_out = {i: [] for i in range(G.vcount())}  # out-edges in CALLS
calls_in  = {i: [] for i in range(G.vcount())}  # in-edges in CALLS
for e in G.es:
    if e["type"] == "CALLS":
        calls_out[e.source].append(e.target)
        calls_in[e.target].append(e.source)

# Infomap for community labels
calls_ids = [e.index for e in G.es if e["type"] == "CALLS"]
calls_only = G.subgraph_edges(calls_ids, delete_vertices=False)
_r.seed(42); np.random.seed(42)
_communities = calls_only.community_infomap()
for _i, _c in enumerate(_communities.membership):
    G.vs[_i]["cluster_id"] = _c

# PPR setup (same as current pipeline)
_calls_degrees = calls_only.degree()
_calls_max = max(_calls_degrees) or 1
import math
HUB_EXPONENT = 2.0

def _is_candidate(v):
    name = v["name"] or ""
    file = v["file"] or ""
    return (
        v["status"] == "active" and v["embedding"] is not None
        and not name.startswith("test_")
        and not file.replace("\\", "/").split("/")[-1].startswith("test_")
    )

def cosine(a, b):
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    return float(np.dot(a, b) / (na * nb)) if na > 0 and nb > 0 else 0.0

print("Graph loaded. Building adjacency...")
print(f"  Candidates: {sum(1 for v in G.vs if _is_candidate(v))}")


# ── BFS implementations ───────────────────────────────────────────────────────

def bfs_expand(seed_indices, direction, depth, max_nodes, community_boundary=False):
    """
    direction: 'out' (follow callees), 'in' (follow callers), 'both'
    community_boundary: if True, don't expand through cross-community nodes
    """
    seed_communities = {G.vs[i]["cluster_id"] for i in seed_indices}
    visited = set(seed_indices)
    result = list(seed_indices)
    
    # Queue: (node_idx, current_depth, can_expand)
    queue = deque()
    for i in seed_indices:
        queue.append((i, 0, True))
    
    while queue and len(result) < max_nodes:
        current, d, can_expand = queue.popleft()
        
        if not can_expand or d >= depth:
            continue
        
        # Get neighbors based on direction
        neighbors = []
        if direction in ('out', 'both'):
            neighbors.extend(calls_out[current])
        if direction in ('in', 'both'):
            neighbors.extend(calls_in[current])
        
        for neighbor in neighbors:
            if neighbor in visited:
                continue
            if not _is_candidate(G.vs[neighbor]):
                continue
            visited.add(neighbor)
            result.append(neighbor)
            
            # Decide if we expand through this neighbor
            if community_boundary:
                neighbor_community = G.vs[neighbor]["cluster_id"]
                expand_further = neighbor_community in seed_communities
            else:
                expand_further = True
            
            queue.append((neighbor, d + 1, expand_further))
    
    return result[:max_nodes]


def run_vector_only(query_vec, k=10):
    scores = [(cosine(query_vec, v["embedding"]), v.index)
              for v in G.vs if _is_candidate(v)]
    scores.sort(reverse=True)
    return [idx for _, idx in scores[:k]]


def run_ppr(query_vec, top_k_seeds=20, k=10):
    """Current PPR pipeline (simplified, no theme overlay)."""
    scores = [(cosine(query_vec, v["embedding"]), v.index)
              for v in G.vs if _is_candidate(v)]
    scores.sort(reverse=True)
    top_seeds = scores[:top_k_seeds]
    
    cluster_counts = {}
    for sim, idx in top_seeds:
        cid = G.vs[idx]["cluster_id"]
        cluster_counts[cid] = cluster_counts.get(cid, 0) + 1
    
    community_weight = {cid: cnt/top_k_seeds for cid, cnt in cluster_counts.items()}
    reset = np.zeros(G.vcount())
    for sim, idx in top_seeds:
        cid = G.vs[idx]["cluster_id"]
        reset[idx] = max(sim, 0.0) * community_weight.get(cid, 0.0)
    total = reset.sum()
    if total > 0: reset /= total
    
    ppr_scores = calls_only.personalized_pagerank(
        vertices=None, damping=0.85, directed=True, weights=None, reset=reset.tolist()
    )
    
    top_cluster_set = set(cluster_counts.keys())
    ranked = [(idx, ppr_scores[idx]) for idx in range(G.vcount())
              if ppr_scores[idx] > 0
              and G.vs[idx]["cluster_id"] in top_cluster_set
              and _is_candidate(G.vs[idx])]
    ranked.sort(key=lambda x: x[1], reverse=True)
    ranked = ranked[:30]
    
    # MMR
    lam = 0.6
    selected = []
    candidates = list(ranked)
    while len(selected) < k and candidates:
        best_idx, best_score = None, -1e9
        for node_idx, ppr_score in candidates:
            emb = G.vs[node_idx]["embedding"]
            if emb is None or not selected: sim_sel = 0.0
            else:
                sims = [cosine(emb, G.vs[s]["embedding"]) for s in selected if G.vs[s]["embedding"] is not None]
                sim_sel = max(sims) if sims else 0.0
            mmr = lam * ppr_score - (1 - lam) * sim_sel
            if mmr > best_score: best_score, best_idx = mmr, node_idx
        if best_idx is None: break
        selected.append(best_idx)
        candidates = [(i, s) for i, s in candidates if i != best_idx]
    return selected


def run_bfs(query_vec, top_k_seeds=5, direction='out', depth=2, max_nodes=15):
    scores = [(cosine(query_vec, v["embedding"]), v.index)
              for v in G.vs if _is_candidate(v)]
    scores.sort(reverse=True)
    seeds = [idx for _, idx in scores[:top_k_seeds]]
    return bfs_expand(seeds, direction, depth, max_nodes, community_boundary=False)


# ── API key for embedding ─────────────────────────────────────────────────────
preferred = ("llm_api_key_2", "llm_api_key2", "llm_api_key")
key = ""
for p in [ROOT / ".env"]:
    if not p.exists(): continue
    vals = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if "=" not in line: continue
        k2, v = line.split("=", 1); vals[k2.strip().lower()] = v.strip()
    for name in preferred:
        if vals.get(name): key = vals[name]; break
    if key: break

def embed(text):
    payload = json.dumps({"model": "nvidia/llama-nemotron-embed-vl-1b-v2:free", "input": text}).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/embeddings", data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    return np.array(json.loads(urllib.request.urlopen(req, timeout=20).read())["data"][0]["embedding"], dtype=np.float32)


# ── Load eval queries ─────────────────────────────────────────────────────────
eval_queries = json.load(open(SANDBOX / "query_rank_eval.json", encoding="utf-8"))
print(f"Eval queries: {len(eval_queries)}")

GOD_NODES = {"close", "make_settings", "init_db", "health_ping", "add_event", "append"}

def find_rank(result_indices, target_name):
    for rank, idx in enumerate(result_indices, 1):
        if G.vs[idx]["name"] == target_name:
            return rank
    return None

def god_node_count(result_indices):
    return sum(1 for idx in result_indices if G.vs[idx]["name"] in GOD_NODES)


# ── Run all modes ─────────────────────────────────────────────────────────────
print("\nRunning experiments...")
print("=" * 80)

results = {
    "vector": [], "ppr": [],
    "bfs_out": [], "bfs_in": [], "bfs_both": []
}

for q in eval_queries:
    query = q["query"]
    target = q["target_name"]
    print(f"\n  Query: '{query[:55]}...' | Target: {target}")
    
    qvec = embed(query)
    
    r_vec   = run_vector_only(qvec, k=10)
    r_ppr   = run_ppr(qvec, top_k_seeds=20, k=10)
    r_bfs_o = run_bfs(qvec, top_k_seeds=5, direction='out',  depth=2, max_nodes=15)[:10]
    r_bfs_i = run_bfs(qvec, top_k_seeds=5, direction='in',   depth=2, max_nodes=15)[:10]
    r_bfs_b = run_bfs(qvec, top_k_seeds=5, direction='both', depth=2, max_nodes=15)[:10]
    
    for mode, result in [("vector",r_vec),("ppr",r_ppr),("bfs_out",r_bfs_o),
                          ("bfs_in",r_bfs_i),("bfs_both",r_bfs_b)]:
        rank = find_rank(result, target)
        god  = god_node_count(result)
        results[mode].append({"id": q["id"], "target": target, "rank": rank, "god_nodes": god})
        status = f"rank {rank}" if rank else "MISS"
        print(f"    {mode:<12}: {status:<10}  god={god}")


# ── Summary table ─────────────────────────────────────────────────────────────
print("\n\n" + "=" * 80)
print("EXPERIMENT 1 RESULTS")
print("=" * 80)
print(f"\n{'Query ID':<35} {'Vec':>4} {'PPR':>4} {'BFS-O':>5} {'BFS-I':>5} {'BFS-B':>5}")
print("-" * 60)

hits = {m: 0 for m in results}
total_god = {m: 0 for m in results}

for i, q in enumerate(eval_queries):
    qid = q["id"][:33]
    row = {m: results[m][i] for m in results}
    def fmt(r): return str(r["rank"]) if r["rank"] else "MISS"
    print(f"  {qid:<33} {fmt(row['vector']):>4} {fmt(row['ppr']):>4} "
          f"{fmt(row['bfs_out']):>5} {fmt(row['bfs_in']):>5} {fmt(row['bfs_both']):>5}")
    for m in results:
        if row[m]["rank"]:
            hits[m] += 1
        total_god[m] += row[m]["god_nodes"]

print("-" * 60)
print(f"  {'HIT@10':<33} {hits['vector']:>4} {hits['ppr']:>4} "
      f"{hits['bfs_out']:>5} {hits['bfs_in']:>5} {hits['bfs_both']:>5}")
print(f"  {'Total god-nodes in results':<33} {total_god['vector']:>4} {total_god['ppr']:>4} "
      f"{total_god['bfs_out']:>5} {total_god['bfs_in']:>5} {total_god['bfs_both']:>5}")

print("\n\nVERDICT:")
best_bfs = max(["bfs_out","bfs_in","bfs_both"], key=lambda m: hits[m])
print(f"  Vector:   {hits['vector']}/13")
print(f"  PPR:      {hits['ppr']}/13")
print(f"  BFS-best: {hits[best_bfs]}/13 ({best_bfs})")
print()

if hits[best_bfs] >= hits["ppr"]:
    print(f"  RESULT: BFS ({best_bfs}) matches or beats PPR")
    if total_god[best_bfs] < total_god["ppr"]:
        print(f"  God-node contamination: BFS {total_god[best_bfs]} vs PPR {total_god['ppr']} -> BFS WINS")
    print(f"  RECOMMENDATION: Replace PPR with {best_bfs}")
    print(f"  NEXT: Experiment 2 (depth tuning) with {best_bfs}")
else:
    print(f"  RESULT: PPR beats BFS ({hits['ppr']} vs {hits[best_bfs]})")
    print(f"  RECOMMENDATION: Keep PPR. Diagnose which queries BFS misses.")
    print(f"  NEXT: Experiment 5 (PPR post-anchor) to find correct PPR use case")

# Save results for record
out = {
    "experiment": 1,
    "description": "BFS vs PPR core architecture comparison",
    "scores": {m: {"hit_at_13": hits[m], "total_god_nodes": total_god[m]} for m in results},
    "per_query": {m: results[m] for m in results}
}
outpath = SANDBOX / "exp1_bfs_vs_ppr.json"
outpath.write_text(json.dumps(out, indent=2), encoding="utf-8")
print(f"\nResults saved: {outpath}")
