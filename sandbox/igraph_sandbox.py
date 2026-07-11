"""
igraph_sandbox.py — Cold Discovery Pipeline & Bidirectional Connector Engine
for agent-memory-orchestrator.

Phases:
  1. Load amo_nodes.json + amo_edges.json into igraph
  2. Compute query-time weights (hub penalty, co-change, AST cost)
  3. Cold discovery: vector seed -> Infomap -> PPR -> MMR
  4. Warm connector: weighted shortest path between two nodes
  5. Ground-truth validation

Usage:
    cd graphdb
    python igraph_sandbox.py
"""

import json
import math
import random as _random
from pathlib import Path
import numpy as np
import igraph as ig

try:
    from sandbox.cochange_analysis import MODE_RISK
    from sandbox.cochange_analysis import cochange_consumer_policy
except ModuleNotFoundError:
    from cochange_analysis import MODE_RISK
    from cochange_analysis import cochange_consumer_policy

ROOT = Path(__file__).resolve().parents[1]
QUERY_FEATURE_MEAN: np.ndarray | None = None
EMBEDDING_SPACE = "raw_openrouter"


def has_nonzero_embedding(values) -> bool:
    return bool(values) and any(abs(float(value)) > 1e-12 for value in values)


def load_graphsage_feature_fallback(nodes: list[dict]) -> tuple[dict[str, np.ndarray], np.ndarray | None, str]:
    active = sorted([node for node in nodes if node.get("status") == "active"], key=lambda node: node["id"])
    if any(has_nonzero_embedding(node.get("embedding") or []) for node in active):
        return {}, None, "raw_openrouter"

    data_path = ROOT / "graphsage_minimal" / "data" / "amo_calls_active.npz"
    meta_path = ROOT / "graphsage_minimal" / "data" / "node_meta.json"
    if not data_path.exists() or not meta_path.exists():
        return {}, None, "missing"

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta_ids = [row["id"] for row in meta]
    active_ids = [node["id"] for node in active]
    if meta_ids != active_ids:
        return {}, None, "id_mismatch"

    data = np.load(data_path)
    x = data["x"].astype(np.float32)
    feature_mean = data["feature_mean"].astype(np.float32)
    return {node_id: x[idx] for idx, node_id in enumerate(meta_ids)}, feature_mean, "graphsage_centered"

# ── Load data ─────────────────────────────────────────────────────────────────
print("Loading graph data...")
with open("sandbox/amo_nodes.json", encoding="utf-8") as f:
    nodes_data = json.load(f)
with open("sandbox/amo_edges.json", encoding="utf-8") as f:
    edges_data = json.load(f)

print(f"  {len(nodes_data)} nodes, {len(edges_data)} edges")

# Only keep edges where both endpoints exist (sanity check)
node_id_set = {n["id"] for n in nodes_data}
edges_data = [e for e in edges_data if e["source"] in node_id_set and e["target"] in node_id_set]
print(f"  {len(edges_data)} edges after endpoint validation")

# ── Build igraph ──────────────────────────────────────────────────────────────
print("Building igraph instance...")
G = ig.Graph(directed=True)

node_ids = [n["id"] for n in nodes_data]
G.add_vertices(len(node_ids))
id_to_idx = {nid: i for i, nid in enumerate(node_ids)}
reused_features, QUERY_FEATURE_MEAN, EMBEDDING_SPACE = load_graphsage_feature_fallback(nodes_data)
if EMBEDDING_SPACE == "graphsage_centered":
    print(f"  Reusing GraphSAGE feature matrix for {len(reused_features)} active embeddings")
elif EMBEDDING_SPACE != "raw_openrouter":
    print(f"  No reusable GraphSAGE feature fallback: {EMBEDDING_SPACE}")

for i, n in enumerate(nodes_data):
    G.vs[i]["id"]           = n["id"]
    G.vs[i]["file"]         = n["file"]
    G.vs[i]["name"]         = n["name"]
    G.vs[i]["text_summary"] = n.get("text_summary", "")
    G.vs[i]["status"]       = n.get("status", "superseded")
    emb = n.get("embedding", [])
    if has_nonzero_embedding(emb):
        G.vs[i]["embedding"] = np.array(emb, dtype=np.float32)
    elif n["id"] in reused_features:
        G.vs[i]["embedding"] = reused_features[n["id"]]
    else:
        G.vs[i]["embedding"] = None

