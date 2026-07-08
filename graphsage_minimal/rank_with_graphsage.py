from __future__ import annotations

import json
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "graphsage_minimal" / "data"
OUT = ROOT / "graphsage_minimal" / "out"


def cosine_matrix(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    q = query / max(np.linalg.norm(query), 1e-8)
    m = matrix / np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-8)
    return m @ q


def target_indices(meta: list[dict], target_name: str, target_hint: str) -> list[int]:
    hint = target_hint.lower()
    return [
        row["idx"]
        for row in meta
        if row.get("name") == target_name and hint in row.get("file", "").lower()
    ]


def rank_of(scores: np.ndarray, targets: list[int]) -> int | None:
    if not targets:
        return None
    order = np.argsort(-scores)
    positions = {int(idx): rank + 1 for rank, idx in enumerate(order)}
    return min(positions[t] for t in targets if t in positions)


def top_rows(scores: np.ndarray, meta: list[dict], limit: int = 10) -> list[dict]:
    order = np.argsort(-scores)[:limit]
    rows = []
    for rank, idx in enumerate(order, start=1):
        item = meta[int(idx)]
        rows.append({
            "rank": rank,
            "score": float(scores[int(idx)]),
            "name": item["name"],
            "file": item["file"],
        })
    return rows


def restrict_to_top(scores: np.ndarray, base_scores: np.ndarray, candidate_k: int) -> np.ndarray:
    restricted = np.full(scores.shape, -1e9, dtype=np.float32)
    candidates = np.argsort(-base_scores)[:candidate_k]
    restricted[candidates] = scores[candidates]
    return restricted


def main() -> None:
    data = np.load(DATA / "amo_calls_active.npz")
    x = data["x"].astype(np.float32)
    meta = json.loads((DATA / "node_meta.json").read_text(encoding="utf-8"))
    queries = json.loads((DATA / "eval_queries.json").read_text(encoding="utf-8"))

    q_path = DATA / "query_embeddings.npy"
    z_path = OUT / "graphsage_embeddings.npy"
    if not q_path.exists():
        raise SystemExit(f"Missing {q_path}; run embed_eval_queries.py first or upload precomputed query embeddings")
    if not z_path.exists():
        raise SystemExit(f"Missing {z_path}; run train_graphsage.py first")

    q = np.load(q_path).astype(np.float32)
    z = np.load(z_path).astype(np.float32)

    report = []
    sweep_seed_k = [15, 30, 50]
    sweep_alpha = [0.1, 0.2, 0.3, 0.4, 0.5]
    sweep_candidate_k = [50, 100]

    for i, query in enumerate(queries):
        vec_scores = cosine_matrix(q[i], x)
        seed_order = np.argsort(-vec_scores)[:50]
        seed_weights = np.maximum(vec_scores[seed_order], 0.0)
        if seed_weights.sum() <= 0:
            seed_weights = np.ones_like(seed_weights)
        seed_weights = seed_weights / seed_weights.sum()
        graph_query = (z[seed_order] * seed_weights[:, None]).sum(axis=0)
        sage_scores = cosine_matrix(graph_query, z)
        hybrid_scores = 0.7 * vec_scores + 0.3 * sage_scores
        rerank_scores = restrict_to_top(hybrid_scores, vec_scores, candidate_k=100)

        targets = target_indices(meta, query["target_name"], query["target_hint"])

        sweep = []
        for candidate_k in sweep_candidate_k:
            for seed_k in sweep_seed_k:
                seeds = np.argsort(-vec_scores)[:seed_k]
                weights = np.maximum(vec_scores[seeds], 0.0)
                if weights.sum() <= 0:
                    weights = np.ones_like(weights)
                weights = weights / weights.sum()
                seed_query = (z[seeds] * weights[:, None]).sum(axis=0)
                seed_scores = cosine_matrix(seed_query, z)
                for alpha in sweep_alpha:
                    scores = (1.0 - alpha) * vec_scores + alpha * seed_scores
                    restricted = restrict_to_top(scores, vec_scores, candidate_k)
                    sweep.append({
                        "candidate_k": candidate_k,
                        "seed_k": seed_k,
                        "alpha": alpha,
                        "global_rank": rank_of(scores, targets),
                        "rerank_rank": rank_of(restricted, targets),
                    })

        row = {
            "id": query["id"],
            "query": query["query"],
            "target_name": query["target_name"],
            "targets": targets,
            "vector_rank": rank_of(vec_scores, targets),
            "graphsage_seed_rank": rank_of(sage_scores, targets),
            "hybrid_rank": rank_of(hybrid_scores, targets),
            "rerank_top100_rank": rank_of(rerank_scores, targets),
            "sweep": sweep,
            "vector_top10": top_rows(vec_scores, meta),
            "graphsage_seed_top10": top_rows(sage_scores, meta),
            "hybrid_top10": top_rows(hybrid_scores, meta),
            "rerank_top10": top_rows(rerank_scores, meta),
        }
        report.append(row)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ranking_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    for row in report:
        print()
        print(row["query"])
        print(f"target={row['target_name']}")
        print(
            f"vector_rank={row['vector_rank']} "
            f"graphsage_seed_rank={row['graphsage_seed_rank']} "
            f"hybrid_rank={row['hybrid_rank']} "
            f"rerank_top100_rank={row['rerank_top100_rank']}"
        )
        best = sorted(
            row["sweep"],
            key=lambda x: x["rerank_rank"] if x["rerank_rank"] is not None else 10**9,
        )[:5]
        print("best restricted rerank settings:")
        for item in best:
            print(
                f"  candidate_k={item['candidate_k']} seed_k={item['seed_k']} "
                f"alpha={item['alpha']:.1f} rerank_rank={item['rerank_rank']} "
                f"global_rank={item['global_rank']}"
            )
        print("rerank top5:")
        for item in row["rerank_top10"][:5]:
            print(f"  {item['rank']}. {item['name']} ({item['file']}) score={item['score']:.4f}")
    print(f"\nWrote {OUT / 'ranking_report.json'}")


if __name__ == "__main__":
    main()
