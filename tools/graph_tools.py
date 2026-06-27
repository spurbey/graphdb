"""
Agent toolset for the graph knowledge base.

Five tools:
  search_code_semantics(prompt)            — text search on active FunctionState.ai_summary
  get_code_time_travel_diff(state_node_id) — PREVIOUS_VERSION traversal
  trace_blast_radius(function_identity_id) — reverse CALLS traversal
  get_temporal_vulnerability_trace(target_func, timestamp_iso) — multi-hop commit filter
  edit_code(file, function_name, new_code) — patch a function in the working tree + re-ingest
"""

from __future__ import annotations
import sys, os, ast, re, importlib, json, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from helixdb import Client, g, read_batch, define_params, param, Predicate, Projection, PropertyValue

HELIX_URL  = "http://127.0.0.1:6969"
REPO_ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── Embedding helper (same model as scalable_ingest) ──────────────────────────
_EMBED_MODEL = "nvidia/llama-nemotron-embed-vl-1b-v2:free"
_EMBED_DIMS  = 2048

def _load_key() -> str:
    try:
        env = os.path.join(REPO_ROOT, ".env")
        for line in open(env):
            if "=" in line:
                return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return ""

_API_KEY = _load_key()

def _embed(text: str) -> list[float]:
    if not _API_KEY or not text.strip():
        return [0.0] * _EMBED_DIMS
    try:
        payload = json.dumps({"model": _EMBED_MODEL, "input": text[:2000]}).encode()
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/embeddings",
            data=payload,
            headers={"Authorization": f"Bearer {_API_KEY}", "Content-Type": "application/json"},
        )
        return json.loads(urllib.request.urlopen(req, timeout=15).read())["data"][0]["embedding"]
    except Exception:
        return [0.0] * _EMBED_DIMS


def _c() -> Client:
    return Client(HELIX_URL)


def _rows(result: dict, key: str) -> list[dict]:
    return result.get(key, {}).get("properties", [])


# ── Tool 1: Semantic search ────────────────────────────────────────────────

def search_code_semantics(prompt: str, k: int = 5) -> list[dict]:
    """
    Vector similarity search on FunctionState.ai_summary_vec where status == 'active'.
    Embeds the prompt via OpenRouter then queries the HelixDB vector index.
    """
    c = _c()
    vec = _embed(prompt)
    batch = (
        read_batch()
        .var_as("states",
            g().vector_search_nodes("FunctionState", "ai_summary_vec", vec, k)
               .where(Predicate.eq("status", "active"))
               .where(Predicate.is_not_null("ai_summary"))
               .project([
                   Projection.property("node_id"),
                   Projection.property("function_id"),
                   Projection.property("ai_summary"),
                   Projection.property("code"),
               ])
        )
        .returning(["states"])
    )
    try:
        result = c.query().dynamic(batch.to_dynamic_request()).send()
        return _rows(result, "states")
    except Exception as e:
        return [{"error": str(e)}]


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

def trace_blast_radius(function_identity_id: str) -> list[dict]:
    """
    Returns all FunctionIdentity nodes that have a CALLS edge pointing TO the
    given function — i.e. "who calls this function?"
    Returns list of {node_id, name, file} dicts.
    """
    c = _c()
    batch = (
        read_batch()
        .var_as("callers",
            g().n_with_label("FunctionIdentity")
               .where(Predicate.eq_param("node_id", "fid"))
               .in_("CALLS")
               .project([
                   Projection.property("node_id"),
                   Projection.property("name"),
                   Projection.property("file"),
               ])
        )
        .returning(["callers"])
    )
    try:
        result = c.query().dynamic(batch.to_dynamic_request(_P_FID, {"fid": function_identity_id})).send()
        return _rows(result, "callers")
    except Exception as e:
        return [{"error": str(e)}]


# ── Tool 4: Temporal vulnerability trace ──────────────────────────────────

_P_TVUL_CALLERS = define_params({"fname": param.string()})
_P_TVUL_STATE   = define_params({"fid": param.string()})
_P_TVUL_COMMIT  = define_params({"sid": param.string(), "ts": param.string()})

def get_temporal_vulnerability_trace(target_func: str, timestamp_iso: str) -> list[dict]:
    """
    Two-hop Python join (avoids mixed-direction single-batch limitation):
      HOP 1: FunctionIdentity(name=target) <-[CALLS]- FunctionIdentity(callers)
      HOP 2: for each caller → HAS_STATE → FunctionState ← GENERATED ← Commit(ts < threshold)
    Returns list of {caller, caller_node_id, state_id, commit_hash, timestamp, msg}.
    """
    c = _c()

    # HOP 1 — who calls target_func?
    hop1 = (
        read_batch()
        .var_as("callers",
            g().n_with_label("FunctionIdentity")
               .where(Predicate.eq_param("name", "fname"))
               .in_("CALLS")
               .project([Projection.property("node_id"), Projection.property("name")])
        )
        .returning(["callers"])
    )
    try:
        callers = _rows(c.query().dynamic(hop1.to_dynamic_request(_P_TVUL_CALLERS, {"fname": target_func})).send(), "callers")
    except Exception as e:
        return [{"error": f"hop1 failed: {e}"}]

    if not callers:
        return []

    results = []
    for caller in callers:
        fid = caller["node_id"]
        cname = caller.get("name", fid)

        # HOP 2a — caller → HAS_STATE → FunctionState
        hop2a = (
            read_batch()
            .var_as("states",
                g().n_with_label("FunctionIdentity")
                   .where(Predicate.eq_param("node_id", "fid"))
                   .out("HAS_STATE")
                   .project([Projection.property("node_id"), Projection.property("commit"),
                              Projection.property("status"), Projection.property("ai_summary")])
            )
            .returning(["states"])
        )
        try:
            states = _rows(c.query().dynamic(hop2a.to_dynamic_request(_P_TVUL_STATE, {"fid": fid})).send(), "states")
        except Exception:
            continue

        for state in states:
            sid = state["node_id"]
            # HOP 2b — FunctionState ← GENERATED ← Commit, filter by timestamp
            hop2b = (
                read_batch()
                .var_as("commits",
                    g().n_with_label("FunctionState")
                       .where(Predicate.eq_param("node_id", "sid"))
                       .in_("GENERATED")
                       .where(Predicate.lt_param("timestamp", "ts"))
                       .project([Projection.property("node_id"), Projection.property("hash"),
                                 Projection.property("timestamp"), Projection.property("msg")])
                )
                .returning(["commits"])
            )
            _P2 = define_params({"sid": param.string(), "ts": param.string()})
            try:
                commits = _rows(c.query().dynamic(hop2b.to_dynamic_request(_P2, {"sid": sid, "ts": timestamp_iso})).send(), "commits")
            except Exception:
                continue

            for commit in commits:
                results.append({
                    "caller":        cname,
                    "caller_node_id": fid,
                    "state_id":      sid,
                    "state_status":  state.get("status"),
                    "ai_summary":    state.get("ai_summary", ""),
                    "commit_hash":   commit.get("hash", ""),
                    "timestamp":     commit.get("timestamp", ""),
                    "msg":           commit.get("msg", "")[:80],
                })

    return results


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
