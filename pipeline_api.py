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
_betweenness: dict[str, float] = {}   # node_id -> betweenness score (computed once)
_GENERIC_DEGREE_THRESHOLD = 200       # exclude high-degree generic utilities (get, append, close)


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

    # Compute betweenness centrality on CALLS graph (once at startup)
    if not _betweenness:
        _compute_betweenness()


def _require_init() -> None:
    if not _initialized:
        raise RuntimeError("pipeline_api.initialize() must be called before search()")


def _compute_betweenness() -> None:
    """
    Compute betweenness centrality on the CALLS-only graph once at startup.
    Filters out generic high-degree utilities (get, append, close) that dominate
    degree but are not meaningful architectural hubs.
    Stores results in _betweenness dict: node_id -> normalized score [0,1].
    """
    global _betweenness
    if not _pipeline:
        return

    p = _pipeline
    try:
        # Build CALLS-only subgraph
        calls_ids = [e.index for e in p.G.es if e["type"] == "CALLS"]
        calls_only = p.G.subgraph_edges(calls_ids, delete_vertices=False)

        print("[pipeline_api] computing betweenness centrality...")
        raw_b = calls_only.betweenness(directed=True)
        degree_total = [calls_only.indegree()[i] + calls_only.outdegree()[i]
                        for i in range(p.G.vcount())]

        # Normalize and filter
        max_b = max(raw_b) if raw_b else 1.0
        _betweenness = {}
        for i, v in enumerate(p.G.vs):
            if v["status"] != "active":
                continue
            if degree_total[i] > _GENERIC_DEGREE_THRESHOLD:
                continue  # exclude generic utilities (get, append, close)
            if raw_b[i] > 0:
                _betweenness[v["id"]] = raw_b[i] / max_b

        print(f"[pipeline_api] betweenness computed: {len(_betweenness)} functions scored")
    except Exception as e:
        print(f"[pipeline_api] betweenness computation failed: {e}")


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



