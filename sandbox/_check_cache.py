"""Check embedding cache and node embedding status."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
cache_path = ROOT / "sandbox/amo_embedding_cache.json"
nodes_path = ROOT / "sandbox/amo_nodes.json"

if cache_path.exists():
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    items = cache.get("items", {})
    nonzero = sum(1 for v in items.values() if any(abs(x) > 1e-12 for x in (v.get("embedding") or [])))
    print(f"Embedding cache: {len(items)} entries, {nonzero} non-zero, model={cache.get('model')}, dim={cache.get('dim')}")
else:
    print("No embedding cache found at", cache_path)

if nodes_path.exists():
    nodes = json.loads(nodes_path.read_text(encoding="utf-8"))
    active = [n for n in nodes if n.get("status") == "active"]
    with_emb = [n for n in active if any(abs(x) > 1e-12 for x in (n.get("embedding") or []))]
    print(f"Nodes: {len(nodes)} total, {len(active)} active, {len(with_emb)} with non-zero embeddings")
