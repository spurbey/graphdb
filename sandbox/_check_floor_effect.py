"""
After floor weighting was applied, both export_snapshot and _filter_answer_grade_nodes
still MISS. This script checks what the floor actually gives them in the reset
vector, and whether the issue is that they're NOT in the top-15 seeds at all.
"""
import json
import random as _r
import urllib.request
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


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    return float(np.dot(a, b) / (na * nb)) if na > 0 and nb > 0 else 0.0


def load_key() -> str:
    preferred = ("llm_api_key_2", "llm_api_key2", "llm_api_key")
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
                return vals[name]
    return ""


key = load_key()
SEED_FLOOR = 0.3
TOP_K = 15


def analyze_query(query_text: str, target_name: str) -> None:
    print(f"\n{'='*60}")
    print(f"Query: '{query_text}'")
    print(f"Target: {target_name}")
    print("="*60)

    # Embed
    payload = json.dumps({"model": "nvidia/llama-nemotron-embed-vl-1b-v2:free",
                          "input": query_text}).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/embeddings",
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    query_vec = np.array(
        json.loads(urllib.request.urlopen(req, timeout=20).read())["data"][0]["embedding"],
        dtype=np.float32
    )

    # Score
    scores = [(cosine(query_vec, v["embedding"]), v.index, v["name"])
              for v in G.vs if _is_candidate(v)]
    scores.sort(reverse=True)

    # Find target rank in ALL candidates
    target_rank = next((r+1 for r, (s, i, n) in enumerate(scores) if n == target_name), None)
    target_score = next((s for s, i, n in scores if n == target_name), None)
    target_in_seeds = target_rank is not None and target_rank <= TOP_K

    print(f"\nTarget vector rank: {target_rank}/{len(scores)}, score: {target_score:.4f}")
    print(f"Target in top-{TOP_K} seeds: {target_in_seeds}")

    # Top-15 seeds
    top_seeds = scores[:TOP_K]
    cluster_counts: dict[int, int] = {}
    for sim, idx, name in top_seeds:
        cid = G.vs[idx]["cluster_id"]
        cluster_counts[cid] = cluster_counts.get(cid, 0) + 1

    # Build reset vector with floor
    uniform_weight = 1.0 / TOP_K
    reset = np.zeros(G.vcount())
    for sim, idx, name in top_seeds:
        cid = G.vs[idx]["cluster_id"]
        w = cluster_counts.get(cid, 0) / TOP_K
        proportional = max(sim, 0.0) * w
        floor_val = SEED_FLOOR * uniform_weight
        reset[idx] = max(proportional, floor_val)

    total = reset.sum()
    if total > 0:
        reset /= total

    # Target's reset weight
    if target_in_seeds:
        target_idx = next(idx for sim, idx, name in scores if name == target_name)
        target_reset = reset[target_idx]
        target_cid = G.vs[target_idx]["cluster_id"]
        seeds_in_cid = cluster_counts.get(target_cid, 0)

        # What would reset be WITHOUT floor?
        w_no_floor = seeds_in_cid / TOP_K
        reset_no_floor = (target_score * w_no_floor) / sum(
            max(s, 0.0) * (cluster_counts.get(G.vs[i]["cluster_id"], 0) / TOP_K)
            for s, i, n in top_seeds
        )

        print(f"\nReset weight WITHOUT floor: {reset_no_floor:.4f} ({reset_no_floor*100:.2f}%)")
        print(f"Reset weight WITH floor:    {target_reset:.4f} ({target_reset*100:.2f}%)")
        print(f"Floor improvement: +{(target_reset - reset_no_floor)*100:.2f} percentage points")
        print(f"Target cluster: {target_cid}, seeds in cluster: {seeds_in_cid}")

        # Top seed's reset weight for comparison
        top_idx = next(idx for sim, idx, name in scores[:1])
        top_reset = reset[top_idx]
        top_name = scores[0][2]
        print(f"\nTop seed '{top_name}' reset weight: {top_reset:.4f} ({top_reset*100:.2f}%)")
        print(f"Target is {top_reset/target_reset:.1f}x weaker than top seed")

        # Now show PPR result (what actually happens)
        print(f"\nPPR result (what ended up in top-10):")
        ppr_scores_list = calls_only.personalized_pagerank(
            vertices=None, damping=0.85, directed=True,
            weights=None, reset=reset.tolist()
        )
        ranked = sorted(enumerate(ppr_scores_list), key=lambda x: x[1], reverse=True)
        top_cluster_set = set(cluster_counts.keys())
        ranked_filtered = [(i, s) for i, s in ranked
                          if G.vs[i]["cluster_id"] in top_cluster_set
                          and _is_candidate(G.vs[i])][:15]

        target_ppr_rank = next((r+1 for r, (i, s) in enumerate(ranked_filtered)
                                if G.vs[i]["name"] == target_name), None)
        print(f"Target PPR rank in filtered candidates: {target_ppr_rank or 'NOT IN TOP 15'}")

        for r, (idx_ppr, score_ppr) in enumerate(ranked_filtered[:10], 1):
            nm = G.vs[idx_ppr]["name"]
            fl = G.vs[idx_ppr]["file"].split("/")[-1]
            marker = " <-- TARGET" if nm == target_name else ""
            print(f"  {r:2d}. {nm} [{fl}]  ppr={score_ppr:.5f}{marker}")
    else:
        print(f"\nTarget NOT in top-{TOP_K} seeds — floor weighting has NO EFFECT.")
        print(f"Target rank {target_rank} is outside the seed window entirely.")
        print(f"\nTop-15 seeds for this query:")
        for r, (sim, idx, name) in enumerate(top_seeds, 1):
            fl = G.vs[idx]["file"].split("/")[-1]
            cid = G.vs[idx]["cluster_id"]
            print(f"  {r:2d}. {name} [{fl}]  score={sim:.4f}  cluster={cid}")


print("Loading done. Running queries...")

analyze_query("store and save session memory snapshot", "export_snapshot")
analyze_query("drop noisy answer grade nodes from current graph context results",
              "_filter_answer_grade_nodes")