def commit_review(changed_function_ids: list[str]) -> list[dict]:
    """
    Review the impact of a commit that changed the given functions.
    Combines 5 layers:

      Layer 1 (blast radius): who calls these functions — HelixDB when available,
                               igraph simulation otherwise
      Layer 2 (betweenness):  how architecturally central are the changed functions
      Layer 3 (GraphSAGE drift): did the structural role change beyond the code change
      Layer 4 (CO_CHANGE flags): what historically moves with these functions
      Layer 5 (PPR territory): what downstream pipeline do these functions orchestrate

    Returns list of dicts sorted by severity (highest first):
    {
        "function_id": str,
        "name": str,
        "file": str,
        "severity": float,           # betweenness_norm * max(drift, 0.1) * log(callers+1)
        "betweenness": float,        # normalized 0-1, 0 if generic utility or unmeasured
        "drift": float | None,       # GraphSAGE embedding drift (None if not in GraphSAGE meta)
        "blast_radius": list[str],   # function names of direct+indirect callers
        "ppr_territory": list[str],  # functions this one orchestrates downstream
        "co_change_warnings": list[str],  # "changed X but not Y (co-change N times)"
        "test_scope": str,           # "local" | "broad" | "critical"
        "reason": str,               # human-readable explanation
    }

    NOTE: blast_radius uses igraph simulation (Python, not Rust) until AMO is
    ingested into HelixDB. After ingestion, replace with HelixDB trace_blast_radius
    for production use (4ms Rust traversal).
    """
    _require_init()
    p = _pipeline

    # Load GraphSAGE embeddings if available
    sage_emb = None
    sage_meta_by_id = {}
    sage_path = ROOT / "graphsage_minimal" / "out" / "graphsage_embeddings.npy"
    meta_path = ROOT / "graphsage_minimal" / "data" / "node_meta.json"
    if sage_path.exists() and meta_path.exists():
        import json as _json
        sage_emb = np.load(sage_path)
        meta = _json.loads(meta_path.read_text(encoding="utf-8"))
        sage_meta_by_id = {m["id"]: m for m in meta}

    # Load CO_CHANGE pairs
    cochange_path = ROOT / "sandbox" / "amo_cochange_pairs.json"
    cochange_pairs = []
    if cochange_path.exists():
        import json as _json
        cochange_pairs = _json.loads(cochange_path.read_text(encoding="utf-8"))

    # Build igraph blast radius (igraph simulation — replace with HelixDB after AMO ingest)
    def igraph_blast_radius(func_id: str, depth: int = 3) -> list[str]:
        idx = p.id_to_idx.get(func_id)
        if idx is None:
            return []
        visited = {idx}
        frontier = {idx}
        callers = []
        for _ in range(depth):
            next_f = set()
            for e in p.G.es:
                if e["type"] == "CALLS" and e.target in frontier and e.source not in visited:
                    next_f.add(e.source)
            frontier = next_f
            visited.update(frontier)
            for i in frontier:
                name = p.G.vs[i]["name"]
                if not name.startswith("test_"):
                    callers.append(p.G.vs[i]["name"])
        return callers

    # Build PPR territory (functions this one orchestrates downstream)
    def ppr_territory(func_id: str, k: int = 8) -> list[str]:
        idx = p.id_to_idx.get(func_id)
        if idx is None:
            return []
        reset = np.zeros(p.G.vcount())
        reset[idx] = 1.0
        calls_ids = [e.index for e in p.G.es if e["type"] == "CALLS"]
        calls_only = p.G.subgraph_edges(calls_ids, delete_vertices=False)
        ppr = calls_only.personalized_pagerank(
            vertices=None, damping=0.85, directed=True, weights=None, reset=reset.tolist()
        )
        ranked = sorted(
            [(ppr[i], p.G.vs[i]["name"], p.G.vs[i]["id"])
             for i in range(p.G.vcount())
             if i != idx and ppr[i] > 0 and p.G.vs[i]["status"] == "active"
             and not p.G.vs[i]["name"].startswith("test_")],
            reverse=True
        )[:k]
        return [name for _, name, _ in ranked]

    results = []
    changed_set = set(changed_function_ids)

    for func_id in changed_function_ids:
        # Find node
        idx = p.id_to_idx.get(func_id)
        if idx is None:
            continue
        v = p.G.vs[idx]
        name = v["name"]
        file = v["file"].split("/")[-1]

        # Layer 2: betweenness
        b_score = _betweenness.get(func_id, 0.0)

        # Layer 3: GraphSAGE drift
        drift = None
        if sage_emb is not None and func_id in sage_meta_by_id:
            sage_idx = sage_meta_by_id[func_id]["idx"]
            old_vec = sage_emb[sage_idx]
            # Current embedding from igraph node (same space, already normalized)
            emb = v.get("embedding") if hasattr(v, "get") else v["embedding"]
            if emb is not None and len(emb) == sage_emb.shape[1]:
                drift = float(1.0 - cosine_sim_np(old_vec, np.array(emb)))

        # Layer 1: blast radius (igraph simulation)
        blast = igraph_blast_radius(func_id, depth=3)

        # Layer 5: PPR territory
        territory = ppr_territory(func_id, k=8)

        # Layer 4: CO_CHANGE warnings
        warnings = []
        for pair in cochange_pairs:
            src, tgt = pair.get("source", ""), pair.get("target", "")
            if func_id not in (src, tgt):
                continue
            other_id = tgt if src == func_id else src
            other_name = other_id.split("::")[-1]
            count = pair.get("occurrence_count", 0)
            if count >= 3 and other_id not in changed_set:
                warnings.append(
                    f"changed '{name}' but not '{other_name}' "
                    f"(co-change {count}x, {pair.get('category')})"
                )

        # Severity = betweenness * max(drift, 0.1) * log(callers+1)
        import math
        effective_drift = max(drift, 0.1) if drift is not None else 0.1
        severity = b_score * effective_drift * math.log(len(blast) + 2)

        # Test scope
        if b_score > 0.5 or (drift is not None and drift > 0.4):
            test_scope = "critical"
            reason = f"high betweenness ({b_score:.2f})" + (f" + high drift ({drift:.2f})" if drift else "")
        elif b_score > 0.1 or len(blast) > 5:
            test_scope = "broad"
            reason = f"moderate centrality, {len(blast)} callers"
        else:
            test_scope = "local"
            reason = f"leaf/near-leaf function, {len(blast)} callers"

        if warnings:
            reason += f" | {len(warnings)} co-change warning(s)"

        results.append({
            "function_id": func_id,
            "name": name,
            "file": file,
            "severity": round(severity, 4),
            "betweenness": round(b_score, 4),
            "drift": round(drift, 4) if drift is not None else None,
            "blast_radius": blast[:20],
            "ppr_territory": territory,
            "co_change_warnings": warnings,
            "test_scope": test_scope,
            "reason": reason,
        })

    results.sort(key=lambda x: x["severity"], reverse=True)
    return results


