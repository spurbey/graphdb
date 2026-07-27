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
import random as _random
from pathlib import Path
import numpy as np
import igraph as ig

try:
    from sandbox.cochange_analysis import MODE_RISK
    from sandbox.cochange_analysis import cochange_consumer_policy
except ModuleNotFoundError:
    from cochange_analysis import MODE_RISK
    from cochange_analysis import cochange_consumer_policy

ROOT = Path(__file__).resolve().parents[1]
QUERY_FEATURE_MEAN: np.ndarray | None = None
EMBEDDING_SPACE = "raw_openrouter"

# ── Graph state (set by load_graph below) ─────────────────────────────────────
G = None
id_to_idx: dict[str, int] = {}
nodes_data: list[dict] = []
edges_data: list[dict] = []
_calls_degrees: list[int] = []
_calls_max: int = 1
_calls_only_graph = None


def has_nonzero_embedding(values) -> bool:
    return bool(values) and any(abs(float(value)) > 1e-12 for value in values)


def load_graphsage_feature_fallback(nodes: list[dict]) -> tuple[dict[str, np.ndarray], np.ndarray | None, str]:
    active = sorted([node for node in nodes if node.get("status") == "active"], key=lambda node: node["id"])
    if any(has_nonzero_embedding(node.get("embedding") or []) for node in active):
        return {}, None, "raw_openrouter"

    data_path = ROOT / "graphsage_minimal" / "data" / "amo_calls_active.npz"
    meta_path = ROOT / "graphsage_minimal" / "data" / "node_meta.json"
    if not data_path.exists() or not meta_path.exists():
        return {}, None, "missing"

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta_ids = [row["id"] for row in meta]
    active_ids = [node["id"] for node in active]
    if meta_ids != active_ids:
        return {}, None, "id_mismatch"

    data = np.load(data_path)
    x = data["x"].astype(np.float32)
    feature_mean = data["feature_mean"].astype(np.float32)
    return {node_id: x[idx] for idx, node_id in enumerate(meta_ids)}, feature_mean, "graphsage_centered"


def load_graph(source: str = "files", data_root: str | Path | None = None,
               helix_url: str = "http://127.0.0.1:6969",
               payload_path: str | Path | None = None) -> bool:
    """
    Load graph data and run Infomap. Call once at server startup.
    
    source:
      "files"    — read sandbox/amo_nodes.json + sandbox/amo_edges.json
      "helixdb"  — query HelixDB for FunctionIdentity+FunctionState,
                   read graph_payload.json for edges
    
    Returns True on success, False on failure.
    """
    global G, id_to_idx, nodes_data, edges_data
    global QUERY_FEATURE_MEAN, EMBEDDING_SPACE

    if source == "helixdb":
        ok = _load_from_helixdb(helix_url, payload_path or (Path.cwd() / "graph_payload.json"))
    else:
        ok = _load_from_files(data_root or ROOT)

    if not ok:
        return False

    _build_igraph()
    _run_infomap()
    _compute_weights()
    return True


def _load_from_files(data_root: str | Path) -> bool:
    global nodes_data, edges_data
    sandbox_dir = Path(data_root) / "sandbox"
    nodes_path = sandbox_dir / "amo_nodes.json"
    edges_path = sandbox_dir / "amo_edges.json"

    if not nodes_path.exists() or not edges_path.exists():
        print(f"  [igraph_sandbox] Data files not found in {sandbox_dir}")
        return False

    print("Loading graph data from files...")
    with open(nodes_path, encoding="utf-8") as f:
        nodes_data = json.load(f)
    with open(edges_path, encoding="utf-8") as f:
        edges_data = json.load(f)
    print(f"  {len(nodes_data)} nodes, {len(edges_data)} edges")
    return True