edge_tuples = [(id_to_idx[e["source"]], id_to_idx[e["target"]]) for e in edges_data]
G.add_edges(edge_tuples)
for i, e in enumerate(edges_data):
    G.es[i]["type"]             = e["type"]
    G.es[i]["co_change_count"]  = e.get("co_change_count", 0)
    G.es[i]["category"]         = e.get("category", "")
    G.es[i]["ast_relation_type"] = e.get("ast_relation_type", "unknown")

print(f"  Graph: {G.vcount()} vertices, {G.ecount()} edges")

# ── Phase 0a: Infomap — run once at startup with fixed seed ──────────────────
# community_infomap() is non-deterministic. Fix randomness before the call so
# community assignments are stable across runs. Hoist out of per-query loop.
print("Running Infomap community detection (fixed seed=42)...")
_random.seed(42)
np.random.seed(42)
calls_edge_ids = [e.index for e in G.es if e["type"] == "CALLS"]
calls_only = G.subgraph_edges(calls_edge_ids, delete_vertices=False)
_communities = calls_only.community_infomap()
for _i, _c in enumerate(_communities.membership):
    G.vs[_i]["cluster_id"] = _c
INFOMAP_N_COMMUNITIES = len(set(_communities.membership))
print(f"  Infomap: {INFOMAP_N_COMMUNITIES} communities over {G.vcount()} nodes")

# ── Phase 2: Query-time weight computation ────────────────────────────────────
print("Computing query-time edge weights...")

degrees = np.array(G.degree())
max_deg = max(degrees) if len(degrees) > 0 else 1
_calls_degrees = calls_only.degree()
_calls_max = max(_calls_degrees) or 1

# Phase 0c: hub penalty exponent — raised from 1.5 to 2.0 after measuring
# god-node contamination on clean (test-filtered) pool.
# Revert to 1.5 if legitimate high-degree nodes get suppressed.
HUB_PENALTY_EXPONENT = 2.0


def compute_weights(alpha=0.4, beta=0.6, co_change_floor=1, cochange_mode=MODE_RISK):
    weights = []
    for edge in G.es:
        etype = edge["type"]
        cochange_policy = None

        # A. AST structural cost
        if etype == "CALLS":
            ast_cost = 1.0
        elif etype == "IMPORTS":
            ast_cost = 1.5
        else:  # CO_CHANGE
            cochange_policy = cochange_consumer_policy(edge["category"], cochange_mode)
            if not cochange_policy["include"]:
                weights.append(9999.0)
                continue
            ast_cost = 2.0

        # B. Temporal closeness (inverse co-change frequency)
        cc = edge["co_change_count"]
        temporal_cost = 1.0 / (1.0 + cc) if cc >= co_change_floor else 1.0

        base_cost = (alpha * ast_cost) + (beta * temporal_cost)
        if cochange_policy is not None:
            base_cost *= cochange_policy["cost_multiplier"]

        # C. Hub penalty on target node (top 5% by CALLS degree)
        calls_deg = _calls_degrees[edge.target]
        if calls_deg > _calls_max * 0.05:
            base_cost *= math.log(calls_deg + 1) ** HUB_PENALTY_EXPONENT

        weights.append(base_cost)
    return weights


G.es["weight"] = compute_weights()
print(f"  Weights computed. Range: [{min(G.es['weight']):.3f}, {max(G.es['weight']):.3f}]")


# ── Phase 3: Cold Discovery Pipeline ─────────────────────────────────────────

def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _is_candidate(v) -> bool:
    """Phase 0b: exclude test functions and inactive/unembedded nodes from results."""
    return (
        v["status"] == "active"
        and v["embedding"] is not None
        and not v["name"].startswith("test_")
    )


