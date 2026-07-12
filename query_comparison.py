"""
5-query comparison: igraph pipeline vs vector-only
Traces every intermediate step to show exactly where PPR helps or hurts.

Queries chosen to cover different scenarios:
  Q1: Direct/lexical — function name is in the query (PPR usually hurts)
  Q2: Cross-module — answer not lexically obvious (PPR should help)
  Q3: Architectural — want the neighborhood not just one function
  Q4: Ambiguous — answer requires structural context
  Q5: Temporal/review — "what changed" type query
"""
import json
import sys
import os
import random as _r
import urllib.request
from pathlib import Path

import numpy as np
import igraph as ig

ROOT = Path(__file__).resolve().parents[0]
SANDBOX = ROOT / "sandbox"

# ── Load graph once ───────────────────────────────────────────────────────────
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
    G.es[i]["type"]            = e["type"]
    G.es[i]["co_change_count"] = e.get("co_change_count", 0)

calls_ids = [e.index for e in G.es if e["type"] == "CALLS"]
calls_only = G.subgraph_edges(calls_ids, delete_vertices=False)
_r.seed(42); np.random.seed(42)
_communities = calls_only.community_infomap()
for _i, _c in enumerate(_communities.membership):
    G.vs[_i]["cluster_id"] = _c

_calls_degrees = calls_only.degree()
_calls_max = max(_calls_degrees) or 1

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

# load api key
preferred = ("llm_api_key_2", "llm_api_key2", "llm_api_key")
key = ""
for p in [ROOT / ".env"]:
    if not p.exists(): continue
    vals = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if "=" not in line: continue
        k, v = line.split("=", 1); vals[k.strip().lower()] = v.strip()
    for name in preferred:
        if vals.get(name): key = vals[name]; break
    if key: break

def embed(text):
    payload = json.dumps({"model": "nvidia/llama-nemotron-embed-vl-1b-v2:free", "input": text}).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/embeddings",
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    return np.array(json.loads(urllib.request.urlopen(req, timeout=20).read())["data"][0]["embedding"], dtype=np.float32)

def run_pipeline(query_vec, k=10, top_k_seeds=20):
    """Run full pipeline, return both vector-only and PPR top-k with intermediate data."""
    # Step 1: score all candidates
    scores = [(cosine(query_vec, v["embedding"]), v.index, v["name"], v["file"].split("/")[-1])
              for v in G.vs if _is_candidate(v)]
    scores.sort(reverse=True)

    # Vector-only top-k
    vector_top = [(name, fname, sim) for sim, idx, name, fname in scores[:k]]

    # Top seeds
    top_seeds = scores[:top_k_seeds]
    cluster_counts = {}
    for sim, idx, name, fname in top_seeds:
        cid = G.vs[idx]["cluster_id"]
        cluster_counts[cid] = cluster_counts.get(cid, 0) + 1

    # Reset vector
    community_weight = {cid: cnt / top_k_seeds for cid, cnt in cluster_counts.items()}
    reset = np.zeros(G.vcount())
    for sim, idx, name, fname in top_seeds:
        cid = G.vs[idx]["cluster_id"]
        w = community_weight.get(cid, 0.0)
        reset[idx] = max(sim, 0.0) * w
    total = reset.sum()
    if total > 0: reset /= total

    # PPR
    ppr_scores = calls_only.personalized_pagerank(
        vertices=None, damping=0.85, directed=True, weights=None, reset=reset.tolist()
    )

    # Cluster filter + candidate filter
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
            if emb is None or not selected:
                sim_sel = 0.0
            else:
                sims = [cosine(emb, G.vs[s]["embedding"]) for s in selected if G.vs[s]["embedding"] is not None]
                sim_sel = max(sims) if sims else 0.0
            mmr = lam * ppr_score - (1 - lam) * sim_sel
            if mmr > best_score:
                best_score, best_idx = mmr, node_idx
        if best_idx is None: break
        selected.append(best_idx)
        candidates = [(i, s) for i, s in candidates if i != best_idx]

    ppr_top = [(G.vs[idx]["name"], G.vs[idx]["file"].split("/")[-1], ppr_scores[idx]) for idx in selected]

    return {
        "vector_top": vector_top,
        "ppr_top": ppr_top,
        "dominant_cluster": max(cluster_counts, key=cluster_counts.get) if cluster_counts else -1,
        "cluster_counts": cluster_counts,
        "n_clusters_in_seeds": len(cluster_counts),
        "seed_scores": [(name, round(sim,4)) for sim,idx,name,fname in top_seeds[:5]],
    }

