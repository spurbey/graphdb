from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VECTOR_JSON = ROOT / ".turbovec_code.json"
DEFAULT_EVAL_CONFIG = ROOT / "eval" / "vector_backend_queries.json"
DEFAULT_OUT = ROOT / "eval" / "report" / "vector_backend_benchmark.json"
HELIX_URL = "http://127.0.0.1:6969"
HELIX_BENCH_LABEL = "VectorBenchDograhCode"


def stable_uint64(text: str) -> int:
    digest = hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "little", signed=False)
    return value or 1


def load_vectors(path: Path) -> tuple[list[str], np.ndarray]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    ids: list[str] = []
    vectors: list[list[float]] = []
    for node_id, vec in raw.items():
        if isinstance(vec, list) and len(vec) == 384:
            ids.append(node_id)
            vectors.append(vec)
    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.maximum(norms, 1e-8)
    return ids, matrix


def load_semantic_queries(path: Path) -> list[dict]:
    items = json.loads(path.read_text(encoding="utf-8"))
    semantic = [item for item in items if item.get("capability") == "semantic_search"]
    return semantic or items


def embed_queries(prompts: list[str]) -> np.ndarray:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("all-MiniLM-L6-v2")
    vectors = model.encode(prompts, show_progress_bar=False, normalize_embeddings=True)
    return np.asarray(vectors, dtype=np.float32)


def summarize_hits(query: dict, result_ids: list[str]) -> dict:
    gt = query.get("ground_truth", {})
    expected = [s.lower() for s in gt.get("expected_substrings", [])]
    file_hint = str(gt.get("expected_file_hint", "")).lower()

    ranks = []
    for i, node_id in enumerate(result_ids, start=1):
        low = node_id.lower()
        if expected and any(s in low for s in expected):
            ranks.append(i)
        elif file_hint and file_hint in low:
            ranks.append(i)
    first_rank = min(ranks) if ranks else None
    return {
        "query_id": query.get("id"),
        "prompt": query.get("prompt"),
        "first_expected_rank": first_rank,
        "hit_at_5": first_rank is not None and first_rank <= 5,
        "hit_at_10": first_rank is not None and first_rank <= 10,
        "top_ids": result_ids[:10],
    }


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    idx = min(len(values) - 1, max(0, round((pct / 100) * (len(values) - 1))))
    return values[idx]


def summarize_backend(name: str, query_results: list[dict], timings_ms: list[float], extra: dict) -> dict:
    ranks = [r["first_expected_rank"] for r in query_results if r["first_expected_rank"] is not None]
    return {
        "backend": name,
        "available": True,
        "query_count": len(query_results),
        "hit_at_5": sum(1 for r in query_results if r["hit_at_5"]),
        "hit_at_10": sum(1 for r in query_results if r["hit_at_10"]),
        "mean_first_expected_rank": round(statistics.mean(ranks), 3) if ranks else None,
        "latency_ms": {
            "p50": round(statistics.median(timings_ms), 3) if timings_ms else None,
            "p95": round(percentile(timings_ms, 95), 3) if timings_ms else None,
            "all": [round(v, 3) for v in timings_ms],
        },
        "results": query_results,
        **extra,
    }


def run_turbovec(bit_width: int, ids: list[str], vectors: np.ndarray, query_vectors: np.ndarray, queries: list[dict], k: int) -> dict:
    try:
        import turbovec
    except Exception as exc:
        return {"backend": f"turbovec_{bit_width}bit", "available": False, "error": repr(exc)}

    vector_ids = np.asarray([stable_uint64(node_id) for node_id in ids], dtype=np.uint64)
    reverse = {int(v): node_id for node_id, v in zip(ids, vector_ids)}

    build_start = time.perf_counter()
    index = turbovec.IdMapIndex(dim=vectors.shape[1], bit_width=bit_width)
    index.add_with_ids(vectors, vector_ids)
    index.prepare()
    build_ms = (time.perf_counter() - build_start) * 1000

    out_path = ROOT / "eval" / "report" / f"dograh_turbovec_{bit_width}bit.tv"
    write_start = time.perf_counter()
    index.write(str(out_path))
    write_ms = (time.perf_counter() - write_start) * 1000

    query_results = []
    timings = []
    for query, qv in zip(queries, query_vectors):
        started = time.perf_counter()
        _scores, result_ids = index.search(qv.reshape(1, -1), k)
        timings.append((time.perf_counter() - started) * 1000)
        node_ids = [reverse[int(v)] for v in result_ids[0] if int(v) in reverse]
        query_results.append(summarize_hits(query, node_ids))

    return summarize_backend(
        f"turbovec_{bit_width}bit",
        query_results,
        timings,
        {
            "bit_width": bit_width,
            "build_ms": round(build_ms, 3),
            "write_ms": round(write_ms, 3),
            "index_file": str(out_path),
            "index_file_bytes": out_path.stat().st_size if out_path.exists() else None,
        },
    )


def _helix_imports():
    from helixdb import (
        Client,
        IndexSpec,
        Projection,
        PropertyInput,
        PropertyValue,
        g,
        read_batch,
        write_batch,
    )

    return Client, IndexSpec, Projection, PropertyInput, PropertyValue, g, read_batch, write_batch


