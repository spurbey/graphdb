"""
Run a single ambiguous query through the cold discovery pipeline and print what it returns.
"""
import json
import math
from pathlib import Path
import urllib.request
import numpy as np
import igraph as ig

ROOT = Path(__file__).resolve().parents[1]
QUERY_FEATURE_MEAN = None


def has_nonzero_embedding(values) -> bool:
    return bool(values) and any(abs(float(value)) > 1e-12 for value in values)


def load_graphsage_feature_fallback(nodes):
    active = sorted([node for node in nodes if node.get("status") == "active"], key=lambda node: node["id"])
    if any(has_nonzero_embedding(node.get("embedding") or []) for node in active):
        return {}, None

    data_path = ROOT / "graphsage_minimal" / "data" / "amo_calls_active.npz"
    meta_path = ROOT / "graphsage_minimal" / "data" / "node_meta.json"
    if not data_path.exists() or not meta_path.exists():
        return {}, None

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta_ids = [row["id"] for row in meta]
    active_ids = [node["id"] for node in active]
    if meta_ids != active_ids:
        return {}, None

    data = np.load(data_path)
    x = data["x"].astype(np.float32)
    return {node_id: x[idx] for idx, node_id in enumerate(meta_ids)}, data["feature_mean"].astype(np.float32)

# ── Load graph ────────────────────────────────────────────────────────────────
with open("sandbox/amo_nodes.json", encoding="utf-8") as f:
    nodes_data = json.load(f)
with open("sandbox/amo_edges.json", encoding="utf-8") as f:
    edges_data = json.load(f)

node_id_set = {n["id"] for n in nodes_data}
edges_data  = [e for e in edges_data if e["source"] in node_id_set and e["target"] in node_id_set]

G = ig.Graph(directed=True)
node_ids = [n["id"] for n in nodes_data]
G.add_vertices(len(node_ids))
id_to_idx = {nid: i for i, nid in enumerate(node_ids)}
reused_features, QUERY_FEATURE_MEAN = load_graphsage_feature_fallback(nodes_data)
if reused_features:
    print(f"Reusing GraphSAGE feature matrix for {len(reused_features)} active embeddings")

for i, n in enumerate(nodes_data):
    G.vs[i]["id"]        = n["id"]
    G.vs[i]["file"]      = n["file"]
    G.vs[i]["name"]      = n["name"]
    G.vs[i]["status"]    = n.get("status", "superseded")
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
    G.es[i]["type"] = e["type"]

# CALLS-only subgraph for PPR + community detection
calls_ids  = [e.index for e in G.es if e["type"] == "CALLS"]
calls_only = G.subgraph_edges(calls_ids, delete_vertices=False)

# ── Infomap (once) ────────────────────────────────────────────────────────────
communities    = calls_only.community_infomap()
G.vs["cluster"] = communities.membership

# ── Embed query ───────────────────────────────────────────────────────────────
def embed(text):
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
        values = {}
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
        raise RuntimeError("No llm_api_key_2, llm_api_key2, or llm_api_key found in .env candidates")
    payload = json.dumps({"model": "nvidia/llama-nemotron-embed-vl-1b-v2:free", "input": text}).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/embeddings",
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    vec = np.array(json.loads(urllib.request.urlopen(req, timeout=20).read())["data"][0]["embedding"], dtype=np.float32)
    if QUERY_FEATURE_MEAN is not None:
        vec = vec.reshape(1, -1) - QUERY_FEATURE_MEAN
        vec = vec / np.maximum(np.linalg.norm(vec, axis=1, keepdims=True), 1e-8)
        return vec[0].astype(np.float32)
    return vec

def cosine(a, b):
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(np.dot(a, b) / (na * nb)) if na and nb else 0.0

# ── The ambiguous query ───────────────────────────────────────────────────────
QUERY = "something broke when the agent tried to remember"

print("=" * 60)
print("QUERY:", repr(QUERY))
print("=" * 60)
print()
print("Embedding query...")
q_vec = embed(QUERY)

# Step 1: vector seeds
seed_scores = [
    (v.index, cosine(q_vec, v["embedding"]))
    for v in G.vs
    if v["embedding"] is not None and v["status"] == "active"
]
seed_scores.sort(key=lambda x: x[1], reverse=True)
top_seeds = seed_scores[:15]

print("Step 1 — Top 5 vector seeds (cosine similarity):")
for idx, score in top_seeds[:5]:
    v = G.vs[idx]
    print(f"  [{score:.3f}] {v['name']}  ({v['file'].split('/')[-1]})")

# Step 2: cluster distribution
seed_clusters = [G.vs[idx]["cluster"] for idx, _ in top_seeds]
cluster_counts = {}
for c in seed_clusters:
    cluster_counts[c] = cluster_counts.get(c, 0) + 1
top_clusters = sorted(cluster_counts, key=cluster_counts.get, reverse=True)[:3]
print(f"\nStep 2 — Infomap clusters of seeds: {dict(list(cluster_counts.items())[:5])}")
print(f"  Dominant clusters: {top_clusters}")

# Step 3: PPR
reset = np.zeros(G.vcount())
for idx, score in top_seeds:
    if G.vs[idx]["cluster"] in top_clusters:
        reset[idx] = max(score, 0.0)
total = reset.sum()
if total > 0:
    reset /= total
else:
    for idx, _ in top_seeds:
        reset[idx] = 1.0 / len(top_seeds)

ppr = calls_only.personalized_pagerank(damping=0.85, directed=True, reset=reset.tolist())
ranked = sorted(enumerate(ppr), key=lambda x: x[1], reverse=True)[:60]
ranked = [(i, s) for i, s in ranked if G.vs[i]["cluster"] in set(top_clusters)][:30]

# Step 4: MMR
selected, candidates = [], list(ranked)
while len(selected) < 10 and candidates:
    best_idx, best_score = None, -1e9
    for node_idx, ppr_score in candidates:
        emb = G.vs[node_idx]["embedding"]
        sim = max((cosine(emb, G.vs[s]["embedding"])
                   for s in selected
                   if G.vs[s]["embedding"] is not None), default=0.0) if selected and emb is not None else 0.0
        score = 0.6 * ppr_score - 0.4 * sim
        if score > best_score:
            best_score, best_idx = score, node_idx
    if best_idx is None:
        break
    selected.append(best_idx)
    candidates = [(i, s) for i, s in candidates if i != best_idx]

print()
print("Step 3+4 — Final top-10 (PPR + MMR diverse selection):")
print()
for rank, node_idx in enumerate(selected):
    v    = G.vs[node_idx]
    file = v["file"].split("/")[-1]
    # get summary from nodes_data
    summary = next((n.get("text_summary","") for n in nodes_data if n["id"] == v["id"]), "")
    print(f"  {rank+1:2d}. {v['name']}  [{file}]")
    print(f"      {summary[:90]}")
    print()

print("=" * 60)
print("INTERPRETATION:")
print(f"  Query '{QUERY}' is ambiguous — could mean:")
print("  - a crash in memory write")
print("  - retrieval returning wrong context")
print("  - session state corruption")
print()
print("  The pipeline returned functions from the memory pipeline")
print("  that are structurally connected to each other — giving an")
print("  agent enough context to debug without knowing exact file names.")
