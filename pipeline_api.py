"""
pipeline_api.py — Importable API wrapper over igraph_sandbox.py

This module exposes the igraph retrieval pipeline as a clean API that
tools/graph_tools.py and tools/graph_mcp_server.py can import without
triggering igraph_sandbox.py's module-level startup code.

Usage:
    from pipeline_api import initialize, search, explain_coupling

    initialize()  # call once at server startup
    results = search("memory ingestion hook processing", k=10)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[0]
SANDBOX = ROOT / "sandbox"

# ── State ─────────────────────────────────────────────────────────────────────
_initialized = False
_pipeline = None   # the igraph_sandbox module, loaded lazily


# ── Embedding helper (same model as scalable_ingest) ──────────────────────────
_EMBED_MODEL = "nvidia/llama-nemotron-embed-vl-1b-v2:free"
_EMBED_DIMS  = 2048


def _load_api_key() -> str:
    preferred = ("llm_api_key_2", "llm_api_key2", "llm_api_key")
    for p in [ROOT / ".env", Path.cwd() / ".env"]:
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


_API_KEY = _load_api_key()


def _embed(text: str) -> np.ndarray:
    if not _API_KEY or not text.strip():
        return np.zeros(_EMBED_DIMS, dtype=np.float32)
    try:
        payload = json.dumps({"model": _EMBED_MODEL, "input": text[:2000]}).encode()
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/embeddings",
            data=payload,
            headers={
                "Authorization": f"Bearer {_API_KEY}",
                "Content-Type": "application/json",
            },
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=20).read())
        return np.array(resp["data"][0]["embedding"], dtype=np.float32)
    except Exception as e:
        print(f"[pipeline_api] embed error: {e}")
        return np.zeros(_EMBED_DIMS, dtype=np.float32)


# ── Initialization ─────────────────────────────────────────────────────────────

def initialize(data_root: str | Path | None = None) -> None:
    """
    Load graph data and run Infomap. Call once at server startup.
    Safe to call multiple times — idempotent.

    data_root: directory containing sandbox/amo_nodes.json etc.
               Defaults to the graphdb repo root.
    """
    global _initialized, _pipeline

    if _initialized:
        return

    # Add sandbox to path so igraph_sandbox can resolve its imports
    sandbox_str = str(SANDBOX)
    if sandbox_str not in sys.path:
        sys.path.insert(0, sandbox_str)

    # Change cwd to repo root so igraph_sandbox.py's relative open() calls work
    target_root = Path(data_root) if data_root else ROOT
    original_cwd = os.getcwd()
    os.chdir(target_root)

    try:
        import importlib
        if "igraph_sandbox" in sys.modules:
            # Already imported — use existing module
            _pipeline = sys.modules["igraph_sandbox"]
        else:
            import igraph_sandbox as _p
            _pipeline = _p
    finally:
        os.chdir(original_cwd)

    _initialized = True
    print("[pipeline_api] initialized")


def _require_init() -> None:
    if not _initialized:
        raise RuntimeError("pipeline_api.initialize() must be called before search()")


# ── Public API ─────────────────────────────────────────────────────────────────

def search(
    prompt: str,
    k: int = 10,
    use_theme_overlay: bool = False,
    mode: str = "general_retrieval",
) -> dict:
    """
    Natural language search over AMO codebase using the igraph pipeline.

    Default: pure vector top-k (cosine similarity, no diversity penalty).
    Every function is a distinct entity — MMR is wrong here (revert from
    Exp 6 which showed 11/13 was a eval-set artifact, not a real improvement).

    use_theme_overlay=True: PPR with theme-conditioned CO_CHANGE weights.

    Returns dict with pipeline_mode field:
    {
        "pipeline_mode": "igraph" | "unavailable",
        "query": str, "consumer_mode": str,
        "nodes": [...], "edges": [...],
        "error": str  (only when unavailable)
    }
    """
    if not _initialized or _pipeline is None:
        return {
            "pipeline_mode": "unavailable",
            "error": "igraph pipeline not initialized — call initialize() first or use search_code_semantics_helix",
        }

    original_cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        query_vec = _embed(prompt)

        if use_theme_overlay:
            selected, ppr_scores, vec_scores = _pipeline.run_cold_discovery(
                query_vec, top_k_seeds=20, final_k=k, use_theme_overlay=True,
            )
        else:
            # Pure vector top-k — no diversity penalty, no random walk
            # Functions are distinct entities; MMR penalizes them incorrectly
            selected, ppr_scores, vec_scores = _pipeline.run_vector_mmr(
                query_vec, final_k=k, lam=1.0,  # lam=1.0 = pure score, no diversity
            )

        result = _pipeline.build_subgraph_output(
            selected, prompt, ppr_scores, vec_scores, mode=mode
        )
        result["pipeline_mode"] = "igraph"
        return result
    except Exception as e:
        return {"pipeline_mode": "unavailable", "error": str(e)}
    finally:
        os.chdir(original_cwd)


def explain_coupling(func_id_a: str, func_id_b: str) -> dict | None:
    """
    Return the CO_CHANGE edge data between two functions if it exists.
    Returns None if no co-change relationship exists.

    func_id_a / func_id_b: node IDs like
        "src/agent_memory_orchestrator/memory/ingest.py::ingest_hook_payload"
    """
    _require_init()

    p = _pipeline
    id_a = p.id_to_idx.get(func_id_a)
    id_b = p.id_to_idx.get(func_id_b)
    if id_a is None or id_b is None:
        return None

    # Check both directions
    for src, tgt in [(id_a, id_b), (id_b, id_a)]:
        eid = p.G.get_eid(src, tgt, directed=True, error=False)
        if eid != -1 and p.G.es[eid]["type"] == "CO_CHANGE":
            e = p.G.es[eid]
            return {
                "source": func_id_a,
                "target": func_id_b,
                "category": e["category"],
                "co_change_count": e["co_change_count"],
                "theme_proportions": e["theme_proportions"],
            }
    return None


def status() -> dict:
    """
    Return pipeline health status. Use this to verify which search mode is active.

    Returns:
    {
        "pipeline": "igraph" | "unavailable",
        "nodes": int,          # active candidate nodes loaded
        "communities": int,    # Infomap communities
        "top_k_seeds": int,    # current seed window
        "theme_overlay": bool, # whether theme embedding is available
    }

    An agent should call this if search results look wrong or unexpectedly flat.
    """
    if not _initialized or _pipeline is None:
        return {"pipeline": "unavailable", "nodes": 0, "communities": 0,
                "top_k_seeds": 0, "theme_overlay": False}

    p = _pipeline
    candidates = sum(1 for v in p.G.vs
                     if v["status"] == "active" and v["embedding"] is not None
                     and not (v["name"] or "").startswith("test_"))
    return {
        "pipeline": "igraph",
        "nodes": candidates,
        "communities": getattr(p, "INFOMAP_N_COMMUNITIES", 0),
        "top_k_seeds": 20,
        "theme_overlay": bool(getattr(p, "_themes_data", [])),
    }



def find_structural_siblings(func_id: str, k: int = 8) -> list[dict]:
    """
    Find functions that play the same architectural role as func_id.

    Uses GraphSAGE structural embeddings (128-dim), not semantic embeddings.
    Two functions are structurally similar if they have similar:
    - call depth from entry points
    - fan-out (how many things they call)
    - community membership in the CALLS graph

    This finds things vector search misses:
    - memory_write [server.py] -> memory_write [tools.py], add_memory_unit
      (the MCP -> tool -> storage chain)
    - rebuild_graph_cache -> do_GET, do_POST (same service tier)

    Returns list of {"id", "name", "file", "similarity"} sorted by similarity.
    Test functions are excluded.

    func_id: full node ID like
        "src/agent_memory_orchestrator/memory/ingest.py::ingest_hook_payload"
    """
    _require_init()

    sage_path = ROOT / "graphsage_minimal" / "out" / "graphsage_embeddings.npy"
    meta_path  = ROOT / "graphsage_minimal" / "data" / "node_meta.json"

    if not sage_path.exists() or not meta_path.exists():
        return [{"error": "GraphSAGE embeddings not found. Run graphsage_minimal/train_graphsage.py first."}]

    import json as _json
    sage_emb = np.load(sage_path)
    meta = _json.loads(meta_path.read_text(encoding="utf-8"))
    meta_by_id = {m["id"]: m for m in meta}

    if func_id not in meta_by_id:
        return [{"error": f"Function '{func_id}' not found in GraphSAGE embeddings."}]

    anchor_idx = meta_by_id[func_id]["idx"]
    anchor_vec = sage_emb[anchor_idx]
    anchor_norm = float(np.linalg.norm(anchor_vec))
    if anchor_norm < 1e-8:
        return [{"error": "Anchor has zero embedding."}]

    # Score all meta nodes
    sage_norms = np.linalg.norm(sage_emb, axis=1)
    safe_norms = np.maximum(sage_norms, 1e-8)
    scores = (sage_emb @ anchor_vec) / (safe_norms * anchor_norm)

    # Filter: exclude test functions, exclude anchor itself
    p = _pipeline
    node_filter = {}
    for m in meta:
        v_idx = p.id_to_idx.get(m["id"])
        if v_idx is None:
            continue
        v = p.G.vs[v_idx]
        name = v["name"] or ""
        file = v["file"] or ""
        file_base = file.replace("\\", "/").split("/")[-1]
        if (v["status"] == "active"
                and v["embedding"] is not None
                and not name.startswith("test_")
                and not file_base.startswith("test_")
                and m["id"] != func_id):
            node_filter[m["id"]] = m["idx"]

    ranked = sorted(
        [(float(scores[idx]), nid) for nid, idx in node_filter.items()],
        reverse=True
    )[:k]

    return [
        {
            "id": nid,
            "name": meta_by_id[nid]["name"],
            "file": meta_by_id[nid]["file"].split("/")[-1],
            "similarity": round(sim, 4),
        }
        for sim, nid in ranked
    ]

