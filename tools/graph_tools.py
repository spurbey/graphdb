"""
Agent toolset for the graph knowledge base. Works with any ingested repo.
All node IDs are prefixed with the repo name (e.g. dograh:func_..., amo:func_...).
"""

from __future__ import annotations
import sys, os, ast, re, importlib, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from helixdb import Client, g, read_batch, define_params, param, Predicate, Projection

HELIX_URL  = "http://127.0.0.1:6969"
REPO_ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── igraph pipeline (primary search) ──────────────────────────────────────────
_pipeline_ready = False
_pipeline_lock = threading.Lock()

def _ensure_pipeline() -> bool:
    """Initialize the igraph pipeline on first use. Returns True if available."""
    global _pipeline_ready
    if _pipeline_ready:
        return True
    with _pipeline_lock:
        if _pipeline_ready:
            return True
        try:
            from pipeline_api import initialize
            initialize(data_root=REPO_ROOT)
            _pipeline_ready = True
            return True
        except Exception as e:
            print(f"[graph_tools] igraph pipeline unavailable: {e}")
            return False

# ── Embedding helper (same model as scalable_ingest) ──────────────────────────
_EMBED_DIMS = 384
_EMBED_MODEL = None
_EMBED_LOCK = threading.Lock()

def _get_embedder():
    global _EMBED_MODEL
    if _EMBED_MODEL is None:
        with _EMBED_LOCK:
            if _EMBED_MODEL is None:
                from sentence_transformers import SentenceTransformer
                _EMBED_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
    return _EMBED_MODEL

def _embed(texts: str | list[str]) -> list[float] | list[list[float]]:
    single = isinstance(texts, str)
    if single:
        texts = [texts]
    model = _get_embedder()
    vecs = model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
    return [v.tolist() for v in vecs]


def _c() -> Client:
    return Client(HELIX_URL)


def _rows(result: dict, key: str) -> list[dict]:
    return result.get(key, {}).get("properties", [])


def _fallback_name(node_id: str, vector_id: object) -> str:
    if node_id:
        return node_id.rsplit("_", 1)[-1]
    return f"vector_{vector_id}"


def _resolve_code_vector_hits(hits: list[dict], k: int) -> list[dict]:
    """Resolve TurboVec vector IDs through HelixDB FunctionIdentity metadata."""
    client = _c()
    results = []
    for hit in hits:
        vector_id = hit.get("vector_id")
        node_id_hint = hit.get("id", "")
        item = {
            "function_id": node_id_hint,
            "vector_id": vector_id,
            "name": _fallback_name(node_id_hint, vector_id),
            "file": "",
            "score": hit.get("score", 0),
            "resolved_via": "cache_hint" if node_id_hint else "unresolved",
        }
        if vector_id is not None:
            batch = (
                read_batch()
                .var_as(
                    "fn",
                    g()
                    .n_with_label("FunctionIdentity")
                    .where(Predicate.eq("code_vector_id", str(vector_id)))
                    .limit(1)
                    .project([
                        Projection.property("node_id"),
                        Projection.property("code_vector_source_node_id"),
                        Projection.property("name"),
                        Projection.property("file"),
                    ]),
                )
                .returning(["fn"])
            )
            try:
                rows = _rows(client.query().dynamic(batch.to_dynamic_request()).send(), "fn")
                if rows:
                    row = rows[0]
                    resolved_id = row.get("code_vector_source_node_id") or row.get("node_id") or node_id_hint
                    item.update({
                        "function_id": resolved_id,
                        "name": row.get("name") or _fallback_name(resolved_id, vector_id),
                        "file": row.get("file", ""),
                        "resolved_via": "helix_code_vector_id",
                    })
            except Exception as e:
                item["resolve_error"] = str(e)
        results.append(item)
        if len(results) >= k:
            break
    return results


# ── Tool 1: Semantic search (igraph pipeline — primary) ───────────────────────

