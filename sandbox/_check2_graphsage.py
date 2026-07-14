import json, numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sage_emb = np.load(ROOT / "graphsage_minimal" / "out" / "graphsage_embeddings.npy")
meta = json.loads((ROOT / "graphsage_minimal" / "data" / "node_meta.json").read_text(encoding="utf-8"))
meta_by_id = {m["id"]: m for m in meta}
sage_n = sage_emb / np.maximum(np.linalg.norm(sage_emb, axis=1, keepdims=True), 1e-8)

def is_test(m):
    n = m.get("name","") or ""
    f = (m.get("file","") or "").replace("\\","/").split("/")[-1]
    return n.startswith("test_") or f.startswith("test_")

def knn(aid, k=8, skip_tests=False):
    if aid not in meta_by_id: return None
    idx = meta_by_id[aid]["idx"]
    scores = (sage_n @ sage_n[idx]).tolist()
    ranked = sorted([(scores[i], meta[i]) for i in range(len(meta)) if meta[i]["id"] != aid],
                    key=lambda x: x[0], reverse=True)
    if skip_tests: ranked = [(s,m) for s,m in ranked if not is_test(m)]
    return ranked[:k]

for aid, label in [
    ("src/agent_memory_orchestrator/memory/ingest.py::ingest_hook_payload", "ingest_hook_payload"),
    ("src/agent_memory_orchestrator/memory/storage.py::add_memory_unit", "add_memory_unit"),
    ("src/agent_memory_orchestrator/graph/service.py::rebuild_graph_cache", "rebuild_graph_cache"),
    ("src/agent_memory_orchestrator/mcp/server.py::memory_write", "memory_write"),
]:
    print(f"\n=== {label} ===")
    raw = knn(aid, 8, skip_tests=False)
    if raw is None: print("NOT IN META"); continue
    prod = knn(aid, 8, skip_tests=True)
    test_count = sum(1 for _,m in raw if is_test(m))
    print(f"Unfiltered: {test_count}/8 test functions")
    for s,m in raw:
        t = "[TEST]" if is_test(m) else "[PROD]"
        print(f"  {t} {s:.3f} {m['name']} [{m['file'].split('/')[-1]}]")
    if test_count > 0:
        print(f"Filtered (what find_structural_siblings returns):")
        for s,m in prod:
            print(f"  {s:.3f} {m['name']} [{m['file'].split('/')[-1]}]")
