"""GraphSAGE structural siblings vs vector semantic similarity — fast version."""
import json
import sys
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

sage_emb = np.load(ROOT / "graphsage_minimal" / "out" / "graphsage_embeddings.npy")
meta = json.loads((ROOT / "graphsage_minimal" / "data" / "node_meta.json").read_text(encoding="utf-8"))
nodes_data = json.loads((ROOT / "sandbox" / "amo_nodes.json").read_text(encoding="utf-8"))

has_nz = lambda v: bool(v) and any(abs(float(x)) > 1e-12 for x in v)

# Build lookup: meta id -> index in sage_emb
meta_idx = {m["id"]: m["idx"] for m in meta}
meta_name = {m["id"]: m["name"] for m in meta}
meta_file = {m["id"]: m["file"].split("/")[-1] for m in meta}
meta_ids = [m["id"] for m in meta]  # ordered list

# Build vector embeddings matrix for meta nodes only
vec_map = {n["id"]: np.array(n["embedding"], dtype=np.float32)
           for n in nodes_data if has_nz(n.get("embedding", []))}

# Only keep meta nodes that have vector embeddings
meta_with_vec = [m for m in meta if m["id"] in vec_map]
vec_matrix = np.array([vec_map[m["id"]] for m in meta_with_vec], dtype=np.float32)
vec_norms = np.linalg.norm(vec_matrix, axis=1, keepdims=True)
vec_matrix_n = vec_matrix / np.maximum(vec_norms, 1e-8)
vec_meta_ids = [m["id"] for m in meta_with_vec]

# Normalize sage embeddings
sage_norms = np.linalg.norm(sage_emb, axis=1, keepdims=True)
sage_matrix_n = sage_emb / np.maximum(sage_norms, 1e-8)

def top_k(scores, ids, anchor_id, k):
    ranked = sorted(zip(scores, ids), reverse=True)
    return [(s, i) for s, i in ranked if i != anchor_id][:k]

ANCHORS = [
    "src/agent_memory_orchestrator/memory/ingest.py::ingest_hook_payload",
    "src/agent_memory_orchestrator/memory/storage.py::add_memory_unit",
    "src/agent_memory_orchestrator/privacy.py::redact_secrets",
    "src/agent_memory_orchestrator/graph/service.py::rebuild_graph_cache",
    "src/agent_memory_orchestrator/mcp/server.py::memory_write",
]

lines = []
lines.append("GraphSAGE Structural Siblings vs Vector Semantic Similarity")
lines.append("=" * 70)

for anchor_id in ANCHORS:
    if anchor_id not in meta_idx:
        lines.append(f"\nSKIP: {anchor_id} not in GraphSAGE meta")
        continue

    anchor_name = meta_name[anchor_id]
    anchor_fname = meta_file[anchor_id]
    sage_idx = meta_idx[anchor_id]

    # GraphSAGE k-NN
    sage_scores = (sage_matrix_n @ sage_matrix_n[sage_idx]).tolist()
    sage_top = top_k(sage_scores, meta_ids, anchor_id, 8)

    # Vector k-NN (only over nodes that have both sage + vector embeddings)
    if anchor_id in vec_map:
        anchor_vec_n = vec_map[anchor_id] / max(float(np.linalg.norm(vec_map[anchor_id])), 1e-8)
        vec_scores = (vec_matrix_n @ anchor_vec_n).tolist()
        vec_top = top_k(vec_scores, vec_meta_ids, anchor_id, 8)
    else:
        vec_top = []

    sage_id_set = {i for _, i in sage_top}
    vec_id_set  = {i for _, i in vec_top}
    overlap     = sage_id_set & vec_id_set
    sage_only   = sage_id_set - vec_id_set
    vec_only    = vec_id_set  - sage_id_set

    lines.append(f"\nAnchor: {anchor_name} [{anchor_fname}]")
    lines.append(f"  GraphSAGE top-8 (structural):")
    for score, nid in sage_top:
        tag = "[BOTH]" if nid in overlap else "[SAGE]"
        lines.append(f"    {tag} [{score:.3f}] {meta_name.get(nid,'?')} [{meta_file.get(nid,'?')}]")

    lines.append(f"  Vector top-8 (semantic):")
    for score, nid in vec_top:
        tag = "[BOTH]" if nid in overlap else "[VEC ]"
        lines.append(f"    {tag} [{score:.3f}] {meta_name.get(nid,'?')} [{meta_file.get(nid,'?')}]")

    lines.append(f"  Overlap={len(overlap)}  SAGE-only={len(sage_only)}  VEC-only={len(vec_only)}")
    lines.append(f"  GraphSAGE unique (same structural role, different semantic domain):")
    for nid in sage_only:
        lines.append(f"    {meta_name.get(nid,'?')} [{meta_file.get(nid,'?')}]")

lines.append("\nINTERPRETATION:")
lines.append("  SAGE-only = structurally equivalent but semantically different")
lines.append("  VEC-only  = semantically similar but structurally different")
lines.append("  High SAGE-only count = GraphSAGE finds architectural patterns")
lines.append("  vector search misses")

print("\n".join(lines))