def search_code_semantics(prompt: str, k: int = 10, mode: str = "general_retrieval") -> dict | list[dict]:
    """
    Primary semantic search using the igraph PPR pipeline.

    Returns a subgraph dict. Always includes "pipeline_mode" field:
    - "igraph": full PPR subgraph with nodes + edges (use this)
    - "unavailable": pipeline not loaded; "error" field explains why.
      In this case, also returns HelixDB flat-list fallback under "helix_fallback".

    If you see pipeline_mode == "unavailable", check that:
    1. igraph pipeline data files exist for the target repo
    2. igraph and numpy are installed
    3. The MCP server was started from the graphdb repo root
    """
    if _ensure_pipeline():
        from pipeline_api import search as _igraph_search
        result = _igraph_search(prompt, k=k, mode=mode)
        if result.get("pipeline_mode") == "igraph":
            return result
        # pipeline_api returned an error — fall through to HelixDB with the error attached
        result["helix_fallback"] = search_code_semantics_helix(prompt, k=k)
        return result

    # pipeline unavailable entirely
    return {
        "pipeline_mode": "unavailable",
        "error": "igraph pipeline could not be loaded — see server startup log",
        "helix_fallback": search_code_semantics_helix(prompt, k=k),
    }


def search_code_semantics_helix(prompt: str, k: int = 5) -> list[dict]:
    """
    Raw turbovec vector search fallback. Returns flat list without graph structure.
    Use search_code_semantics() for the full pipeline with subgraph output.
    """
    vecs = _embed(prompt)
    if not vecs:
        return []
    vec = vecs[0]

    import turbovec_adapter
    top_k = turbovec_adapter.code_index.search(vec, k * 3)
    if not top_k:
        return []
    return _resolve_code_vector_hits(top_k, k)


# ── Tool: explain_coupling ─────────────────────────────────────────────────────

def explain_coupling(func_id_a: str, func_id_b: str) -> dict:
    """
    Return the CO_CHANGE relationship between two functions if it exists.

    func_id_a / func_id_b: full node IDs (e.g. repo_name:func_path_file_funcName)

    Returns the co-change edge data (category, jaccard-implied count,
    theme proportions) or {"coupled": false} if no relationship exists.
    """
    if _ensure_pipeline():
        try:
            from pipeline_api import explain_coupling as _explain
            result = _explain(func_id_a, func_id_b)
            if result is not None:
                result["coupled"] = True
                return result
            return {"coupled": False, "func_id_a": func_id_a, "func_id_b": func_id_b}
        except Exception as e:
            return {"error": str(e)}
    return {"error": "igraph pipeline unavailable"}


# ── Tool: trace_semantic_evolution ─────────────────────────────────────────────

def trace_semantic_evolution(func_id: str) -> dict:
    if not _ensure_pipeline():
        return {"error": "igraph pipeline unavailable"}
    try:
        from pipeline_api import read_function_memory_history
        import json
        mock_file = os.path.join(REPO_ROOT, "sandbox", "mock_memories_embedded.json")
        if not os.path.exists(mock_file):
            return {"error": "No mock memory file found for traversal test."}

        with open(mock_file, "r", encoding="utf-8") as f:
            all_mock = json.load(f)

        history = read_function_memory_history(func_id)
        if not history:
            return {"error": f"No memory history found for {func_id}"}

        evolution = {
            "target_function": func_id,
            "timeline": []
        }

        for h in history:
            commit = h["commit_sha"]
            coupled_changes = []

            for other in all_mock:
                other_id = other["function_id"]
                if other_id == func_id:
                    continue

                for other_h in other["history"]:
                    if other_h["commit_sha"] == commit:
                        coupling = explain_coupling(func_id, other_id)
                        coupled_changes.append({
                            "function_id": other_id,
                            "edge_type": other_h["edge_type"],
                            "memory": other_h["memory"],
                            "historically_coupled": coupling.get("coupled", False)
                        })

            evolution["timeline"].append({
                "commit_sha": commit,
                "target_edge": h["edge_type"],
                "target_memory": h["memory"],
                "coupled_changes_in_commit": coupled_changes
            })

        return evolution
    except Exception as e:
        return {"error": str(e)}


