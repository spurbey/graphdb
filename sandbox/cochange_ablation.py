"""
cochange_ablation.py — Theme-overlay ablation for co-change retrieval.

Runs each query in query_rank_eval.json through three retrieval modes and
prints a side-by-side rank comparison:

  Mode A: vector-only          (cosine top-k, no graph)
  Mode B: vector + CALLS PPR/MMR  (current baseline — no co-change)
  Mode C: vector + CALLS PPR/MMR + theme co-change overlay  (new)

Theme overlay (Mode C):
  1. Embed the query once.
  2. Score query against each theme's label text (cosine sim).
  3. For every CO_CHANGE edge: boost = sum(theme_proportions[t] * theme_score[t])
  4. Add that boost as a negative cost delta on the edge weight.
  5. Run PPR + MMR identically to Mode B.

No LLM calls.  All arithmetic is over pre-computed vectors and proportions.

Usage:
    cd graphdb
    python sandbox/cochange_ablation.py
    # or to skip embedding the themes (uses zero scores for all themes):
    AMO_SKIP_THEME_EMBED=1 python sandbox/cochange_ablation.py
"""

from __future__ import annotations

import json
import math
import os
import random as _random
import urllib.request
from pathlib import Path

import igraph as ig
import numpy as np

try:
    from sandbox.cochange_analysis import MODE_RISK, cochange_consumer_policy
except ModuleNotFoundError:
    from cochange_analysis import MODE_RISK, cochange_consumer_policy

ROOT = Path(__file__).resolve().parents[1]
EMBED_MODEL = "nvidia/llama-nemotron-embed-vl-1b-v2:free"
EMBED_DIMS = 2048
SKIP_THEME_EMBED = os.environ.get("AMO_SKIP_THEME_EMBED", "").lower() in {"1", "true", "yes"}


# ── API key ────────────────────────────────────────────────────────────────────
def _load_key() -> str:
    preferred = ("llm_api_key_2", "llm_api_key2", "llm_api_key")
    candidates = [
        Path.cwd() / ".env",
        ROOT / ".env",
        Path.cwd().parent / ".env",
        ROOT.parent / ".env",
    ]
    for p in candidates:
        if not p.exists():
            continue
        vals: dict[str, str] = {}
        for line in p.read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip().lower()] = v.strip()
        for name in preferred:
            if vals.get(name):
                return vals[name]
    return ""


API_KEY = _load_key()


def _embed_text(text: str) -> np.ndarray:
    if not API_KEY or not text.strip():
        return np.zeros(EMBED_DIMS, dtype=np.float32)
    try:
        payload = json.dumps({"model": EMBED_MODEL, "input": text[:2000]}).encode()
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/embeddings",
            data=payload,
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=20).read())
        return np.array(resp["data"][0]["embedding"], dtype=np.float32)
    except Exception as exc:
        print(f"  [embed warn] {exc}")
        return np.zeros(EMBED_DIMS, dtype=np.float32)


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


# ── Load artifacts ─────────────────────────────────────────────────────────────
print("Loading graph artifacts...")
nodes_data: list[dict] = json.loads((ROOT / "sandbox/amo_nodes.json").read_text(encoding="utf-8"))
edges_data: list[dict] = json.loads((ROOT / "sandbox/amo_edges.json").read_text(encoding="utf-8"))
themes_data: list[dict] = json.loads((ROOT / "sandbox/amo_cochange_themes.json").read_text(encoding="utf-8"))
eval_queries: list[dict] = json.loads((ROOT / "sandbox/query_rank_eval.json").read_text(encoding="utf-8"))

node_id_set = {n["id"] for n in nodes_data}
edges_data = [e for e in edges_data if e["source"] in node_id_set and e["target"] in node_id_set]
print(f"  {len(nodes_data)} nodes, {len(edges_data)} edges, {len(themes_data)} themes")


# ── Build igraph ───────────────────────────────────────────────────────────────
print("Building graph...")
G = ig.Graph(directed=True)
node_ids = [n["id"] for n in nodes_data]
G.add_vertices(len(node_ids))
id_to_idx = {nid: i for i, nid in enumerate(node_ids)}

has_nonzero = lambda v: bool(v) and any(abs(float(x)) > 1e-12 for x in v)