def _load_from_helixdb(helix_url: str, payload_path: str | Path) -> bool:
    global nodes_data, edges_data

    from helixdb import Client, g, read_batch, Projection

    pp = Path(payload_path)

    # ── Query FunctionIdentity from HelixDB (the canonical source) ────────
    helix_rows = []
    helix_ok = True
    try:
        c = Client(helix_url)
        fi_batch = (
            read_batch()
            .var_as("fns",
                g().n_with_label("FunctionIdentity")
                   .limit(10000)
                   .project([
                       Projection.property("node_id"),
                       Projection.property("name"),
                       Projection.property("file"),
                       Projection.property("code_vec"),
                   ])
            )
            .returning(["fns"])
        )
        fi_result = c.query().dynamic(fi_batch.to_dynamic_request()).send()
        helix_rows = fi_result.get("fns", {}).get("properties", [])
    except Exception as e:
        print(f"  HelixDB query failed ({e}), trying graph_payload.json fallback")
        helix_ok = False

    # ── Build nodes_data ──────────────────────────────────────────────────
    nodes_data = []
    if helix_ok and helix_rows:
        for row in helix_rows:
            fid = row.get("node_id", "")
            vec = row.get("code_vec", [])
            if isinstance(vec, str):
                try:
                    vec = json.loads(vec)
                except Exception:
                    vec = []
            valid = (isinstance(vec, list) and len(vec) == 384
                     and any(abs(v) > 1e-12 for v in vec))
            nodes_data.append({
                "id": fid,
                "file": row.get("file", ""),
                "name": row.get("name", ""),
                "code": "",
                "text_summary": "",
                "embedding": vec if valid else [],
                "status": "active",
            })
        print(f"  HelixDB: {len(nodes_data)} FunctionIdentity (code_vec embeddings)")
    elif pp.exists():
        with open(pp, encoding="utf-8") as f:
            payload = json.load(f)
        for n in payload.get("nodes", []):
            if n.get("type") != "FunctionIdentity":
                continue
            vec = n.get("code_vec", [])
            if isinstance(vec, str):
                try:
                    vec = json.loads(vec)
                except Exception:
                    vec = []
            valid = (isinstance(vec, list) and len(vec) == 384
                     and any(abs(v) > 1e-12 for v in vec))
            nodes_data.append({
                "id": n.get("id", ""),
                "file": n.get("file", ""),
                "name": n.get("name", ""),
                "code": n.get("code", ""),
                "text_summary": n.get("text_summary", ""),
                "embedding": vec if valid else [],
                "status": "active",
            })
        print(f"  graph_payload.json: {len(nodes_data)} nodes (HelixDB fallback)")
    else:
        print(f"  No HelixDB and no {pp} — no node data")
        return False

    # ── Drop nodes without valid embeddings ───────────────────────────────
    before = len(nodes_data)
    nodes_data = [n for n in nodes_data if has_nonzero_embedding(n.get("embedding", []))]
    dropped = before - len(nodes_data)
    if dropped:
        print(f"  Dropped {dropped} nodes without code_vec embeddings")

    # ── Build edges_data from graph_payload.json ──────────────────────────
    payload_edges = []
    if pp.exists():
        with open(pp, encoding="utf-8") as f:
            payload_data = json.load(f)
        payload_edges = payload_data.get("edges", [])

    # Infer repo prefix from first edge's from field and filter stale nodes
    repo_prefix = ""
    for raw in payload_edges:
        src = raw.get("from", "")
        if ":" in src:
            repo_prefix = src.split(":")[0] + ":"
            break
    if repo_prefix:
        before = len(nodes_data)
        nodes_data = [n for n in nodes_data if n["id"].startswith(repo_prefix)]
        dropped = before - len(nodes_data)
        if dropped:
            print(f"  Dropped {dropped} nodes from other repos (prefix={repo_prefix})")

    node_id_set = {n["id"] for n in nodes_data}
    edges_data = []
    for raw in payload_edges:
        label = raw.get("label", "")
        if label not in ("CALLS", "IMPORTS", "INHERITS"):
            continue
        src = raw.get("from", "")
        tgt = raw.get("to", "")
        if src in node_id_set and tgt in node_id_set:
            edges_data.append({
                "source": src,
                "target": tgt,
                "type": label,
                "co_change_count": 0,
                "category": "",
                "ast_relation_type": "unknown",
                "theme_proportions": {},
                })

    # ── Load CO_CHANGE edges from cochange_pairs.json ──────────────────────
    if not repo_prefix:
        for n in nodes_data:
            nid = n.get("id", "")
            if ":" in nid:
                repo_prefix = nid.split(":")[0] + ":"
                break
    repo_name = repo_prefix.rstrip(":")
    cochange_file = Path(__file__).resolve().parent / f"{repo_name}_cochange_pairs.json"
    if cochange_file.exists():
        with open(cochange_file, encoding="utf-8") as f:
            cochange_pairs = json.load(f)
        co_added = 0
        for pair in cochange_pairs:
            src = pair.get("source", "") or pair.get("func_a", "")
            tgt = pair.get("target", "") or pair.get("func_b", "")
            if src in node_id_set and tgt in node_id_set:
                edges_data.append({
                    "source": src,
                    "target": tgt,
                    "type": "CO_CHANGE",
                    "co_change_count": pair.get("co_change_count", pair.get("count", 1)),
                    "category": pair.get("category", ""),
                    "ast_relation_type": "co_change",
                    "theme_proportions": pair.get("theme_proportions", {}),
                })
                co_added += 1
        print(f"  CO_CHANGE: {co_added} edges loaded from {cochange_file.name}")
    else:
        print(f"  No CO_CHANGE file at {cochange_file.name} — skipping")

    has_vec = sum(1 for n in nodes_data if has_nonzero_embedding(n.get("embedding", [])))
    print(f"  Nodes: {len(nodes_data)} ({has_vec} with code_vec), Edges: {len(edges_data)}")
    return True