# ── Tool: pipeline_status ──────────────────────────────────────────────────────

def pipeline_status() -> dict:
    """
    Return igraph pipeline health. Use to verify which search mode is active.

    If pipeline is unavailable, search_code_semantics falls back to HelixDB
    vector search — results will be a flat list instead of a subgraph.
    """
    if _ensure_pipeline():
        try:
            from pipeline_api import status as _status
            return _status()
        except Exception as e:
            return {"pipeline": "unavailable", "error": str(e)}
    return {"pipeline": "unavailable", "error": "igraph pipeline failed to initialize"}


def find_structural_siblings(func_id: str, k: int = 8) -> list[dict]:
    """
    Find functions that play the same architectural role as func_id.

    Uses GraphSAGE structural embeddings (128-dim). Finds functions at the
    same depth in the call hierarchy with similar call patterns — regardless
    of whether their names or code are semantically similar.

    Different from search_code_semantics (which finds semantically similar
    functions). This finds architecturally equivalent functions:
      memory_write [server.py] -> memory_write [tools.py], add_memory_unit,
      process_event (the full MCP->tool->storage chain)

    Use this when you want to know: "what other functions do the same job
    as this one, possibly in a different module?"

    func_id: full node ID (e.g. repo_name:func_path_file_funcName)
    """
    if _ensure_pipeline():
        try:
            from pipeline_api import find_structural_siblings as _find
            return _find(func_id, k=k)
        except Exception as e:
            return [{"error": str(e)}]
    return [{"error": "igraph pipeline unavailable"}]


def commit_review(changed_function_ids: list[str]) -> list[dict]:
    """
    Review the impact of a commit that changed the given functions.
    Combines 5 layers: blast radius, betweenness centrality, GraphSAGE drift,
    CO_CHANGE warnings, and PPR downstream territory.

    Returns list sorted by severity (highest first). Each entry:
    {
        "name": str, "file": str, "severity": float,
        "betweenness": float,       # architectural centrality (0-1)
        "drift": float | None,      # GraphSAGE structural role change
        "blast_radius": list[str],  # functions that call this
        "ppr_territory": list[str], # functions this orchestrates downstream
        "co_change_warnings": list[str],  # "changed X but not Y"
        "test_scope": "local" | "broad" | "critical",
        "reason": str,
    }
    """
    if _ensure_pipeline():
        try:
            from pipeline_api import commit_review as _cr
            return _cr(changed_function_ids)
        except Exception as e:
            return [{"error": str(e)}]
    return [{"error": "igraph pipeline unavailable"}]


def select_tests(changed_function_ids: list[str]) -> dict:
    """
    Given changed functions, return the minimum test set to run.
    Uses betweenness + GraphSAGE drift to scope:
      critical -> all blast radius tests
      broad -> 2-hop tests
      local -> direct tests only

    Returns: {critical, recommended, skippable, co_change_warnings, summary}
    """
    if _ensure_pipeline():
        try:
            from pipeline_api import select_tests as _st
            return _st(changed_function_ids)
        except Exception as e:
            return {"error": str(e)}
    return {"error": "igraph pipeline unavailable"}


def annotate_commit(sha: str, debug: bool = False) -> dict:
    """
    Annotate the semantic memory of functions changed in a commit.

    Triggered by /annotate-commit slash command. Reads diffs, calls LLM
    with skills/annotate_commit_prompt.md, writes memory + typed edges
    (REDESIGNED/FIXED/EXTENDED/REFACTORED) to HelixDB.

    sha: commit hash (full or short)
    debug: if True, writes JSON log to sandbox/out/annotate_commit_{sha}.json

    Returns: {sha, annotated: [{func_id, edge_type, memory}],
              skipped: [{func_id, reason}], errors: [...],
              cooccurrence_candidates: int}
    """
    if _ensure_pipeline():
        try:
            from pipeline_api import annotate_commit as _ac
            return _ac(sha, debug=debug)
        except Exception as e:
            return {"sha": sha, "annotated": [], "skipped": [],
                    "errors": [{"func_id": "global", "error": str(e)}],
                    "cooccurrence_candidates": 0}
    return {"sha": sha, "annotated": [], "skipped": [],
            "errors": [{"func_id": "global", "error": "igraph pipeline unavailable"}],
            "cooccurrence_candidates": 0}