for i, n in enumerate(nodes_data):
    G.vs[i]["id"] = n["id"]
    G.vs[i]["file"] = n["file"]
    G.vs[i]["name"] = n["name"]
    G.vs[i]["status"] = n.get("status", "superseded")
    emb = n.get("embedding") or []
    G.vs[i]["embedding"] = np.array(emb, dtype=np.float32) if has_nonzero(emb) else None

edge_tuples = [(id_to_idx[e["source"]], id_to_idx[e["target"]]) for e in edges_data]
G.add_edges(edge_tuples)
for i, e in enumerate(edges_data):
    G.es[i]["type"] = e["type"]
    G.es[i]["co_change_count"] = e.get("co_change_count", 0)
    G.es[i]["category"] = e.get("category", "")
    G.es[i]["theme_proportions"] = e.get("theme_proportions", {})

calls_edge_ids = [e.index for e in G.es if e["type"] == "CALLS"]
calls_only = G.subgraph_edges(calls_edge_ids, delete_vertices=False)

degrees = calls_only.degree()
calls_max = max(degrees) or 1

# Phase 0a: Infomap — run once at startup with fixed seed (deterministic)
print("Running Infomap community detection (fixed seed=42)...")
_random.seed(42)
np.random.seed(42)
_communities = calls_only.community_infomap()
for _i, _c in enumerate(_communities.membership):
    G.vs[_i]["cluster_id"] = _c
INFOMAP_N_COMMUNITIES = len(set(_communities.membership))
print(f"  Infomap: {INFOMAP_N_COMMUNITIES} communities over {G.vcount()} nodes")

# Phase 0c: hub penalty exponent raised to 2.0 (was 1.5)
HUB_PENALTY_EXPONENT = 2.0

print(f"  Graph ready: {G.vcount()} vertices, {G.ecount()} edges ({len(calls_edge_ids)} CALLS)")


# ── Theme embeddings ───────────────────────────────────────────────────────────
# Build a label string per theme for cosine matching at query time.
# Use dependency_id if present, else category + commit messages snippet.
def _theme_label_text(theme: dict) -> str:
    if theme.get("dependency_id"):
        dep = theme["dependency_id"].replace("function:", "").replace("file:", "")
        return f"shared dependency {dep}"
    msgs = " ".join(m.splitlines()[0] for m in (theme.get("commit_messages") or [])[:5] if m)
    return f"{theme.get('category', 'cochange')} {msgs}".strip()[:300]


theme_ids: list[str] = [t["theme_id"] for t in themes_data]
theme_labels: list[str] = [_theme_label_text(t) for t in themes_data]
theme_vecs: list[np.ndarray] = []

if not SKIP_THEME_EMBED and API_KEY:
    print(f"Embedding {len(themes_data)} theme labels...")
    import time
    for idx, label in enumerate(theme_labels):
        vec = _embed_text(label)
        theme_vecs.append(vec)
        if (idx + 1) % 10 == 0:
            print(f"  {idx + 1}/{len(theme_labels)} theme labels embedded")
        time.sleep(0.15)
    print(f"  Done. {len(theme_vecs)} theme vectors ready.")
else:
    reason = "AMO_SKIP_THEME_EMBED=1" if SKIP_THEME_EMBED else "no API key"
    print(f"Skipping theme embedding ({reason}). Theme overlay will use zero scores.")
    theme_vecs = [np.zeros(EMBED_DIMS, dtype=np.float32) for _ in themes_data]

theme_vec_map: dict[str, np.ndarray] = dict(zip(theme_ids, theme_vecs))


# ── Weight computation ─────────────────────────────────────────────────────────
def _is_candidate(v) -> bool:
    """Phase 0b: exclude test functions and inactive/unembedded nodes from results.
    Filters by both function name prefix AND file path — catches functions named
    e.g. 'snapshot' that live in test_graph_rag.py."""
    name: str = v["name"] or ""
    file: str = v["file"] or ""
    file_base = file.replace("\\", "/").split("/")[-1]
    return (
        v["status"] == "active"
        and v["embedding"] is not None
        and not name.startswith("test_")
        and not file_base.startswith("test_")
    )