def _build_igraph():
    global G, id_to_idx, nodes_data, edges_data
    global QUERY_FEATURE_MEAN, EMBEDDING_SPACE

    node_id_set = {n["id"] for n in nodes_data}
    edges_data[:] = [e for e in edges_data if e["source"] in node_id_set and e["target"] in node_id_set]

    print("Building igraph instance...")
    G = ig.Graph(directed=True)
    node_ids = [n["id"] for n in nodes_data]
    G.add_vertices(len(node_ids))
    id_to_idx = {nid: i for i, nid in enumerate(node_ids)}

    reused_features, QUERY_FEATURE_MEAN, EMBEDDING_SPACE = load_graphsage_feature_fallback(nodes_data)
    if EMBEDDING_SPACE == "graphsage_centered":
        print(f"  Reusing GraphSAGE feature matrix for {len(reused_features)} active embeddings")
    elif EMBEDDING_SPACE != "raw_openrouter":
        print(f"  No reusable GraphSAGE feature fallback: {EMBEDDING_SPACE}")

    for i, n in enumerate(nodes_data):
        G.vs[i]["id"]           = n["id"]
        G.vs[i]["file"]         = n["file"]
        G.vs[i]["name"]         = n["name"]
        G.vs[i]["text_summary"] = n.get("text_summary", "")
        G.vs[i]["status"]       = n.get("status", "superseded")
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
        G.es[i]["type"]             = e["type"]
        G.es[i]["co_change_count"]  = e.get("co_change_count", 0)
        G.es[i]["category"]         = e.get("category", "")
        G.es[i]["ast_relation_type"] = e.get("ast_relation_type", "unknown")
        G.es[i]["theme_proportions"] = e.get("theme_proportions", {})

    print(f"  Graph: {G.vcount()} vertices, {G.ecount()} edges")


def _run_infomap():
    print("Running Infomap community detection (fixed seed=42)...")
    _random.seed(42)
    np.random.seed(42)
    calls_edge_ids = [e.index for e in G.es if e["type"] == "CALLS"]
    calls_only = G.subgraph_edges(calls_edge_ids, delete_vertices=False)
    _communities = calls_only.community_infomap()
    for _i, _c in enumerate(_communities.membership):
        G.vs[_i]["cluster_id"] = _c
    INFOMAP_N_COMMUNITIES = len(set(_communities.membership))
    print(f"  Infomap: {INFOMAP_N_COMMUNITIES} communities over {G.vcount()} nodes")