def query_function_history(func_id: str, topic: str) -> list[dict]:
    """
    Search a function's semantic memory history by topic.

    Vector search on memory_vec across ALL FunctionState nodes for this function
    (including superseded states — full design history). Returns memories ranked
    by relevance to topic with commit SHA, edge type, and similarity score.

    Use before modifying a function to understand its design decisions.

    func_id: full node ID (e.g. repo_name:func_path_file_funcName)
    topic: natural language query e.g.
      'session handling and agent normalization'
    """
    if _ensure_pipeline():
        try:
            from pipeline_api import query_function_history as _qfh
            return _qfh(func_id, topic)
        except Exception as e:
            return [{"error": str(e)}]
    return [{"error": "igraph pipeline unavailable"}]


# ── Tool 2: Time-travel diff ───────────────────────────────────────────────

_P_NID = define_params({"nid": param.string()})

def get_code_time_travel_diff(state_node_id: str) -> dict:
    """
    Given a FunctionState node_id, returns both the current state and the
    immediately preceding state (via PREVIOUS_VERSION edge).
    Returns {current: {...}, previous: {...} | None}
    """
    c = _c()

    current_batch = (
        read_batch()
        .var_as("cur",
            g().n_with_label("FunctionState")
               .where(Predicate.eq_param("node_id", "nid"))
               .project([
                   Projection.property("node_id"),
                   Projection.property("ai_summary"),
                   Projection.property("code"),
                   Projection.property("commit"),
                   Projection.property("status"),
               ])
        )
        .returning(["cur"])
    )
    prev_batch = (
        read_batch()
        .var_as("prev",
            g().n_with_label("FunctionState")
               .where(Predicate.eq_param("node_id", "nid"))
               .out("PREVIOUS_VERSION")
               .project([
                   Projection.property("node_id"),
                   Projection.property("ai_summary"),
                   Projection.property("code"),
                   Projection.property("commit"),
               ])
        )
        .returning(["prev"])
    )
    try:
        cur_rows  = _rows(c.query().dynamic(current_batch.to_dynamic_request(_P_NID, {"nid": state_node_id})).send(), "cur")
        prev_rows = _rows(c.query().dynamic(prev_batch.to_dynamic_request(_P_NID, {"nid": state_node_id})).send(), "prev")
        return {
            "current":  cur_rows[0]  if cur_rows  else None,
            "previous": prev_rows[0] if prev_rows else None,
        }
    except Exception as e:
        return {"error": str(e)}


# ── Tool 3: Blast radius ───────────────────────────────────────────────────

_P_FID = define_params({"fid": param.string()})

def trace_blast_radius(function_identity_id: str, depth: int = 3) -> list[dict]:
    """
    Returns all FunctionIdentity nodes that have a CALLS edge pointing TO the
    given function, up to `depth` hops deep.

    Uses repeat().emit_all() — the entire traversal executes inside HelixDB's
    Rust engine as a single request. No client-side looping between hops.
    """
    from helixdb import RepeatConfig, SubTraversal
    c = _c()
    batch = (
        read_batch()
        .var_as("callers",
            g().n_with_label("FunctionIdentity")
               .where(Predicate.eq_param("node_id", "fid"))
               .repeat(
                   RepeatConfig.new(SubTraversal.new().in_("CALLS"))
                               .times(depth)
                               .emit_all()
               )
               .dedup()
               .project([
                   Projection.property("node_id"),
                   Projection.property("name"),
                   Projection.property("file"),
               ])
        )
        .returning(["callers"])
    )
    try:
        result = c.query().dynamic(
            batch.to_dynamic_request(_P_FID, {"fid": function_identity_id})
        ).send()
        # exclude the anchor itself from results
        return [r for r in _rows(result, "callers")
                if r.get("node_id") != function_identity_id]
    except Exception as e:
        return [{"error": str(e)}]