def _base_weights(include_cochange: bool = True) -> list[float]:
    """Compute static edge weights, optionally excluding CO_CHANGE edges."""
    weights = []
    for edge in G.es:
        etype = edge["type"]
        if etype == "CALLS":
            ast_cost = 1.0
        elif etype == "IMPORTS":
            ast_cost = 1.5
        else:  # CO_CHANGE
            if not include_cochange:
                weights.append(9999.0)
                continue
            policy = cochange_consumer_policy(edge["category"], MODE_RISK)
            if not policy["include"]:
                weights.append(9999.0)
                continue
            cc = edge["co_change_count"]
            temporal = 1.0 / (1.0 + cc) if cc >= 1 else 1.0
            cost = (0.4 * 2.0 + 0.6 * temporal) * policy["cost_multiplier"]
            deg = degrees[edge.target]
            if deg > calls_max * 0.05:
                cost *= math.log(deg + 1) ** HUB_PENALTY_EXPONENT
            weights.append(cost)
            continue

        cc = edge["co_change_count"]
        temporal = 1.0 / (1.0 + cc) if cc >= 1 else 1.0
        cost = 0.4 * ast_cost + 0.6 * temporal
        deg = degrees[edge.target]
        if deg > calls_max * 0.05:
            cost *= math.log(deg + 1) ** HUB_PENALTY_EXPONENT
        weights.append(cost)
    return weights


def _theme_boosted_weights(query_vec: np.ndarray) -> list[float]:
    """
    Compute theme scores for this query, then apply boost to CO_CHANGE edges.
    boost = sum(theme_proportions[t] * cosine(query, theme_vec[t]))
    The boost lowers the edge cost (makes it cheaper to traverse).
    """
    # Score query against all theme vectors once
    theme_scores: dict[str, float] = {}
    for tid, tvec in theme_vec_map.items():
        theme_scores[tid] = max(0.0, _cosine(query_vec, tvec))

    weights = []
    for edge in G.es:
        etype = edge["type"]
        if etype == "CALLS":
            ast_cost = 1.0
            cc = edge["co_change_count"]
            temporal = 1.0 / (1.0 + cc) if cc >= 1 else 1.0
            cost = 0.4 * ast_cost + 0.6 * temporal
            deg = degrees[edge.target]
            if deg > calls_max * 0.05:
                cost *= math.log(deg + 1) ** HUB_PENALTY_EXPONENT
            weights.append(cost)
        elif etype == "IMPORTS":
            ast_cost = 1.5
            cc = edge["co_change_count"]
            temporal = 1.0 / (1.0 + cc) if cc >= 1 else 1.0
            cost = 0.4 * ast_cost + 0.6 * temporal
            deg = degrees[edge.target]
            if deg > calls_max * 0.05:
                cost *= math.log(deg + 1) ** HUB_PENALTY_EXPONENT
            weights.append(cost)
        else:  # CO_CHANGE
            policy = cochange_consumer_policy(edge["category"], MODE_RISK)
            if not policy["include"]:
                weights.append(9999.0)
                continue
            cc = edge["co_change_count"]
            temporal = 1.0 / (1.0 + cc) if cc >= 1 else 1.0
            cost = (0.4 * 2.0 + 0.6 * temporal) * policy["cost_multiplier"]
            deg = degrees[edge.target]
            if deg > calls_max * 0.05:
                cost *= math.log(deg + 1) ** HUB_PENALTY_EXPONENT

            # Apply theme boost: weighted sum of query-theme alignment
            proportions: dict[str, float] = edge["theme_proportions"] or {}
            if proportions:
                boost = sum(
                    prop * theme_scores.get(tid, 0.0)
                    for tid, prop in proportions.items()
                )
                # Reduce cost by up to 60% based on theme alignment
                cost *= max(0.4, 1.0 - 0.6 * boost)

            weights.append(cost)
    return weights