def _compute_weights():
    global _calls_degrees, _calls_max
    print("Computing query-time edge weights...")
    degrees = np.array(G.degree())
    calls_edge_ids = [e.index for e in G.es if e["type"] == "CALLS"]
    calls_only = G.subgraph_edges(calls_edge_ids, delete_vertices=False)
    _calls_degrees = calls_only.degree()
    _calls_max = max(_calls_degrees) or 1
    _HUB_PENALTY = 2.0

    def compute_weights(alpha=0.4, beta=0.6, co_change_floor=1, cochange_mode=MODE_RISK):
        weights = []
        for edge in G.es:
            etype = edge["type"]
            cochange_policy = None

            if etype == "CALLS":
                ast_cost = 1.0
            elif etype == "IMPORTS":
                ast_cost = 1.5
            else:
                cochange_policy = cochange_consumer_policy(edge["category"], cochange_mode)
                if not cochange_policy["include"]:
                    weights.append(9999.0)
                    continue
                ast_cost = 2.0

            cc = edge["co_change_count"]
            temporal_cost = 1.0 / (1.0 + cc) if cc >= co_change_floor else 1.0
            base_cost = (alpha * ast_cost) + (beta * temporal_cost)
            if cochange_policy is not None:
                base_cost *= cochange_policy["cost_multiplier"]

            calls_deg = _calls_degrees[edge.target]
            if calls_deg > _calls_max * 0.05:
                base_cost *= math.log(calls_deg + 1) **_HUB_PENALTY
            weights.append(base_cost)
        return weights

    G.es["weight"] = compute_weights()
    print(f"  Weights computed. Range: [{min(G.es['weight']):.3f}, {max(G.es['weight']):.3f}]")



# ── Theme overlay setup ───────────────────────────────────────────────────────
# Load co-change themes. Build label text per theme for query-time cosine scoring.
# theme_vec_map is populated lazily on first query that uses theme overlay.

_themes_data: list[dict] = []
_theme_ids: list[str] = []
_theme_labels: list[str] = []
_theme_vecs: list[np.ndarray] = []
_theme_vec_map: dict[str, np.ndarray] = {}
_themes_embedded = False


def _embed_text(text: str) -> np.ndarray:
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("all-MiniLM-L6-v2")
    vecs = model.encode([text], show_progress_bar=False, normalize_embeddings=True)
    return np.array(vecs[0], dtype=np.float32)


def _theme_label_text(theme: dict) -> str:
    if theme.get("dependency_id"):
        dep = theme["dependency_id"].replace("function:", "").replace("file:", "")
        return f"shared dependency {dep}"
    msgs = " ".join(
        m.splitlines()[0] for m in (theme.get("commit_messages") or [])[:5] if m
    )
    return f"{theme.get('category', 'cochange')} {msgs}".strip()[:300]


def _ensure_theme_vecs() -> None:
    global _theme_vecs, _theme_vec_map, _themes_embedded
    if _themes_embedded:
        return
    if not _themes_data:
        _themes_embedded = True
        return
    from sentence_transformers import SentenceTransformer
    print(f"  Embedding {len(_themes_data)} theme labels...")
    model = SentenceTransformer("all-MiniLM-L6-v2")
    vecs = model.encode(_theme_labels, show_progress_bar=False, normalize_embeddings=True)
    _theme_vecs = [np.array(v, dtype=np.float32) for v in vecs]
    _theme_vec_map = dict(zip(_theme_ids, _theme_vecs))
    _themes_embedded = True

def _theme_boosted_weights(query_vec: np.ndarray) -> list[float]:
    global _calls_degrees, _calls_max
    _ensure_theme_vecs()
    theme_scores: dict[str, float] = {
        tid: max(0.0, cosine_sim(query_vec, tvec))
        for tid, tvec in _theme_vec_map.items()
    }
    weights = []
    for edge in G.es:
        etype = edge["type"]
        cc  = edge["co_change_count"]
        deg = _calls_degrees[edge.target]

        if etype == "CALLS":
            cost = 0.4 * 1.0 + 0.6 * (1.0 / (1.0 + cc) if cc >= 1 else 1.0)
        elif etype == "IMPORTS":
            cost = 0.4 * 1.5 + 0.6 * (1.0 / (1.0 + cc) if cc >= 1 else 1.0)
        else:
            policy = cochange_consumer_policy(edge["category"], MODE_RISK)
            if not policy["include"]:
                weights.append(9999.0)
                continue
            temporal = 1.0 / (1.0 + cc) if cc >= 1 else 1.0
            cost = (0.4 * 2.0 + 0.6 * temporal) * policy["cost_multiplier"]
            proportions: dict[str, float] = edge["theme_proportions"] or {}
            if proportions:
                boost = sum(
                    prop * theme_scores.get(tid, 0.0)
                    for tid, prop in proportions.items()
                )
                cost *= max(0.4, 1.0 - 0.6 * boost)

        if deg > _calls_max * 0.05:
            cost *= math.log(deg + 1) ** 2.0
        weights.append(cost)
    return weights