def run_cold_discovery(query_embedding: np.ndarray, top_k_seeds=15, final_k=10):
    """
    Step 1: Vector seed search (cosine similarity) — test functions excluded
    Step 2: Infomap community scope (pre-computed at startup, deterministic)
    Step 3: Personalized PageRank with soft community weighting
    Step 4: MMR diverse selection
    """
    # Step 1: Vector seed search — candidates only (no test functions)
    seed_scores = []
    for v in G.vs:
        if _is_candidate(v):
            sim = cosine_sim(query_embedding, v["embedding"])
            seed_scores.append((v.index, sim))

    seed_scores.sort(key=lambda x: x[1], reverse=True)
    top_seeds = seed_scores[:top_k_seeds]

    print(f"\n  Top {top_k_seeds} vector seeds:")
    for idx, score in top_seeds[:5]:
        v = G.vs[idx]
        print(f"    [{score:.3f}] {v['name']} ({v['file'].split('/')[-1]})")

    # Step 2: Community distribution of seeds (cluster_id already set at startup)
    seed_clusters = [G.vs[idx]["cluster_id"] for idx, _ in top_seeds]
    cluster_counts: dict[int, int] = {}
    for c in seed_clusters:
        cluster_counts[c] = cluster_counts.get(c, 0) + 1
    print(f"  Seed cluster distribution: {dict(list(cluster_counts.items())[:5])} ({len(cluster_counts)} clusters)")

    # Step 3: Personalized PageRank with soft community weighting
    # Phase 1 fix: proportional weighting instead of binary dominant-cluster mask.
    # A community with k seeds out of top_k_seeds gets weight k/top_k_seeds in
    # the reset vector instead of being zeroed out entirely.
    total_seeds = len(top_seeds)
    community_weight = {
        cid: count / total_seeds
        for cid, count in cluster_counts.items()
    }

    reset_vector = np.zeros(G.vcount())
    for idx, score in top_seeds:
        cid = G.vs[idx]["cluster_id"]
        w = community_weight.get(cid, 0.0)
        reset_vector[idx] = max(score, 0.0) * w

    total = reset_vector.sum()
    if total > 0:
        reset_vector /= total
    else:
        for idx, _ in top_seeds:
            reset_vector[idx] = 1.0 / len(top_seeds)

    ppr_scores = calls_only.personalized_pagerank(
        vertices=None,
        damping=0.85,
        directed=True,
        weights=None,
        reset=reset_vector.tolist(),
    )

    ranked_ppr = sorted(enumerate(ppr_scores), key=lambda x: x[1], reverse=True)[:60]

    # Include all clusters that had at least 1 seed (Phase 1 fix: no hard top-3 cutoff)
    top_cluster_set = set(cluster_counts.keys())
    ranked_ppr = [
        (idx, score) for idx, score in ranked_ppr
        if G.vs[idx]["cluster_id"] in top_cluster_set
        and _is_candidate(G.vs[idx])  # Phase 0b: exclude test functions from results
    ][:30]

    # Step 4: MMR diverse selection
    lam = 0.6   # balance: higher = more PPR weight, lower = more diversity
    selected: list[int] = []
    candidates = list(ranked_ppr)

    while len(selected) < final_k and candidates:
        best_idx, best_score = None, -1e9
        for node_idx, ppr_score in candidates:
            emb = G.vs[node_idx]["embedding"]
            if emb is None:
                sim_to_selected = 0.0
            elif not selected:
                sim_to_selected = 0.0
            else:
                sims = [
                    cosine_sim(emb, G.vs[sel_idx]["embedding"])
                    for sel_idx in selected
                    if G.vs[sel_idx]["embedding"] is not None
                ]
                sim_to_selected = max(sims) if sims else 0.0

            mmr_score = lam * ppr_score - (1 - lam) * sim_to_selected
            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = node_idx

        if best_idx is None:
            break
        selected.append(best_idx)
        candidates = [(i, s) for i, s in candidates if i != best_idx]

    return selected


# ── Phase 4: Warm Connector ───────────────────────────────────────────────────

def find_connecting_path(source_id: str, target_id: str) -> list[str]:
    """Weighted shortest path between two node IDs."""
    src = id_to_idx.get(source_id)
    tgt = id_to_idx.get(target_id)
    if src is None or tgt is None:
        return []
    try:
        paths = G.get_shortest_paths(src, to=tgt, weights=G.es["weight"], mode="out")
        if paths and paths[0]:
            return [G.vs[i]["id"] for i in paths[0]]
        return []
    except Exception as e:
        return [f"error: {e}"]


# ── Phase 5: Ground-truth validation ─────────────────────────────────────────