def ensure_helix_benchmark_nodes(ids: list[str], vectors: np.ndarray, batch_size: int = 200) -> dict:
    Client, IndexSpec, _Projection, PropertyInput, PropertyValue, g, _read_batch, write_batch = _helix_imports()
    client = Client(HELIX_URL)

    started = time.perf_counter()
    batch = write_batch()
    batch = batch.var_as("idx_id", g().create_index_if_not_exists(IndexSpec.node_unique_equality(HELIX_BENCH_LABEL, "node_id")))
    batch = batch.var_as("idx_vec", g().create_index_if_not_exists(IndexSpec.node_vector(HELIX_BENCH_LABEL, "code_vec")))
    client.query().dynamic(batch.returning(["idx_id", "idx_vec"]).to_dynamic_request()).send()
    index_ms = (time.perf_counter() - started) * 1000

    inserted_attempts = 0
    insert_started = time.perf_counter()
    for offset in range(0, len(ids), batch_size):
        batch = write_batch()
        names = []
        for i, (node_id, vec) in enumerate(zip(ids[offset:offset + batch_size], vectors[offset:offset + batch_size])):
            name = f"n{i}"
            props = {
                "node_id": PropertyInput.value(f"bench:{node_id}"),
                "source_node_id": PropertyInput.value(node_id),
                "vector_id": PropertyInput.value(str(stable_uint64(node_id))),
                "code_vec": PropertyInput.value(PropertyValue.f32_array(vec.astype(np.float32).tolist())),
            }
            batch = batch.var_as(name, g().add_n(HELIX_BENCH_LABEL, props))
            names.append(name)
        try:
            client.query().dynamic(batch.returning(names).to_dynamic_request()).send()
            inserted_attempts += len(names)
        except Exception:
            # Re-runs can hit unique-key conflicts. The fixed label is intentional so
            # the second run measures search without endlessly creating new labels.
            pass

    return {
        "index_setup_ms": round(index_ms, 3),
        "insert_attempts": inserted_attempts,
        "insert_ms": round((time.perf_counter() - insert_started) * 1000, 3),
    }


def run_helix(ids: list[str], vectors: np.ndarray, query_vectors: np.ndarray, queries: list[dict], k: int, skip_insert: bool) -> dict:
    try:
        Client, _IndexSpec, Projection, _PropertyInput, _PropertyValue, g, read_batch, _write_batch = _helix_imports()
        client = Client(HELIX_URL)
    except Exception as exc:
        return {"backend": "helix_native", "available": False, "error": repr(exc)}

    setup = {}
    if not skip_insert:
        try:
            setup = ensure_helix_benchmark_nodes(ids, vectors)
        except Exception as exc:
            return {"backend": "helix_native", "available": False, "phase": "setup", "error": repr(exc)}

    query_results = []
    timings = []
    for query, qv in zip(queries, query_vectors):
        try:
            batch = (
                read_batch()
                .var_as(
                    "hits",
                    g()
                    .vector_search_nodes(HELIX_BENCH_LABEL, "code_vec", qv.astype(np.float32).tolist(), k)
                    .project([
                        Projection.property("source_node_id"),
                        Projection.property("node_id"),
                    ]),
                )
                .returning(["hits"])
            )
            started = time.perf_counter()
            res = client.query().dynamic(batch.to_dynamic_request()).send()
            timings.append((time.perf_counter() - started) * 1000)
            rows = res.get("hits", {}).get("properties", [])
            node_ids = [row.get("source_node_id") or row.get("node_id", "").replace("bench:", "") for row in rows]
            query_results.append(summarize_hits(query, node_ids))
        except Exception as exc:
            return {
                "backend": "helix_native",
                "available": False,
                "phase": "query",
                "error": repr(exc),
                "setup": setup,
            }

    return summarize_backend("helix_native", query_results, timings, {"setup": setup, "label": HELIX_BENCH_LABEL})


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare real TurboVec 2/4-bit and Helix native vector search on Dograh embeddings.")
    parser.add_argument("--vectors", type=Path, default=DEFAULT_VECTOR_JSON)
    parser.add_argument("--eval-config", type=Path, default=DEFAULT_EVAL_CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--skip-helix-insert", action="store_true")
    args = parser.parse_args()

    ids, vectors = load_vectors(args.vectors)
    queries = load_semantic_queries(args.eval_config)
    query_vectors = embed_queries([q["prompt"] for q in queries])

    args.out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "corpus": {
            "vector_json": str(args.vectors),
            "vector_count": len(ids),
            "dim": int(vectors.shape[1]) if len(ids) else None,
        },
        "query_count": len(queries),
        "k": args.k,
        "backends": [],
    }

    report["backends"].append(run_turbovec(2, ids, vectors, query_vectors, queries, args.k))
    report["backends"].append(run_turbovec(4, ids, vectors, query_vectors, queries, args.k))
    report["backends"].append(run_helix(ids, vectors, query_vectors, queries, args.k, args.skip_helix_insert))

    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