# ── Phase 3: Cold Discovery Pipeline ─────────────────────────────────────────

def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


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


def run_cold_discovery(query_embedding: np.ndarray, top_k_seeds=20, final_k=10,
                       use_theme_overlay=False):
    """
    Step 1: Vector seed search (cosine similarity) — test functions excluded
    Step 2: Infomap community scope (pre-computed at startup, deterministic)
    Step 3: Personalized PageRank with soft community weighting
    Step 4: MMR diverse selection

    use_theme_overlay=True  → PPR on full graph with theme-boosted CO_CHANGE weights
    use_theme_overlay=False → PPR on CALLS-only graph (faster, no API calls)
    """
    # Step 1: Vector seed search — candidates only (no test functions)
    seed_scores = []
    for v in G.vs:
        if _is_candidate(v):
            sim = cosine_sim(query_embedding, v["embedding"])
            seed_scores.append((v.index, sim))

    seed_scores.sort(key=lambda x: x[1], reverse=True)
    top_seeds = seed_scores[:top_k_seeds]

    print(f"\n  Top {top_k_seeds} vector seeds:")
    for idx, score in top_seeds[:5]:
        v = G.vs[idx]
        print(f"    [{score:.3f}] {v['name']} ({v['file'].split('/')[-1]})")

    # Step 2: Community distribution of seeds (cluster_id already set at startup)
    seed_clusters = [G.vs[idx]["cluster_id"] for idx, _ in top_seeds]
    cluster_counts: dict[int, int] = {}
    for c in seed_clusters:
        cluster_counts[c] = cluster_counts.get(c, 0) + 1
    print(f"  Seed cluster distribution: {dict(list(cluster_counts.items())[:5])} ({len(cluster_counts)} clusters)")

    # Step 3: Personalized PageRank with soft community weighting
    total_seeds = len(top_seeds)
    community_weight = {
        cid: count / total_seeds
        for cid, count in cluster_counts.items()
    }

    # Seed floor weighting: SEED_FLOOR=0.0 means pure proportional weighting (current behavior).
    # Raising SEED_FLOOR gives every seed a minimum reset mass regardless of cosine score.
    # Tested 2026-07-12 with SEED_FLOOR=0.3: no effect on export_snapshot (cluster filter
    # removes it regardless) or _filter_answer_grade_nodes (proportional already > floor).
    # Left at 0.0 until a miss is specifically diagnosed where floor would activate.
    # See PIPELINE_SPEC.md Result Log 2026-07-12 for full diagnosis.
    SEED_FLOOR = 0.0
    uniform_weight = 1.0 / len(top_seeds)

    reset_vector = np.zeros(G.vcount())
    for idx, score in top_seeds:
        cid = G.vs[idx]["cluster_id"]
        w = community_weight.get(cid, 0.0)
        proportional = max(score, 0.0) * w
        floor = SEED_FLOOR * uniform_weight
        reset_vector[idx] = max(proportional, floor)

    total = reset_vector.sum()
    if total > 0:
        reset_vector /= total
    else:
        for idx, _ in top_seeds:
            reset_vector[idx] = 1.0 / len(top_seeds)

    # Choose graph and weights based on mode
    if use_theme_overlay:
        G.es["weight"] = _theme_boosted_weights(query_embedding)
        ppr_graph = G  # full graph including CO_CHANGE with boosted weights
    else:
        # Build CALLS-only subgraph (lazy, cached)
        global _calls_only_graph
        if _calls_only_graph is None or _calls_only_graph.vcount() != G.vcount():
            calls_edge_ids = [e.index for e in G.es if e["type"] == "CALLS"]
            _calls_only_graph = G.subgraph_edges(calls_edge_ids, delete_vertices=False)
        ppr_graph = _calls_only_graph

    ppr_scores = ppr_graph.personalized_pagerank(
        vertices=None,
        damping=0.85,
        directed=True,
        weights=G.es["weight"] if use_theme_overlay else None,
        reset=reset_vector.tolist(),
    )

    ranked_ppr = sorted(enumerate(ppr_scores), key=lambda x: x[1], reverse=True)[:60]

    # Include all clusters that had at least 1 seed (Phase 1 fix: no hard top-3 cutoff)
    # Hard cluster filter confirmed necessary (2026-07-12): softening to 0.1 weight
    # caused god-node contamination (config/util functions flooding results) and
    # regressed old_context and rebuild_indexes. Score dropped 5->4 and 6->4.
    # export_snapshot remains a permanent miss — its cluster has no seeds for this query.
    # See PIPELINE_SPEC.md Result Log for full diagnosis.
    top_cluster_set = set(cluster_counts.keys())
    ranked_ppr = [
        (idx, score) for idx, score in ranked_ppr
        if G.vs[idx]["cluster_id"] in top_cluster_set
        and _is_candidate(G.vs[idx])
    ][:30]

    # Step 4: MMR diverse selection
    lam = 0.6   # balance: higher = more PPR weight, lower = more diversity
    selected: list[int] = []
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
                sims = [
                    cosine_sim(emb, G.vs[sel_idx]["embedding"])
                    for sel_idx in selected
                    if G.vs[sel_idx]["embedding"] is not None
                ]
                sim_to_selected = max(sims) if sims else 0.0

            mmr_score = lam * ppr_score - (1 - lam) * sim_to_selected
            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = node_idx

        if best_idx is None:
            break
        selected.append(best_idx)
        candidates = [(i, s) for i, s in candidates if i != best_idx]

    # Build vector score lookup for subgraph output
    vec_score_map = {idx: score for idx, score in seed_scores}

    return selected, ppr_scores, vec_score_map