def load_query_embedding(query_text: str) -> np.ndarray:
    """Embed a query string using the same OpenRouter model."""
    import urllib.request
    try:
        key = ""
        preferred_names = ("llm_api_key_2", "llm_api_key2", "llm_api_key")
        env_candidates = [
            Path.cwd() / ".env",
            Path(__file__).resolve().parents[1] / ".env",
            Path.cwd().parent / ".env",
            Path(__file__).resolve().parents[2] / ".env",
        ]
        for env_path in env_candidates:
            if not env_path.exists():
                continue
            values: dict[str, str] = {}
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if "=" not in line:
                    continue
                name, value = line.split("=", 1)
                values[name.strip().lower()] = value.strip()
            for name in preferred_names:
                if values.get(name):
                    key = values[name]
                    break
            if key:
                break
        if not key:
            raise RuntimeError("No llm_api_key found in .env candidates")
        payload = json.dumps({
            "model": "nvidia/llama-nemotron-embed-vl-1b-v2:free",
            "input": query_text
        }).encode()
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/embeddings",
            data=payload,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=20).read())
        vec = np.array(resp["data"][0]["embedding"], dtype=np.float32)
        if QUERY_FEATURE_MEAN is not None:
            vec = vec.reshape(1, -1) - QUERY_FEATURE_MEAN
            vec = vec / np.maximum(np.linalg.norm(vec, axis=1, keepdims=True), 1e-8)
            return vec[0].astype(np.float32)
        return vec
    except Exception as e:
        print(f"  [embed warn] {e}")
        return np.zeros(2048, dtype=np.float32)


def run_validation():
    """
    Run 3 ground-truth validation queries.
    Each query has a known target function — pass if it appears in top-10.
    """
    tests = [
        {
            "query":       "memory ingestion pipeline hook processing",
            "target_name": "ingest_hook_payload",
            "target_hint": "ingest",
        },
        {
            "query":       "retrieve context from memory for agent",
            "target_name": "memory_context_pack",
            "target_hint": "tools",
        },
        {
            "query":       "store and save session memory snapshot",
            "target_name": "export_snapshot",
            "target_hint": "snapshots",
        },
    ]

    print("\n" + "=" * 60)
    print("GROUND-TRUTH VALIDATION")
    print("=" * 60)

    passed = 0
    for test in tests:
        query  = test["query"]
        t_name = test["target_name"]
        t_hint = test["target_hint"]

        print(f"\nQuery: {repr(query)}")
        print(f"Target: function named ~'{t_name}' in a file containing '{t_hint}'")

        vec      = load_query_embedding(query)
        selected = run_cold_discovery(vec, top_k_seeds=15, final_k=10)

        found = False
        print(f"  Top-10 results:")
        for rank, node_idx in enumerate(selected):
            v    = G.vs[node_idx]
            name = v["name"]
            file = v["file"].split("/")[-1]
            print(f"    {rank+1}. {name} ({file})")
            if name == t_name and t_hint.lower() in v["file"].lower():
                found = True

        status = "PASS" if found else "MISS"
        print(f"  Result: {status}")
        if found:
            passed += 1

    print(f"\n{'='*60}")
    print(f"Validation: {passed}/{len(tests)} passed")
    return passed


# ── Run everything ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Quick connector demo
    print("\n" + "=" * 60)
    print("WARM CONNECTOR DEMO")
    print("=" * 60)

    # Find two active functions to connect
    active_nodes = [v for v in G.vs if v["status"] == "active" and v["embedding"] is not None]
    if len(active_nodes) >= 2:
        src_node = active_nodes[0]
        tgt_node = active_nodes[50] if len(active_nodes) > 50 else active_nodes[-1]
        print(f"Source: {src_node['name']} ({src_node['file'].split('/')[-1]})")
        print(f"Target: {tgt_node['name']} ({tgt_node['file'].split('/')[-1]})")
        path = find_connecting_path(src_node["id"], tgt_node["id"])
        if path:
            print(f"Path ({len(path)} hops):")
            for p in path:
                name = p.split("::")[-1]
                file = p.split("::")[0].split("/")[-1]
                print(f"  {name} ({file})")
        else:
            print("  No path found between these nodes.")

    # Run validation
    run_validation()