# ── 5 queries ─────────────────────────────────────────────────────────────────
QUERIES = [
    {
        "id": "Q1_direct",
        "query": "search memories by natural language",
        "expected": "memory_search or search_memories",
        "type": "Direct/lexical — answer name is in query",
        "what_to_watch": "PPR should help or be neutral (semantic neighborhood is relevant)"
    },
    {
        "id": "Q2_cross_module",
        "query": "remove sensitive data before persisting to storage",
        "expected": "redact_secrets (privacy.py)",
        "type": "Cross-module — answer not lexically obvious, needs CO_CHANGE signal",
        "what_to_watch": "PPR might miss it (no CALLS bridge), theme overlay would help"
    },
    {
        "id": "Q3_architectural",
        "query": "entry point that handles incoming codex hook payload from IDE",
        "expected": "ingest_hook_payload (ingest.py)",
        "type": "Architectural entry point — need the hub not leaf functions",
        "what_to_watch": "PPR should help — entry points have many callers"
    },
    {
        "id": "Q4_ambiguous",
        "query": "validate and normalize agent name for session tracking",
        "expected": "normalize_agent or _normalize_agent",
        "type": "Ambiguous — two candidates in different modules",
        "what_to_watch": "Which one does PPR pick? Does community matter?"
    },
    {
        "id": "Q5_temporal",
        "query": "rebuild and reindex vector store after data changes",
        "expected": "memory_rebuild_indexes or rebuild_indexes",
        "type": "Operational/admin — answer is specific tool function",
        "what_to_watch": "Does vector or PPR find it? Should be in strong seed cluster"
    },
]

print("Graph loaded. Running 5 queries...\n")
print("=" * 80)

results = []
for q in QUERIES:
    print(f"\n[{q['id']}] {q['query']}")
    print(f"  Type: {q['type']}")
    print(f"  Expected: {q['expected']}")
    print(f"  Watch for: {q['what_to_watch']}")

    qvec = embed(q["query"])
    r = run_pipeline(qvec, k=10, top_k_seeds=20)

    # Check if expected is found in each mode
    expected_names = q["expected"].replace("(","").replace(")","").replace(".py","").split(" or ")
    expected_names = [e.strip() for e in expected_names]

    vec_ranks = {}
    for rank, (name, fname, score) in enumerate(r["vector_top"], 1):
        for exp in expected_names:
            if exp.lower() in name.lower():
                vec_ranks[name] = rank

    ppr_ranks = {}
    for rank, (name, fname, score) in enumerate(r["ppr_top"], 1):
        for exp in expected_names:
            if exp.lower() in name.lower():
                ppr_ranks[name] = rank

    print(f"\n  Seeds: {r['seed_scores']}")
    print(f"  Clusters in seeds: {r['n_clusters_in_seeds']}, dominant: cluster {r['dominant_cluster']} ({r['cluster_counts'].get(r['dominant_cluster'],0)} seeds)")

    print(f"\n  VECTOR top-10:")
    for rank, (name, fname, score) in enumerate(r["vector_top"], 1):
        marker = " <-- EXPECTED" if any(e.lower() in name.lower() for e in expected_names) else ""
        print(f"    {rank:2d}. {name} [{fname}]  vec={score:.4f}{marker}")

    print(f"\n  PPR top-10:")
    for rank, (name, fname, score) in enumerate(r["ppr_top"], 1):
        marker = " <-- EXPECTED" if any(e.lower() in name.lower() for e in expected_names) else ""
        print(f"    {rank:2d}. {name} [{fname}]  ppr={score:.5f}{marker}")

    vec_rank_str = str(vec_ranks) if vec_ranks else "MISS"
    ppr_rank_str = str(ppr_ranks) if ppr_ranks else "MISS"
    verdict = ""
    if vec_ranks and ppr_ranks:
        best_vec = min(vec_ranks.values())
        best_ppr = min(ppr_ranks.values())
        if best_ppr < best_vec:
            verdict = "PPR HELPED (better rank)"
        elif best_ppr > best_vec:
            verdict = "PPR HURT (worse rank)"
        else:
            verdict = "NEUTRAL (same rank)"
    elif vec_ranks and not ppr_ranks:
        verdict = "PPR HURT (vector found it, PPR missed)"
    elif not vec_ranks and ppr_ranks:
        verdict = "PPR HELPED (vector missed, PPR found it)"
    else:
        verdict = "BOTH MISSED"

    print(f"\n  RESULT: vector={vec_rank_str}  ppr={ppr_rank_str}  --> {verdict}")
    results.append({**q, "vec_ranks": vec_ranks, "ppr_ranks": ppr_ranks, "verdict": verdict})

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)
print(f"{'Query':<25} {'Expected':<30} {'Vector':>8} {'PPR':>8}  Verdict")
print("-" * 85)
for r in results:
    vs = min(r["vec_ranks"].values()) if r["vec_ranks"] else "MISS"
    ps = min(r["ppr_ranks"].values()) if r["ppr_ranks"] else "MISS"
    print(f"  {r['id']:<23} {r['expected']:<30} {str(vs):>8} {str(ps):>8}  {r['verdict']}")
print()
helped = sum(1 for r in results if "HELPED" in r["verdict"])
hurt   = sum(1 for r in results if "HURT" in r["verdict"])
neutral = sum(1 for r in results if "NEUTRAL" in r["verdict"] or "MISSED" in r["verdict"])
print(f"PPR helped: {helped}/5  |  PPR hurt: {hurt}/5  |  neutral/both-miss: {neutral}/5")
print()
print("KEY INSIGHT:")
if hurt >= helped:
    print("  PPR is hurting more than it helps on these queries.")
    print("  The queries where the function NAME is close to the query text,")
    print("  PPR disperses mass into the structural neighborhood instead of")
    print("  keeping it at the target. Vector-only would be better for these.")
    print("  PPR adds value only when the target is structurally embedded in")
    print("  a well-connected community (many callers, many callees).")
else:
    print("  PPR is helping on these queries.")
    print("  Structural context is improving retrieval beyond lexical matching.")