def run_vector_mmr(query_embedding: np.ndarray, final_k: int = 10, lam: float = 0.8):
    """
    Experiment 6 winner: vector search + MMR at lambda=0.8.
    Scores 11/13 HIT vs 10/13 for vector alone or PPR.

    lambda=0.8: 80% score-weighted, 20% diversity penalty.
    Acts as soft tiebreaker — keeps high-scoring functions even when similar
    to already-selected, but breaks ties toward diversity.

    Returns (selected_indices, None, vec_score_map) — same tuple shape as
    run_cold_discovery for compatibility with build_subgraph_output.
    """
    # Score all candidates by cosine similarity
    seed_scores = []
    for v in G.vs:
        if _is_candidate(v):
            sim = cosine_sim(query_embedding, v["embedding"])
            seed_scores.append((v.index, sim))
    seed_scores.sort(key=lambda x: x[1], reverse=True)

    # MMR selection from top-k*3 candidates
    candidates_pool = [(idx, score) for idx, score in seed_scores[:final_k * 3]]
    selected: list[int] = []
    remaining = list(candidates_pool)

    while len(selected) < final_k and remaining:
        best_idx, best_score = None, -1e9
        for node_idx, base_score in remaining:
            emb = G.vs[node_idx]["embedding"]
            if emb is None or not selected:
                sim_sel = 0.0
            else:
                sims = [
                    cosine_sim(emb, G.vs[s]["embedding"])
                    for s in selected
                    if G.vs[s]["embedding"] is not None
                ]
                sim_sel = max(sims) if sims else 0.0
            mmr = lam * base_score - (1 - lam) * sim_sel
            if mmr > best_score:
                best_score, best_idx = mmr, node_idx
        if best_idx is None:
            break
        selected.append(best_idx)
        remaining = [(i, s) for i, s in remaining if i != best_idx]

    vec_score_map = {idx: score for idx, score in seed_scores}
    return selected, None, vec_score_map


# ── Phase 3: Subgraph output ──────────────────────────────────────────────────

