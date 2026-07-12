"""
Isolate PPR vs MMR contribution.
Run same 5 queries through 4 modes:
  A) Vector only (top-k by cosine)
  B) Vector + PPR, no MMR (top-k by PPR score directly)
  C) Vector + PPR + MMR (current pipeline)
  D) Vector + MMR, no PPR (diversity on cosine scores directly)

This answers: is it PPR or MMR causing the displacement?
"""
import json
import random as _r
import urllib.request
from pathlib import Path

import numpy as np
import igraph as ig

ROOT = Path(__file__).resolve().parents[0]
SANDBOX = ROOT / "sandbox"

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

calls_ids = [e.index for e in G.es if e["type"] == "CALLS"]
calls_only = G.subgraph_edges(calls_ids, delete_vertices=False)
_r.seed(42); np.random.seed(42)
_communities = calls_only.community_infomap()
for _i, _c in enumerate(_communities.membership):
    G.vs[_i]["cluster_id"] = _c

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

def mmr_select(candidates, k, lam=0.6):
    """candidates: list of (idx, score). Returns list of idx."""
    selected = []
    remaining = list(candidates)
    while len(selected) < k and remaining:
        best_idx, best_score = None, -1e9
        for node_idx, base_score in remaining:
            emb = G.vs[node_idx]["embedding"]
            if emb is None or not selected:
                sim_sel = 0.0
            else:
                sims = [cosine(emb, G.vs[s]["embedding"]) for s in selected if G.vs[s]["embedding"] is not None]
                sim_sel = max(sims) if sims else 0.0
            score = lam * base_score - (1 - lam) * sim_sel
            if score > best_score:
                best_score, best_idx = score, node_idx
        if best_idx is None: break
        selected.append(best_idx)
        remaining = [(i, s) for i, s in remaining if i != best_idx]
    return selected

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

def run_all_modes(query_vec, k=10, top_k_seeds=20):
    all_candidates = [(cosine(query_vec, v["embedding"]), v.index, v["name"])
                      for v in G.vs if _is_candidate(v)]
    all_candidates.sort(reverse=True)
    total = len(all_candidates)

    # Mode A: vector only (top-k by cosine, no diversity)
    mode_a = [(name, round(sim, 4)) for sim, idx, name in all_candidates[:k]]

    # Mode D: vector + MMR (diversity on cosine scores, no PPR)
    vec_candidates_for_mmr = [(idx, sim) for sim, idx, name in all_candidates[:k*3]]
    selected_d = mmr_select(vec_candidates_for_mmr, k, lam=0.6)
    mode_d = [(G.vs[idx]["name"], round(cosine(query_vec, G.vs[idx]["embedding"]), 4)) for idx in selected_d]

    # PPR
    top_seeds = all_candidates[:top_k_seeds]
    cluster_counts = {}
    for sim, idx, name in top_seeds:
        cid = G.vs[idx]["cluster_id"]
        cluster_counts[cid] = cluster_counts.get(cid, 0) + 1

    community_weight = {cid: cnt / top_k_seeds for cid, cnt in cluster_counts.items()}
    reset = np.zeros(G.vcount())
    for sim, idx, name in top_seeds:
        cid = G.vs[idx]["cluster_id"]
        reset[idx] = max(sim, 0.0) * community_weight.get(cid, 0.0)
    total_r = reset.sum()
    if total_r > 0: reset /= total_r

    ppr_scores = calls_only.personalized_pagerank(
        vertices=None, damping=0.85, directed=True, weights=None, reset=reset.tolist()
    )

    top_cluster_set = set(cluster_counts.keys())
    ranked_ppr = [(idx, ppr_scores[idx]) for idx in range(G.vcount())
                  if ppr_scores[idx] > 0
                  and G.vs[idx]["cluster_id"] in top_cluster_set
                  and _is_candidate(G.vs[idx])]
    ranked_ppr.sort(key=lambda x: x[1], reverse=True)
    ranked_ppr = ranked_ppr[:30]

    # Mode B: vector + PPR, NO MMR (straight top-k by PPR score)
    mode_b = [(G.vs[idx]["name"], round(ppr_scores[idx], 5)) for idx, _ in ranked_ppr[:k]]

    # Mode C: vector + PPR + MMR (current pipeline)
    selected_c = mmr_select(ranked_ppr, k, lam=0.6)
    mode_c = [(G.vs[idx]["name"], round(ppr_scores[idx], 5)) for idx in selected_c]

    return mode_a, mode_b, mode_c, mode_d, cluster_counts

