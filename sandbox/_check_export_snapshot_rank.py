"""
Pull the actual embedding rank and cosine score of export_snapshot
against the old_snapshot query, using the post-Phase-0 fixed-seed graph.
Confirms whether the miss is a near-miss or a total miss.
"""
import json
import random as _random
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

# Phase 0a: fixed seed — same as running pipeline
_random.seed(42)
np.random.seed(42)
_communities = calls_only.community_infomap()
for _i, _c in enumerate(_communities.membership):
    G.vs[_i]["cluster_id"] = _c


def _is_candidate(v) -> bool:
    name      = v["name"] or ""
    file      = v["file"] or ""
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


# ── Load the stored query embedding from ablation results ─────────────────────
# We don't have the raw query vector stored, but we can reconstruct the
# cosine rank from the stored top-10 scores as anchor points.
# Better: use the query_embeddings.npy if available, else re-embed.

# Try graphsage data query embeddings first (may have old_snapshot)
qemb_path = ROOT / "graphsage_minimal" / "data" / "query_embeddings.npy"
eval_path  = ROOT / "graphsage_minimal" / "data" / "eval_queries.json"

query_vec = None
if qemb_path.exists() and eval_path.exists():
    eval_queries = json.loads(eval_path.read_text(encoding="utf-8"))
    q_vecs = np.load(qemb_path).astype(np.float32)
    for i, q in enumerate(eval_queries):
        if q.get("id") == "snapshot_store" or "snapshot" in q.get("query", "").lower():
            query_vec = q_vecs[i]
            print(f"Using cached query embedding: '{q['query']}'")
            break

if query_vec is None:
    # Need to embed — load API key and call
    import os, urllib.request
    key = ""
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
                key = vals[name]
                break
        if key:
            break

    query_text = "store and save session memory snapshot"
    print(f"Embedding query: '{query_text}'")
    payload = json.dumps({"model": "nvidia/llama-nemotron-embed-vl-1b-v2:free",
                          "input": query_text}).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/embeddings",
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=20).read())
    query_vec = np.array(resp["data"][0]["embedding"], dtype=np.float32)
    print("  Done.")

# ── Score all candidates ───────────────────────────────────────────────────────
scores = []
for v in G.vs:
    if _is_candidate(v):
        sim = cosine(query_vec, v["embedding"])
        scores.append((v.index, sim, v["name"], v["file"].split("/")[-1]))

scores.sort(key=lambda x: x[1], reverse=True)

# ── Report export_snapshot rank ───────────────────────────────────────────────
print(f"\nTotal candidates scored: {len(scores)}")
print()
print("Top-20 by cosine similarity:")
for rank, (idx, sim, name, fname) in enumerate(scores[:20], 1):
    marker = " <-- TARGET" if name == "export_snapshot" else ""
    print(f"  {rank:3d}. [{sim:.4f}] {name} [{fname}]{marker}")

print()
export_rank = None
export_score = None
for rank, (idx, sim, name, fname) in enumerate(scores, 1):
    if name == "export_snapshot" and "snapshots" in G.vs[idx]["file"]:
        export_rank = rank
        export_score = sim
        print(f"export_snapshot [snapshots.py]:")
        print(f"  cosine score : {sim:.4f}")
        print(f"  rank         : {rank} / {len(scores)}")
        print(f"  cluster_id   : {G.vs[idx]['cluster_id']}")
        break

if export_rank is None:
    print("export_snapshot [snapshots.py] not found in candidate pool")
else:
    print()
    if export_rank <= 15:
        verdict = "NEAR-MISS: would be a seed — PPR scoping is the blocker"
    elif export_rank <= 50:
        verdict = "MODERATE MISS: better summary likely brings it into top-15 seeds"
    elif export_rank <= 200:
        verdict = "SIGNIFICANT MISS: better summary helps but not guaranteed to reach top-10"
    else:
        verdict = "TOTAL MISS: near-zero similarity — better summary alone unlikely sufficient"
    print(f"Verdict: {verdict}")

# ── Also check what nodes ARE near export_snapshot in embedding space ─────────
print()
print("20 nodes closest to export_snapshot in embedding space (excluding itself):")
es_idx = next(
    (v.index for v in G.vs if v["name"] == "export_snapshot" and "snapshots" in (v["file"] or "")),
    None
)
if es_idx is not None:
    es_emb = G.vs[es_idx]["embedding"]
    neighbors = []
    for v in G.vs:
        if v.index == es_idx or v["embedding"] is None:
            continue
        neighbors.append((cosine(es_emb, v["embedding"]), v["name"], v["file"].split("/")[-1], v["status"]))
    neighbors.sort(reverse=True)
    for sim, name, fname, status in neighbors[:20]:
        print(f"  [{sim:.4f}] {name} [{fname}] ({status})")