def build_subgraph_output(
    selected_indices: list[int],
    query: str,
    ppr_scores: list[float] | None = None,
    vector_scores: dict[int, float] | None = None,
    mode: str = "general_retrieval",
) -> dict:
    """
    Given MMR-selected node indices, return a structured subgraph:
      - nodes: id, name, file, summary, code, ppr_score, vector_score, community_id
      - edges: all edges between selected nodes, filtered by consumer policy mode

    mode mirrors cochange_consumer_policy modes:
      general_retrieval / risk / pre_edit / why_coupled
    temporal_burst CO_CHANGE edges are excluded in general_retrieval and risk modes
    structural_redundant edges are excluded in why_coupled mode.
    """
    selected_set = set(selected_indices)

    # Build nodes
    node_lookup = {n["id"]: n for n in nodes_data}
    nodes_out = []
    for idx in selected_indices:
        v = G.vs[idx]
        nd = node_lookup.get(v["id"], {})
        nodes_out.append({
            "id":           v["id"],
            "name":         v["name"],
            "file":         v["file"],
            "summary":      nd.get("text_summary", ""),
            "code":         nd.get("code", ""),
            "ppr_score":    float(ppr_scores[idx]) if ppr_scores else None,
            "vector_score": float(vector_scores[idx]) if vector_scores and idx in vector_scores else None,
            "community_id": G.vs[idx]["cluster_id"],
        })

    # Build edges — only between selected nodes, filtered by consumer policy
    edges_out = []
    for edge in G.es:
        if edge.source not in selected_set or edge.target not in selected_set:
            continue
        etype = edge["type"]
        if etype == "CO_CHANGE":
            policy = cochange_consumer_policy(edge["category"], mode)
            if not policy["include"]:
                continue
        edges_out.append({
            "source":              G.vs[edge.source]["id"],
            "target":              G.vs[edge.target]["id"],
            "type":                etype,
            "co_change_category":  edge["category"] if etype == "CO_CHANGE" else "",
            "co_change_count":     edge["co_change_count"],
        })

    return {
        "query":        query,
        "consumer_mode": mode,
        "nodes":        nodes_out,
        "edges":        edges_out,
    }


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
    """Embed a query string using local sentence-transformers (384-dim)."""
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("all-MiniLM-L6-v2")
    vecs = model.encode([query_text], show_progress_bar=False, normalize_embeddings=True)
    return np.array(vecs[0], dtype=np.float32)


def run_validation():
    """
    Run 3 ground-truth validation queries.
    Each query has a known target function — pass if it appears in top-10.
    For the first passing query, also write a subgraph JSON to sandbox/out/.
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

    out_dir = ROOT / "sandbox" / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    passed = 0
    for test in tests:
        query  = test["query"]
        t_name = test["target_name"]
        t_hint = test["target_hint"]

        print(f"\nQuery: {repr(query)}")
        print(f"Target: function named ~'{t_name}' in a file containing '{t_hint}'")

        vec = load_query_embedding(query)
        selected, ppr_scores, vec_scores = run_cold_discovery(vec, top_k_seeds=20, final_k=10)

        found = False
        print(f"  Top-10 results:")
        for rank, node_idx in enumerate(selected):
            v    = G.vs[node_idx]
            name = v["name"]
            file = v["file"].split("/")[-1]
            ppr  = ppr_scores[node_idx] if ppr_scores else 0.0
            vscore = vec_scores.get(node_idx, 0.0)
            print(f"    {rank+1}. {name} ({file})  ppr={ppr:.5f} vec={vscore:.3f}")
            if name == t_name and t_hint.lower() in v["file"].lower():
                found = True

        status = "PASS" if found else "MISS"
        print(f"  Result: {status}")
        if found:
            passed += 1
            # Write subgraph JSON for this query
            subgraph = build_subgraph_output(
                selected, query, ppr_scores, vec_scores, mode="general_retrieval"
            )
            slug = query.replace(" ", "_")[:40]
            out_path = out_dir / f"subgraph_{slug}.json"
            out_path.write_text(json.dumps(subgraph, indent=2), encoding="utf-8")
            print(f"  Subgraph written: {out_path.name}  "
                  f"({len(subgraph['nodes'])} nodes, {len(subgraph['edges'])} edges)")

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