def cosine_sim_np(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    return float(np.dot(a, b) / (na * nb)) if na > 0 and nb > 0 else 0.0


def select_tests(changed_function_ids: list[str]) -> dict:
    """
    Given changed functions, return the minimum test set needed.

    Uses betweenness + GraphSAGE drift to scope test selection:
      critical functions (high betweenness or high drift) -> all blast radius tests
      broad functions -> tests within 2 hops
      local/leaf functions -> only direct tests

    Returns:
    {
        "critical": list[str],   # test functions that must run
        "recommended": list[str], # test functions that should run
        "skippable": list[str],  # test functions that can be skipped
        "co_change_warnings": list[str],
        "summary": str,
    }
    """
    _require_init()

    reviews = commit_review(changed_function_ids)
    if not reviews:
        return {"critical": [], "recommended": [], "skippable": [], "co_change_warnings": [], "summary": "No functions found"}

    p = _pipeline
    # Find all test functions in graph
    all_tests = [v["name"] for v in p.G.vs
                 if v["status"] == "active"
                 and (v["name"].startswith("test_") or
                      (v["file"] or "").replace("\\", "/").split("/")[-1].startswith("test_"))]

    # Blast radius across all changed functions
    all_callers = set()
    critical_callers = set()
    broad_callers = set()

    for r in reviews:
        callers = set(r["blast_radius"])
        all_callers.update(callers)
        if r["test_scope"] == "critical":
            critical_callers.update(callers)
        elif r["test_scope"] == "broad":
            broad_callers.update(callers)

    # Map callers to test functions
    def tests_for(caller_names: set) -> list[str]:
        return [t for t in all_tests if any(
            c.lower() in t.lower() for c in caller_names
        )]

    critical_tests = tests_for(critical_callers)
    broad_tests    = [t for t in tests_for(broad_callers) if t not in critical_tests]
    all_warnings   = [w for r in reviews for w in r["co_change_warnings"]]

    skippable = [t for t in all_tests
                 if t not in critical_tests and t not in broad_tests]

    n_total = len(all_tests)
    n_run = len(critical_tests) + len(broad_tests)
    reduction = round(100 * (1 - n_run / max(n_total, 1)))

    summary = (
        f"Run {n_run}/{n_total} tests ({reduction}% reduction). "
        f"{len(reviews)} changed functions: "
        f"{sum(1 for r in reviews if r['test_scope']=='critical')} critical, "
        f"{sum(1 for r in reviews if r['test_scope']=='broad')} broad, "
        f"{sum(1 for r in reviews if r['test_scope']=='local')} local."
    )

    return {
        "critical": critical_tests,
        "recommended": broad_tests,
        "skippable": skippable[:20],  # cap for readability
        "co_change_warnings": all_warnings,
        "summary": summary,
        "per_function": reviews,
    }


# ── Sub-agent tools for /annotate-commit ──────────────────────────────────────
# These four functions are called by the sub-agent during commit annotation.
# They are also exposed through the MCP server as standalone tools.

def list_functions_changed_in_commit(sha: str) -> list[dict]:
    """
    Return functions changed in a commit, with their diffs.

    Uses GitPython to read the commit, tree-sitter to parse changed .py files.
    Returns list of:
    {
        "func_id": str,      # full node ID (file::function_name)
        "name": str,
        "file": str,
        "is_new": bool,      # True if function didn't exist in parent commit
        "diff_text": str,    # unified diff text for this function only
        "new_code": str,     # current function code
        "old_code": str,     # code before this commit (empty if new)
    }
    """
    try:
        import git
        import re
        from pathlib import Path as _Path

        repo_path = ROOT / ".."/  "agent-memory-orchestrator"
        if not repo_path.exists():
            # fallback to current repo
            repo_path = ROOT

        repo = git.Repo(str(repo_path))
        commit = repo.commit(sha)
        parent = commit.parents[0] if commit.parents else None

        results = []
        changed_files = []
        if parent:
            for diff in parent.diff(commit):
                path = diff.b_path or diff.a_path
                if path and path.endswith(".py"):
                    changed_files.append(path)
        else:
            for item in commit.tree.traverse():
                if hasattr(item, "path") and item.path.endswith(".py"):
                    changed_files.append(item.path)

        import tree_sitter_python as _tspy
        from tree_sitter import Language as _Lang, Parser as _Parser
        _py = _Lang(_tspy.language(), "python")
        _p = _Parser(); _p.set_language(_py)

        def _extract_functions(source: str, file_path: str) -> dict[str, str]:
            """Return {func_name: code} for all functions in source."""
            src = source.encode("utf-8")
            tree = _p.parse(src)
            fns = {}
            def walk(node):
                if node.type in ("function_definition", "decorated_definition"):
                    fn = node if node.type == "function_definition" else node.child_by_field_name("definition")
                    if fn:
                        nm = fn.child_by_field_name("name")
                        if nm:
                            name = src[nm.start_byte:nm.end_byte].decode("utf-8")
                            code = src[fn.start_byte:fn.end_byte].decode("utf-8")
                            fns[name] = code
                for child in node.children:
                    walk(child)
            walk(tree.root_node)
            return fns

        for file_path in changed_files:
            try:
                new_blob = commit.tree / file_path
                new_src = new_blob.data_stream.read().decode("utf-8", errors="replace")
                new_fns = _extract_functions(new_src, file_path)
            except KeyError:
                new_fns = {}

            try:
                old_blob = (parent.tree / file_path) if parent else None
                old_src = old_blob.data_stream.read().decode("utf-8", errors="replace") if old_blob else ""
                old_fns = _extract_functions(old_src, file_path)
            except KeyError:
                old_fns = {}

            safe = file_path.replace("/", "_").replace("\\", "_").replace(".py", "")

            for func_name, new_code in new_fns.items():
                old_code = old_fns.get(func_name, "")
                if new_code == old_code:
                    continue  # unchanged
                is_new = func_name not in old_fns
                func_id = f"{file_path}::{func_name}"

                # Build simple diff text
                import difflib
                diff_lines = list(difflib.unified_diff(
                    old_code.splitlines(keepends=True),
                    new_code.splitlines(keepends=True),
                    fromfile=f"a/{file_path}",
                    tofile=f"b/{file_path}",
                    n=3,
                ))
                diff_text = "".join(diff_lines)[:3000]

                results.append({
                    "func_id": func_id,
                    "name": func_name,
                    "file": file_path,
                    "is_new": is_new,
                    "diff_text": diff_text,
                    "new_code": new_code[:2000],
                    "old_code": old_code[:2000],
                })

        return results

    except Exception as e:
        return [{"error": str(e)}]


def read_function_memory_history(func_id: str) -> list[dict]:
    """
    Return all annotated memories for a function.

    Only returns states where memory is non-empty (annotation has been written).
    Fetches edge_type by querying incoming edges on each state.

    Returns list of:
    {
        "state_id": str,
        "commit_sha": str,
        "memory": str,
        "edge_type": str,   # REDESIGNED / FIXED / EXTENDED / REFACTORED / INTRODUCED
    }
    Returns empty list if no memories exist yet.
    """
    try:
        from helixdb import Client as _Client, g as _g, read_batch as _rb, Predicate as _Pred, Projection as _Proj, define_params as _dp, param as _p
        c = _Client("http://127.0.0.1:6969")

        # Fetch all FunctionState nodes for this function
        batch = (
            _rb()
            .var_as("states",
                _g().n_with_label("FunctionState")
                   .where(_Pred.eq("function_id", func_id))
                   .project([
                       _Proj.property("node_id"),
                       _Proj.property("commit"),
                       _Proj.property("memory"),
                   ])
            )
            .returning(["states"])
        )
        result = c.query().dynamic(batch.to_dynamic_request()).send()
        states = result.get("states", {}).get("properties", [])

        # Bug 1 fix: filter in Python — HelixDB treats "" as non-null,
        # so is_not_null() would pass every unannotated state (memory="").
        # Only keep states where memory is actually populated.
        annotated = [s for s in states if s.get("memory") and s.get("memory").strip()]

        if not annotated:
            return []

        # Bug 2 fix: fetch edge_type per state via incoming edge traversal.
        # edge_type lives on the Commit->FunctionState edge, not on the node.
        _SEMANTIC_EDGES = {"REDESIGNED", "FIXED", "EXTENDED", "REFACTORED", "INTRODUCED"}
        _PARAMS_ET = _dp({"nid": _p.string()})
        results = []
        for s in annotated:
            state_id = s.get("node_id", "")
            edge_type = "GENERATED"  # default if no semantic edge found

            try:
                et_batch = (
                    _rb()
                    .var_as("incoming",
                        _g().n_with_label("FunctionState")
                           .where(_Pred.eq_param("node_id", "nid"))
                           .in_e()
                           .project([_Proj.property("label")])
                    )
                    .returning(["incoming"])
                )
                et_result = c.query().dynamic(
                    et_batch.to_dynamic_request(_PARAMS_ET, {"nid": state_id})
                ).send()
                edge_labels = [
                    e.get("label", "")
                    for e in et_result.get("incoming", {}).get("properties", [])
                ]
                # Prefer semantic edge over GENERATED
                for preferred in ("REDESIGNED", "FIXED", "EXTENDED", "REFACTORED", "INTRODUCED"):
                    if preferred in edge_labels:
                        edge_type = preferred
                        break
            except Exception:
                pass  # edge_type stays as "GENERATED"

            results.append({
                "state_id": state_id,
                "commit_sha": s.get("commit", ""),
                "memory": s.get("memory", ""),
                "edge_type": edge_type,
            })

        return results

    except Exception as e:
        return [{"error": str(e)}]


_ALLOWED_EDGE_TYPES = {"REDESIGNED", "FIXED", "EXTENDED", "REFACTORED"}

def write_function_memory(
    func_id: str,
    commit_sha: str,
    edge_type: str,
    memory_text: str,
) -> dict:
    """
    Write an LLM-derived memory note to a FunctionState node.

    Validates edge_type is one of: REDESIGNED / FIXED / EXTENDED / REFACTORED
    Embeds memory_text to get memory_vec.
    Updates FunctionState in HelixDB: memory + memory_vec fields.
    Creates typed edge from Commit to FunctionState.

    Returns:
    {
        "ok": bool,
        "func_id": str,
        "state_id": str,    # the FunctionState node that was updated
        "edge_type": str,
        "error": str | None,
    }
    """
    if edge_type not in _ALLOWED_EDGE_TYPES:
        return {
            "ok": False, "func_id": func_id, "state_id": None,
            "edge_type": edge_type,
            "error": f"Invalid edge_type '{edge_type}'. Must be one of: {sorted(_ALLOWED_EDGE_TYPES)}"
        }

    if not memory_text or not memory_text.strip():
        return {"ok": False, "func_id": func_id, "state_id": None,
                "edge_type": edge_type, "error": "memory_text cannot be empty"}

    # Find the FunctionState node for this function at this commit
    # state_id convention: state_{safe_file}_{func_name}_{commit_hash7}
    # But func_id is "file::name", so we need to query HelixDB
    try:
        from helixdb import Client as _Client, g as _g, write_batch as _wb, read_batch as _rb
        from helixdb import Predicate as _Pred, Projection as _Proj, PropertyInput as _PI, PropertyValue as _PV
        from helixdb import define_params as _dp, param as _p, NodeRef as _NR

        c = _Client("http://127.0.0.1:6969")

        # Find the FunctionState for this function + commit
        batch = (
            _rb()
            .var_as("state",
                _g().n_with_label("FunctionState")
                   .where(_Pred.eq("function_id", func_id))
                   .where(_Pred.eq("commit", commit_sha[:7]))
                   .project([_Proj.property("node_id")])
            )
            .returning(["state"])
        )
        result = c.query().dynamic(batch.to_dynamic_request()).send()
        states = result.get("state", {}).get("properties", [])
        if not states:
            return {"ok": False, "func_id": func_id, "state_id": None,
                    "edge_type": edge_type,
                    "error": f"No FunctionState found for {func_id} at commit {commit_sha[:7]}"}

        state_id = states[0]["node_id"]

        # Embed the memory text
        memory_vec = _embed(memory_text)

        # Write memory and memory_vec to FunctionState
        _PARAMS = _dp({"nid": _p.string(), "mem": _p.string()})
        update_batch = (
            _wb()
            .var_as("n",
                _g().n_with_label("FunctionState")
                   .where(_Pred.eq_param("node_id", "nid"))
                   .set_property("memory", _PI.param("mem"))
            )
            .returning(["n"])
        )
        c.query().dynamic(
            update_batch.to_dynamic_request(_PARAMS, {"nid": state_id, "mem": memory_text})
        ).send()

        # Write memory_vec
        _PARAMS_VEC = _dp({"nid": _p.string()})
        vec_batch = (
            _wb()
            .var_as("n",
                _g().n_with_label("FunctionState")
                   .where(_Pred.eq_param("node_id", "nid"))
                   .set_property("memory_vec", _PI.value(_PV.f32_array(memory_vec)))
            )
            .returning(["n"])
        )
        c.query().dynamic(
            vec_batch.to_dynamic_request(_PARAMS_VEC, {"nid": state_id})
        ).send()

        # Create typed edge from Commit to FunctionState
        commit_id = f"commit_{commit_sha[:7]}"
        _EDGE_PARAMS = _dp({"src_id": _p.string(), "tgt_id": _p.string()})
        edge_batch = (
            _wb()
            .var_as("src", _g().n_with_label("Commit").where(_Pred.eq_param("node_id", "src_id")))
            .var_as("tgt", _g().n_with_label("FunctionState").where(_Pred.eq_param("node_id", "tgt_id")))
            .var_as("e", _g().n(_NR.var("src")).add_e(edge_type, _NR.var("tgt"), {}))
            .returning(["e"])
        )
        c.query().dynamic(
            edge_batch.to_dynamic_request(_EDGE_PARAMS, {"src_id": commit_id, "tgt_id": state_id})
        ).send()

        return {"ok": True, "func_id": func_id, "state_id": state_id,
                "edge_type": edge_type, "error": None}

    except Exception as e:
        return {"ok": False, "func_id": func_id, "state_id": None,
                "edge_type": edge_type, "error": str(e)}


def log_new_cooccurrence(func_id_a: str, func_id_b: str, commit_sha: str) -> None:
    """
    Log a co-occurrence of two functions in a commit as a candidate CO_CHANGE pair.

    Does NOT promote to CO_CHANGE. The existing cochange_analysis.py gate
    (occurrence_count >= 3 AND jaccard >= 0.20) handles promotion on next full run.

    Appends to sandbox/cooccurrence_candidates.jsonl.
    """
    import json as _json
    from datetime import datetime, timezone

    candidates_path = ROOT / "sandbox" / "cooccurrence_candidates.jsonl"
    entry = {
        "source": func_id_a,
        "target": func_id_b,
        "commit": commit_sha,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with open(candidates_path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(entry) + "\n")
    except Exception as e:
        print(f"[pipeline_api] log_new_cooccurrence failed: {e}")


def annotate_commit(sha: str, debug: bool = False) -> dict:
    """
    Annotate the semantic memory of functions changed in a commit.

    For each meaningfully changed function:
    1. Reads the diff and commit message
    2. Reads prior memory history for context
    3. Calls LLM (with system prompt from skills/annotate_commit_prompt.md)
       to decide: skip (trivial) or classify + write memory
    4. Writes memory + typed edge to HelixDB via write_function_memory()
    5. Logs unknown co-occurring pairs to sandbox/cooccurrence_candidates.jsonl

    Returns:
    {
        "sha": str,
        "annotated": [{"func_id", "edge_type", "memory"}],
        "skipped": [{"func_id", "reason"}],
        "errors": [{"func_id", "error"}],
        "cooccurrence_candidates": int,
    }

    debug=True: writes full JSON log to sandbox/out/annotate_commit_{sha[:12]}.json
    """
    result = {
        "sha": sha,
        "annotated": [],
        "skipped": [],
        "errors": [],
        "cooccurrence_candidates": 0,
    }

    # Load system prompt
    prompt_path = ROOT / "skills" / "annotate_commit_prompt.md"
    if not prompt_path.exists():
        result["errors"].append({"func_id": "global", "error": "skills/annotate_commit_prompt.md not found"})
        return result

    system_prompt = prompt_path.read_text(encoding="utf-8")

    # Step 1: get changed functions
    changed = list_functions_changed_in_commit(sha)
    if not changed:
        result["skipped"].append({"func_id": "global", "reason": "no Python functions changed"})
        return result
    if changed and "error" in changed[0]:
        result["errors"].append({"func_id": "global", "error": changed[0]["error"]})
        return result

    # Track annotated func_ids for co-occurrence logging
    annotated_ids = []
    known_cochange_ids = set()
    try:
        import json as _json
        pairs_path = ROOT / "sandbox" / "amo_cochange_pairs.json"
        if pairs_path.exists():
            pairs = _json.loads(pairs_path.read_text(encoding="utf-8"))
            for p in pairs:
                known_cochange_ids.add((p.get("source",""), p.get("target","")))
                known_cochange_ids.add((p.get("target",""), p.get("source","")))
    except Exception:
        pass

    for func in changed:
        func_id = func.get("func_id", "")
        func_name = func.get("name", "")
        is_new = func.get("is_new", False)

        # Skip new functions — INTRODUCED edge created automatically by scalable_ingest
        if is_new:
            result["skipped"].append({"func_id": func_id, "reason": "new function (INTRODUCED edge created automatically)"})
            continue

        # Step 2: read prior history for context
        history = read_function_memory_history(func_id)
        prior_context = ""
        if history and "error" not in history[0]:
            prior_context = "\n".join(
                f"- {h.get('edge_type','?')}: {h.get('memory','')}" for h in history[:3]
            )

        # Step 3: call LLM
        user_prompt = f"""DRY-RUN MODE: Do not call any tools. Reply with ONLY a JSON object.

Commit SHA: {sha[:12]}
Function: {func_name} (file: {func.get('file','?')})

Commit message:
{func.get('diff_text','')[:100].split(chr(10))[0] if func.get('diff_text') else 'No message'}

Diff:
{func.get('diff_text','')[:2000]}

Prior memory history (most recent first):
{prior_context if prior_context else '(none — first annotation)'}

Reply with ONLY this JSON (no other text):
{{"skip": false, "edge_type": "REDESIGNED|FIXED|EXTENDED|REFACTORED", "memory": "1-2 sentences: what changed and why"}}
OR if trivial:
{{"skip": true, "skip_reason": "brief reason"}}"""

        try:
            import json as _json
            import urllib.request as _req

            payload = _json.dumps({
                "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
                "messages": [
                    {"role": "system", "content": system_prompt[:3000]},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": 200,
                "temperature": 0,
            }).encode()

            request = _req.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=payload,
                headers={"Authorization": f"Bearer {_API_KEY}", "Content-Type": "application/json"},
            )
            response = _json.loads(_req.urlopen(request, timeout=30).read())
            raw = response["choices"][0]["message"]["content"].strip()

            # Parse JSON from response
            import re as _re
            json_match = _re.search(r'\{[^{}]+\}', raw, _re.DOTALL)
            if not json_match:
                result["errors"].append({"func_id": func_id, "error": f"No JSON in response: {raw[:100]}"})
                continue

            decision = _json.loads(json_match.group())

            if decision.get("skip"):
                result["skipped"].append({"func_id": func_id, "reason": decision.get("skip_reason", "trivial")})
                continue

            edge_type = decision.get("edge_type", "")
            memory_text = decision.get("memory", "")

            if edge_type not in _ALLOWED_EDGE_TYPES:
                result["errors"].append({"func_id": func_id, "error": f"Invalid edge_type from LLM: {edge_type}"})
                continue

            if not memory_text.strip():
                result["errors"].append({"func_id": func_id, "error": "Empty memory from LLM"})
                continue

            # Step 4: write to HelixDB
            write_result = write_function_memory(func_id, sha, edge_type, memory_text)
            if write_result.get("ok"):
                result["annotated"].append({
                    "func_id": func_id,
                    "edge_type": edge_type,
                    "memory": memory_text,
                })
                annotated_ids.append(func_id)
            else:
                result["errors"].append({"func_id": func_id, "error": write_result.get("error", "write failed")})

        except Exception as e:
            result["errors"].append({"func_id": func_id, "error": str(e)})

    # Step 5: log unknown co-occurring pairs
    for i, id_a in enumerate(annotated_ids):
        for id_b in annotated_ids[i+1:]:
            pair = (id_a, id_b)
            if pair not in known_cochange_ids and (id_b, id_a) not in known_cochange_ids:
                log_new_cooccurrence(id_a, id_b, sha)
                result["cooccurrence_candidates"] += 1

    # Step 6: debug log
    if debug:
        import json as _json
        out_dir = ROOT / "sandbox" / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"annotate_commit_{sha[:12]}.json"
        out_path.write_text(_json.dumps({
            "sha": sha,
            "changed_functions": changed,
            "result": result,
        }, indent=2), encoding="utf-8")

    return result


def query_function_history(func_id: str, topic: str) -> list[dict]:
    """
    Search a function's semantic memory history by topic.

    Vector search on memory_vec across ALL FunctionState nodes for this function
    (not just active — includes superseded states).

    Returns memories ranked by relevance to topic, newest-first within top results.
    Each entry: {state_id, commit_sha, memory, edge_type, score}

    Use before modifying a function to understand its design history.
    """
    memories = read_function_memory_history(func_id)
    if not memories or (memories and "error" in memories[0]):
        return memories

    if not topic or not topic.strip():
        return memories  # return all if no topic

    # Embed the topic
    topic_vec = _embed(topic)
    topic_norm = float(np.linalg.norm(topic_vec))
    if topic_norm < 1e-8:
        return memories

    # Score each memory by cosine similarity
    scored = []
    for m in memories:
        memory_text = m.get("memory", "")
        if not memory_text:
            continue
        mem_vec = _embed(memory_text)
        mem_norm = float(np.linalg.norm(mem_vec))
        if mem_norm < 1e-8:
            score = 0.0
        else:
            score = float(np.dot(topic_vec, mem_vec) / (topic_norm * mem_norm))
        scored.append({**m, "score": round(score, 4)})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored
