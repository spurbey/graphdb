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
import numpy as np
import igraph as ig

# ── Load data ─────────────────────────────────────────────────────────────────
print("Loading graph data...")
with open("amo_nodes.json", encoding="utf-8") as f:
    nodes_data = json.load(f)
with open("amo_edges.json", encoding="utf-8") as f:
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

for i, n in enumerate(nodes_data):
    G.vs[i]["id"]           = n["id"]
    G.vs[i]["file"]         = n["file"]
    G.vs[i]["name"]         = n["name"]
    G.vs[i]["text_summary"] = n.get("text_summary", "")
    G.vs[i]["status"]       = n.get("status", "superseded")
    emb = n.get("embedding", [])
    G.vs[i]["embedding"]    = np.array(emb, dtype=np.float32) if emb else None

edge_tuples = [(id_to_idx[e["source"]], id_to_idx[e["target"]]) for e in edges_data]
G.add_edges(edge_tuples)
for i, e in enumerate(edges_data):
    G.es[i]["type"]             = e["type"]
    G.es[i]["co_change_count"]  = e.get("co_change_count", 0)
    G.es[i]["ast_relation_type"] = e.get("ast_relation_type", "unknown")

print(f"  Graph: {G.vcount()} vertices, {G.ecount()} edges")

# ── Phase 2: Query-time weight computation ────────────────────────────────────
print("Computing query-time edge weights...")

degrees  = np.array(G.degree())
max_deg  = max(degrees) if len(degrees) > 0 else 1

# Build a CALLS-only subgraph for PPR and community detection
calls_edge_ids = [e.index for e in G.es if e["type"] == "CALLS"]
calls_only = G.subgraph_edges(calls_edge_ids, delete_vertices=False)

def compute_weights(alpha=0.4, beta=0.6, co_change_floor=1):
    weights = []
    for edge in G.es:
        etype = edge["type"]

        # A. AST structural cost
        if etype == "CALLS":
            ast_cost = 1.0
        elif etype == "IMPORTS":
            ast_cost = 1.5
        else:  # CO_CHANGE
            ast_cost = 2.0

        # B. Temporal closeness (inverse co-change)
        cc = edge["co_change_count"]
        temporal_cost = 1.0 / (1.0 + cc) if cc >= co_change_floor else 1.0

        base_cost = (alpha * ast_cost) + (beta * temporal_cost)

        # C. Hub penalty on target (top 5% degree in CALLS graph)
        calls_deg = calls_only.degree()[edge.target]
        calls_max = max(calls_only.degree()) or 1
        if calls_deg > calls_max * 0.05:
            base_cost *= math.log(calls_deg + 1) ** 1.5

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


def run_cold_discovery(query_embedding: np.ndarray, top_k_seeds=15, final_k=10):
    """
    Step 1: Vector seed search (cosine similarity)
    Step 2: Infomap community scope filtering
    Step 3: Personalized PageRank
    Step 4: MMR diverse selection
    """
    # Step 1: Vector seed search — only on active nodes with embeddings
    seed_scores = []
    for v in G.vs:
        emb = v["embedding"]
        if emb is not None and v["status"] == "active":
            sim = cosine_sim(query_embedding, emb)
            seed_scores.append((v.index, sim))

    seed_scores.sort(key=lambda x: x[1], reverse=True)
    top_seeds = seed_scores[:top_k_seeds]

    print(f"\n  Top {top_k_seeds} vector seeds:")
    for idx, score in top_seeds[:5]:
        v = G.vs[idx]
        print(f"    [{score:.3f}] {v['name']} ({v['file'].split('/')[-1]})")

    # Step 2: Infomap community detection — CALLS graph only (structural signal)
    calls_weights = [1.0] * calls_only.ecount()
    communities = calls_only.community_infomap(edge_weights=calls_weights if calls_weights else None)
    G.vs["cluster_id"] = communities.membership

    seed_clusters = [G.vs[idx]["cluster_id"] for idx, _ in top_seeds]
    cluster_counts = {}
    for c in seed_clusters:
        cluster_counts[c] = cluster_counts.get(c, 0) + 1
    print(f"  Seed cluster distribution: {dict(list(cluster_counts.items())[:5])} ({len(cluster_counts)} clusters)")

    # Step 3: Personalized PageRank — scoped to dominant seed cluster
    dominant_cluster = max(cluster_counts, key=cluster_counts.get)
    cluster_mask = np.array([
        1.0 if G.vs[i]["cluster_id"] == dominant_cluster else 0.0
        for i in range(G.vcount())
    ])

    reset_vector = np.zeros(G.vcount())
    for idx, score in top_seeds:
        reset_vector[idx] = max(score, 0.0)
    # Zero out seeds not in dominant cluster
    reset_vector *= cluster_mask
    total = reset_vector.sum()
    if total > 0:
        reset_vector /= total
    else:
        # fallback: uniform reset over seed nodes
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

    # Filter to top-3 seed clusters by seed count — prevents total exclusion of minority clusters
    top_clusters = sorted(cluster_counts, key=cluster_counts.get, reverse=True)[:3]
    top_cluster_set = set(top_clusters)
    ranked_ppr = [
        (idx, score) for idx, score in ranked_ppr
        if G.vs[idx]["cluster_id"] in top_cluster_set
    ][:30]

    # Step 4: MMR diverse selection
    lam = 0.6   # balance: higher = more PPR weight, lower = more diversity
    selected = []
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
                sims = []
                for sel_idx in selected:
                    sel_emb = G.vs[sel_idx]["embedding"]
                    if sel_emb is not None:
                        sims.append(cosine_sim(emb, sel_emb))
                sim_to_selected = max(sims) if sims else 0.0

            mmr_score = lam * ppr_score - (1 - lam) * sim_to_selected
            if mmr_score > best_score:
                best_score = mmr_score
                best_idx   = node_idx

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
        for line in open(".env"):
            if "llm_api_key" in line.lower() and "=" in line and "2" not in line.split("=")[0]:
                key = line.split("=", 1)[1].strip()
                break
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
        return np.array(resp["data"][0]["embedding"], dtype=np.float32)
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
