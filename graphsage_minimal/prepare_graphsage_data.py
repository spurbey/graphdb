from __future__ import annotations

import json
import random
import zipfile
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SANDBOX = ROOT / "sandbox"
OUT = ROOT / "graphsage_minimal" / "data"
OUT.mkdir(parents=True, exist_ok=True)

RNG_SEED = 7

EVAL_QUERIES = [
    {
        "id": "memory_ingestion",
        "query": "memory ingestion pipeline hook processing",
        "target_name": "ingest_hook_payload",
        "target_hint": "ingest",
    },
    {
        "id": "memory_context",
        "query": "retrieve context from memory for agent",
        "target_name": "memory_context_pack",
        "target_hint": "tools",
    },
    {
        "id": "snapshot_store",
        "query": "store and save session memory snapshot",
        "target_name": "export_snapshot",
        "target_hint": "snapshots",
    },
]


def _load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _active_embedding_nodes(nodes: list[dict]) -> list[dict]:
    active = []
    for node in nodes:
        emb = node.get("embedding") or []
        if node.get("status") == "active" and len(emb) > 0:
            active.append(node)
    active.sort(key=lambda n: n["id"])
    return active


def _undirected_unique_edges(edges: list[dict], id_to_idx: dict[str, int]) -> np.ndarray:
    pairs: set[tuple[int, int]] = set()
    for edge in edges:
        if edge.get("type") != "CALLS":
            continue
        src = id_to_idx.get(edge.get("source"))
        dst = id_to_idx.get(edge.get("target"))
        if src is None or dst is None or src == dst:
            continue
        a, b = (src, dst) if src < dst else (dst, src)
        pairs.add((a, b))
    if not pairs:
        return np.zeros((2, 0), dtype=np.int64)
    ordered = np.array(sorted(pairs), dtype=np.int64)
    return ordered.T


def _split_edges(edge_index: np.ndarray) -> dict[str, np.ndarray]:
    rng = random.Random(RNG_SEED)
    pairs = list(zip(edge_index[0].tolist(), edge_index[1].tolist()))
    rng.shuffle(pairs)

    n = len(pairs)
    n_train = int(n * 0.8)
    n_val = int(n * 0.1)
    chunks = {
        "train": pairs[:n_train],
        "val": pairs[n_train:n_train + n_val],
        "test": pairs[n_train + n_val:],
    }
    result = {}
    for name, chunk in chunks.items():
        result[name] = np.array(chunk, dtype=np.int64).T if chunk else np.zeros((2, 0), dtype=np.int64)
    return result


def _write_bundle() -> Path:
    bundle = ROOT / "graphsage_minimal_bundle.zip"
    include = [
        ROOT / "graphsage_minimal" / "README.md",
        ROOT / "graphsage_minimal" / "prepare_graphsage_data.py",
        ROOT / "graphsage_minimal" / "embed_eval_queries.py",
        ROOT / "graphsage_minimal" / "train_graphsage.py",
        ROOT / "graphsage_minimal" / "rank_with_graphsage.py",
        ROOT / "graphsage_minimal" / "probe_commit_inductive.py",
        ROOT / "graphsage_minimal" / "data" / "amo_calls_active.npz",
        ROOT / "graphsage_minimal" / "data" / "node_meta.json",
        ROOT / "graphsage_minimal" / "data" / "eval_queries.json",
    ]
    optional = [ROOT / "graphsage_minimal" / "data" / "query_embeddings.npy"]
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in include + [p for p in optional if p.exists()]:
            zf.write(path, path.relative_to(ROOT).as_posix())
    return bundle


def main() -> None:
    nodes = _load_json(SANDBOX / "amo_nodes.json")
    edges = _load_json(SANDBOX / "amo_edges.json")

    active = _active_embedding_nodes(nodes)
    id_to_idx = {node["id"]: i for i, node in enumerate(active)}
    raw_x = np.asarray([node["embedding"] for node in active], dtype=np.float32)

    # Keep data centered and length-normalized for stable CPU/GPU training.
    feature_mean = raw_x.mean(axis=0, keepdims=True)
    x = raw_x - feature_mean
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    x = x / np.maximum(norms, 1e-8)

    calls = _undirected_unique_edges(edges, id_to_idx)
    splits = _split_edges(calls)

    np.savez_compressed(
        OUT / "amo_calls_active.npz",
        x=x,
        feature_mean=feature_mean.astype(np.float32),
        edge_index=calls,
        train_edges=splits["train"],
        val_edges=splits["val"],
        test_edges=splits["test"],
    )

    meta = [
        {
            "idx": i,
            "id": node["id"],
            "name": node.get("name", ""),
            "file": node.get("file", ""),
            "version_count": node.get("version_count", 0),
        }
        for i, node in enumerate(active)
    ]
    (OUT / "node_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (OUT / "eval_queries.json").write_text(json.dumps(EVAL_QUERIES, indent=2), encoding="utf-8")

    bundle = _write_bundle()

    print("Prepared GraphSAGE data")
    print(f"  active nodes: {len(active)}")
    print(f"  feature dim: {x.shape[1] if len(active) else 0}")
    print(f"  unique active CALLS edges: {calls.shape[1]}")
    print(f"  train/val/test: {splits['train'].shape[1]}/{splits['val'].shape[1]}/{splits['test'].shape[1]}")
    print(f"  wrote: {OUT / 'amo_calls_active.npz'}")
    print(f"  bundle: {bundle}")


if __name__ == "__main__":
    main()