# ── PPR + MMR retrieval ────────────────────────────────────────────────────────
def _run_retrieval(
    query_vec: np.ndarray,
    weights: list[float],
    top_k_seeds: int = 15,
    final_k: int = 10,
) -> list[int]:
    """
    Vector seed (test-filtered) → Infomap scope (pre-computed, deterministic)
    → PPR with soft community weighting → MMR (test-filtered results).

    Phase 0a: Infomap NOT called here — cluster_id already set at startup.
    Phase 0b: test functions excluded from seeds and results.
    Phase 1:  soft proportional reset vector, all seed communities included.
    """
    # Step 1: vector seeds — candidates only (no test functions)
    seed_scores = [
        (v.index, _cosine(query_vec, v["embedding"]))
        for v in G.vs
        if _is_candidate(v)
    ]
    seed_scores.sort(key=lambda x: x[1], reverse=True)
    top_seeds = seed_scores[:top_k_seeds]

    # Step 2: community distribution — uses pre-computed cluster_id
    cluster_counts: dict[int, int] = {}
    for idx, _ in top_seeds:
        c = G.vs[idx]["cluster_id"]
        cluster_counts[c] = cluster_counts.get(c, 0) + 1

    # Step 3: PPR with soft community weighting (Phase 1 fix)
    # Proportional weighting: community with k/total seeds gets weight k/total.
    # No community is hard-zeroed — minority communities get small but real mass.
    G.es["weight"] = weights
    total_seeds = len(top_seeds)
    community_weight = {
        cid: count / total_seeds
        for cid, count in cluster_counts.items()
    }

    # Seed floor weighting: 0.0 = pure proportional. See PIPELINE_SPEC.md 2026-07-12.
    SEED_FLOOR = 0.0
    uniform_weight = 1.0 / max(total_seeds, 1)

    reset = np.zeros(G.vcount())
    for idx, score in top_seeds:
        cid = G.vs[idx]["cluster_id"]
        w = community_weight.get(cid, 0.0)
        proportional = max(score, 0.0) * w
        floor = SEED_FLOOR * uniform_weight
        reset[idx] = max(proportional, floor)

    total = reset.sum()
    if total > 0:
        reset /= total
    else:
        for idx, _ in top_seeds:
            reset[idx] = 1.0 / max(len(top_seeds), 1)
        total2 = reset.sum()
        if total2 > 0:
            reset /= total2
        else:
            reset[:] = 1.0 / G.vcount()

    # PPR on full graph (includes CO_CHANGE edges with boosted weights in Mode C)
    ppr_scores = G.personalized_pagerank(
        vertices=None,
        damping=0.85,
        directed=True,
        weights=G.es["weight"],
        reset=reset.tolist(),
    )
    ranked = sorted(enumerate(ppr_scores), key=lambda x: x[1], reverse=True)[:60]

    # Hard cluster filter — Option A' (soft 0.1 weight) tested 2026-07-12, FAILED.
    # Caused god-node contamination, dropped CallsPPR 5->4, ThemeOverlay 6->4.
    # Reverted. export_snapshot accepted as permanent miss.
    top_cluster_set = set(cluster_counts.keys())
    ranked = [
        (i, s) for i, s in ranked
        if G.vs[i]["cluster_id"] in top_cluster_set
        and _is_candidate(G.vs[i])
    ][:30]

    # Step 4: MMR
    selected: list[int] = []
    candidates = list(ranked)
    while len(selected) < final_k and candidates:
        best_idx, best_score = None, -1e9
        for node_idx, ppr_score in candidates:
            emb = G.vs[node_idx]["embedding"]
            if selected and emb is not None:
                sim = max(
                    (_cosine(emb, G.vs[s]["embedding"]) for s in selected if G.vs[s]["embedding"] is not None),
                    default=0.0,
                )
            else:
                sim = 0.0
            mmr = 0.6 * ppr_score - 0.4 * sim
            if mmr > best_score:
                best_score, best_idx = mmr, node_idx
        if best_idx is None:
            break
        selected.append(best_idx)
        candidates = [(i, s) for i, s in candidates if i != best_idx]

    return selected


def _vector_only(query_vec: np.ndarray, final_k: int = 10) -> list[int]:
    # Phase 0b: test functions excluded
    scores = [
        (v.index, _cosine(query_vec, v["embedding"]))
        for v in G.vs
        if _is_candidate(v)
    ]
    scores.sort(key=lambda x: x[1], reverse=True)
    return [idx for idx, _ in scores[:final_k]]


# ── Evaluate one query across three modes ─────────────────────────────────────
def _rank_of_target(node_indices: list[int], target_ids: set[str]) -> int | None:
    for rank, idx in enumerate(node_indices, 1):
        if G.vs[idx]["id"] in target_ids:
            return rank
    return None


def _node_name(idx: int) -> str:
    v = G.vs[idx]
    return f"{v['name']} [{v['file'].split('/')[-1]}]"


static_weights_with_cochange = _base_weights(include_cochange=True)
static_weights_no_cochange = _base_weights(include_cochange=False)


