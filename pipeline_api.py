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

    Always returns a dict with a "pipeline_mode" field so callers can tell
    which path was taken:
    {
        "pipeline_mode": "igraph" | "unavailable",
        "query": str,
        "consumer_mode": str,
        "nodes": [...],   # present when pipeline_mode == "igraph"
        "edges": [...],   # present when pipeline_mode == "igraph"
        "error": str,     # present when pipeline_mode == "unavailable"
    }

    If pipeline is unavailable (amo_nodes.json missing, igraph not installed,
    etc.) returns {"pipeline_mode": "unavailable", "error": "..."} so the
    caller can fall back to HelixDB search rather than silently getting wrong
    results.
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
        selected, ppr_scores, vec_scores = _pipeline.run_cold_discovery(
            query_vec,
            top_k_seeds=20,
            final_k=k,
            use_theme_overlay=use_theme_overlay,
        )
        result = _pipeline.build_subgraph_output(
            selected, prompt, ppr_scores, vec_scores, mode=mode
        )
        result["pipeline_mode"] = "igraph"
        return result
    except Exception as e:
        return {
            "pipeline_mode": "unavailable",
            "error": str(e),
        }
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