# ── Tool 4: Temporal vulnerability trace ──────────────────────────────────

_P_TVUL = define_params({"fname": param.string(), "ts": param.string()})

def get_temporal_vulnerability_trace(target_func: str, timestamp_iso: str) -> list[dict]:
    """
    Single chained Rust query — no Python loops between hops.

    Path traversed entirely inside HelixDB:
      FunctionIdentity(name=target) <-[CALLS]- FunctionIdentity
                                    -[HAS_STATE]-> FunctionState
                                    <-[GENERATED]- Commit(timestamp < threshold)

    One HTTP request. Rust follows every pointer in memory.
    Returns list of {caller, caller_node_id, state_id, commit_hash, timestamp, msg}.
    """
    c = _c()
    batch = (
        read_batch()
        .var_as("commits",
            g().n_with_label("FunctionIdentity")
               .where(Predicate.eq_param("name", "fname"))
               .in_("CALLS")
               .out("HAS_STATE")
               .in_("GENERATED")
               .where(Predicate.lt_param("timestamp", "ts"))
               .project([
                   Projection.property("node_id"),
                   Projection.property("hash"),
                   Projection.property("timestamp"),
                   Projection.property("msg"),
               ])
        )
        .returning(["commits"])
    )
    try:
        result = c.query().dynamic(
            batch.to_dynamic_request(_P_TVUL, {"fname": target_func, "ts": timestamp_iso})
        ).send()
        return _rows(result, "commits")
    except Exception as e:
        return [{"error": str(e)}]


# ── Tool 5: Edit code ──────────────────────────────────────────────────────

def edit_code(file: str, function_name: str, new_code: str) -> dict:
    """
    Patch a function in the working tree and re-ingest that file into HelixDB.

    Steps:
      1. Validate new_code parses as valid Python.
      2. Find the existing function in the file by AST line numbers.
      3. Replace exactly those lines with new_code.
      4. Write the file back.
      5. Re-run scalable_ingest so the graph reflects the change.

    Returns {status, file, function_name, lines_replaced} or {error}.
    """
    abs_path = os.path.join(REPO_ROOT, file.replace("/", os.sep))
    if not os.path.isfile(abs_path):
        return {"error": f"file not found: {file}"}

    # 1. Validate new_code
    try:
        ast.parse(new_code)
    except SyntaxError as e:
        return {"error": f"new_code syntax error: {e}"}

    # 2. Find function in existing file
    source = open(abs_path, encoding="utf-8").read()
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return {"error": f"existing file unparseable: {e}"}

    target = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == function_name),
        None
    )
    if target is None:
        return {"error": f"function '{function_name}' not found in {file}"}

    lines = source.splitlines(keepends=True)
    start = target.lineno - 1        # ast lineno is 1-based
    end   = target.end_lineno        # slice end is exclusive

    # 3. Detect indentation of the function and normalise new_code to match
    indent = re.match(r"(\s*)", lines[start]).group(1)
    new_lines = []
    for i, ln in enumerate(new_code.splitlines(keepends=True)):
        # first line gets existing indent; subsequent lines keep relative indent
        if i == 0:
            new_lines.append(indent + ln.lstrip())
        else:
            new_lines.append(indent + ln if ln.strip() else ln)
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"

    # 4. Write back
    patched = lines[:start] + new_lines + lines[end:]
    open(abs_path, "w", encoding="utf-8").write("".join(patched))

    # 5. Re-ingest (import fresh to pick up any module changes)
    try:
        import scalable_ingest
        importlib.reload(scalable_ingest)
        scalable_ingest.run_ingestion()
        ingest_status = "ok"
    except Exception as e:
        ingest_status = f"warning: re-ingest failed ({e})"

    return {
        "status":         "patched",
        "file":           file,
        "function_name":  function_name,
        "lines_replaced": f"{start+1}–{end}",
        "ingest":         ingest_status,
    }


if __name__ == "__main__":
    import json
    results = search_code_semantics("user authentication login")
    print(json.dumps(results, indent=2))