QUERIES = [
    ("search memories by natural language",
     ["memory_search", "search_memories"]),
    ("remove sensitive data before persisting to storage",
     ["redact_secrets"]),
    ("entry point that handles incoming codex hook payload from IDE",
     ["ingest_hook_payload"]),
    ("validate and normalize agent name for session tracking",
     ["normalize_agent", "_normalize_agent"]),
    ("rebuild and reindex vector store after data changes",
     ["memory_rebuild_indexes", "rebuild_indexes"]),
]

print("PPR vs MMR isolation test")
print("=" * 80)
print()

all_results = []
for query, expected in QUERIES:
    print(f"Query: '{query}'")
    print(f"Expected: {expected}")
    qvec = embed(query)
    ma, mb, mc, md, ccs = run_all_modes(qvec, k=10)

    def find_rank(results, expected_names):
        for rank, (name, score) in enumerate(results, 1):
            if any(e.lower() in name.lower() for e in expected_names):
                return rank
        return None

    ra = find_rank(ma, expected)
    rb = find_rank(mb, expected)
    rc = find_rank(mc, expected)
    rd = find_rank(md, expected)

    dom_cluster = max(ccs, key=ccs.get) if ccs else -1

    print(f"  Dominant cluster: {dom_cluster} ({ccs.get(dom_cluster,0)} of 20 seeds)")
    print(f"  A) Vector only:        rank {ra or 'MISS'}")
    print(f"  B) PPR, no MMR:        rank {rb or 'MISS'}")
    print(f"  C) PPR + MMR (current):rank {rc or 'MISS'}")
    print(f"  D) Vector + MMR:       rank {rd or 'MISS'}")

    # diagnose
    diagnoses = []
    if ra and not rb:
        diagnoses.append("PPR is the killer — it removed what vector found")
    elif not ra and rb:
        diagnoses.append("PPR is the rescuer — found what vector missed")
    elif ra and rb and rb > ra:
        diagnoses.append("PPR hurt rank")
    elif ra and rb and rb < ra:
        diagnoses.append("PPR improved rank")

    if rb and not rc:
        diagnoses.append("MMR is the killer — PPR found it but MMR removed it")
    elif not rb and not rc:
        diagnoses.append("Both PPR and MMR failed")
    elif rb and rc and rc > rb:
        diagnoses.append("MMR hurt rank")
    elif rb and rc and rc < rb:
        diagnoses.append("MMR improved rank")

    if ra and not rd:
        diagnoses.append("MMR on vector alone kills it (too similar to others selected)")
    elif not ra and rd:
        diagnoses.append("MMR on vector helps (diversity rescued it)")

    print(f"  Diagnosis: {' | '.join(diagnoses) if diagnoses else 'no change'}")

    # Show where the expected item appears in each mode's top-10
    print(f"  Top-5 per mode:")
    for label, results in [("A-vec", ma), ("B-ppr", mb), ("C-cur", mc), ("D-vmmr", md)]:
        top5 = [f"{n}({'*' if any(e.lower() in n.lower() for e in expected) else ''})" for n, s in results[:5]]
        print(f"    {label}: {', '.join(top5)}")
    print()
    all_results.append((query, expected, ra, rb, rc, rd))

print("=" * 80)
print("FINAL TABLE")
print(f"{'Query':<20} {'Expected':<20} {'A:Vec':>6} {'B:PPR':>6} {'C:PPR+MMR':>9} {'D:Vec+MMR':>9}")
print("-" * 75)
for query, expected, ra, rb, rc, rd in all_results:
    q_short = query[:18]
    e_short = expected[0][:18]
    print(f"  {q_short:<20} {e_short:<20} {str(ra or 'MISS'):>6} {str(rb or 'MISS'):>6} {str(rc or 'MISS'):>9} {str(rd or 'MISS'):>9}")

print()
print("READING THIS TABLE:")
print("  A vs B = pure PPR effect (does PPR help or hurt before diversity?)")
print("  B vs C = pure MMR effect (does diversity help or hurt after PPR?)")
print("  A vs D = pure MMR on vector effect (does diversity help on cosine scores?)")
print("  A vs C = overall pipeline vs vector alone")