def evaluate_query(q: dict) -> dict:
    query_text = q["query"]
    target_ids = set(q.get("targets", []))
    target_name = q.get("target_name", "?")

    print(f"\n  Query: {repr(query_text)}")
    print(f"  Target: {target_name}")

    query_vec = _embed_text(query_text)

    # Mode A: vector only
    results_a = _vector_only(query_vec)
    rank_a = _rank_of_target(results_a, target_ids)

    # Mode B: vector + CALLS PPR/MMR (no co-change) — k=20 (tested, zero regressions)
    results_b = _run_retrieval(query_vec, static_weights_no_cochange, top_k_seeds=20)
    rank_b = _rank_of_target(results_b, target_ids)

    # Mode C: vector + CALLS PPR/MMR + theme co-change overlay — k=15
    # k=20 causes 2 regressions in theme overlay mode (old_ingest, rebuild_indexes).
    # Kept at k=15 until theme overlay regression is investigated.
    boosted_weights = _theme_boosted_weights(query_vec)
    results_c = _run_retrieval(query_vec, boosted_weights, top_k_seeds=15)
    rank_c = _rank_of_target(results_c, target_ids)

    return {
        "id": q["id"],
        "query": query_text,
        "target_name": target_name,
        "rank_vector_only": rank_a,
        "rank_calls_ppr": rank_b,
        "rank_theme_overlay": rank_c,
        "top10_vector": [_node_name(i) for i in results_a],
        "top10_calls_ppr": [_node_name(i) for i in results_b],
        "top10_theme_overlay": [_node_name(i) for i in results_c],
    }


# ── Main ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("CO-CHANGE THEME OVERLAY ABLATION")
    print("=" * 70)
    print(f"Queries: {len(eval_queries)}  |  Themes: {len(themes_data)}")
    print(f"Theme embedding: {'SKIPPED (zeros)' if SKIP_THEME_EMBED or not API_KEY else 'ACTIVE'}")
    print()

    results = []
    for q in eval_queries:
        results.append(evaluate_query(q))

    # ── Summary table ──────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print(f"{'Query ID':<30} {'Target':<30} {'VecOnly':>7} {'CallsPPR':>8} {'ThemeOvl':>8}")
    print("-" * 70)

    def _rank_str(r: int | None) -> str:
        return str(r) if r is not None else "MISS"

    hit_a = hit_b = hit_c = 0
    for r in results:
        ra, rb, rc = r["rank_vector_only"], r["rank_calls_ppr"], r["rank_theme_overlay"]
        if ra is not None:
            hit_a += 1
        if rb is not None:
            hit_b += 1
        if rc is not None:
            hit_c += 1
        improvement = ""
        if rc is not None and (rb is None or rc < rb):
            improvement = " ^"
        elif rc is None and rb is not None:
            improvement = " v"
        print(
            f"{r['id']:<30} {r['target_name']:<30} "
            f"{_rank_str(ra):>7} {_rank_str(rb):>8} {_rank_str(rc):>8}{improvement}"
        )

    n = len(results)
    print("-" * 70)
    print(
        f"{'HIT@10':<30} {'':<30} "
        f"{hit_a}/{n:>4}  {hit_b}/{n:>5}  {hit_c}/{n:>5}"
    )
    print()
    print("^ = theme overlay improved rank   v = theme overlay lost a hit")

    # ── Detailed top-10 per query ──────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("DETAILED TOP-10 PER QUERY")
    print("=" * 70)
    for r in results:
        print(f"\n[{r['id']}] {repr(r['query'])}")
        print(f"  Target: {r['target_name']}")
        print(f"  Ranks - vector: {_rank_str(r['rank_vector_only'])}  "
              f"calls_ppr: {_rank_str(r['rank_calls_ppr'])}  "
              f"theme_overlay: {_rank_str(r['rank_theme_overlay'])}")
        cols = [
            ("VectorOnly", r["top10_vector"]),
            ("CallsPPR", r["top10_calls_ppr"]),
            ("ThemeOverlay", r["top10_theme_overlay"]),
        ]
        for label, top10 in cols:
            print(f"\n  {label}:")
            for rank, name in enumerate(top10, 1):
                print(f"    {rank:2d}. {name}")

    # ── Save results ───────────────────────────────────────────────────────────
    out_path = ROOT / "sandbox" / "ablation_results.json"
    out_data = {
        "summary": {
            "n_queries": n,
            "hit_at_10_vector_only": hit_a,
            "hit_at_10_calls_ppr": hit_b,
            "hit_at_10_theme_overlay": hit_c,
            "theme_embedding_active": not (SKIP_THEME_EMBED or not API_KEY),
        },
        "per_query": results,
    }
    out_path.write_text(json.dumps(out_data, indent=2), encoding="utf-8")
    print(f"\nResults saved: {out_path}")
