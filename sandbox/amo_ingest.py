"""
amo_ingest.py — Cold Discovery Pipeline ingestion for agent-memory-orchestrator.

Processes the first 100 commits of the AMO repo and produces:
  nodes.json  — FunctionIdentity nodes with code + ai_summary + embedding (active only)
  edges.json  — CALLS, IMPORTS, CO_CHANGE edges with co_change_count

Embedding strategy:
  - Graph structure (all nodes/edges): every function across all 100 commits
  - Embeddings: only active functions at commit 100 (saves API calls)

Usage:
    cd graphdb
    python amo_ingest.py
"""

import ast
import hashlib
import json
import os
import re
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import git

try:
    from sandbox.cochange_analysis import add_commit_pairs
    from sandbox.cochange_analysis import analyze_cochanges
    from sandbox.cochange_analysis import pair_key
except ModuleNotFoundError:
    from cochange_analysis import add_commit_pairs
    from cochange_analysis import analyze_cochanges
    from cochange_analysis import pair_key

# ── Config ────────────────────────────────────────────────────────────────────
AMO_REPO_PATH = str(Path(__file__).resolve().parents[2] / "agent-memory-orchestrator")
N_COMMITS     = 100
EMBED_MODEL   = "nvidia/llama-nemotron-embed-vl-1b-v2:free"
EMBED_DIMS    = 2048
OUT_NODES     = "sandbox/amo_nodes.json"
OUT_EDGES     = "sandbox/amo_edges.json"
OUT_COCHANGE_PAIRS = "sandbox/amo_cochange_pairs.json"
OUT_COCHANGE_THEMES = "sandbox/amo_cochange_themes.json"
OUT_COCHANGE_THRESHOLDS = "sandbox/amo_cochange_thresholds.json"
OUT_EMBED_CACHE = "sandbox/amo_embedding_cache.json"
EMBED_DELAY   = 0.2
# Set AMO_SKIP_EMBEDDINGS=1 to regenerate co-change artifacts without
# spending embedding API calls.  Existing non-zero embeddings from
# amo_nodes.json are preserved; nodes without a cached embedding get zeros.
SKIP_EMBEDDINGS = os.environ.get("AMO_SKIP_EMBEDDINGS", "").lower() in {"1", "true", "yes"}

# ── Load API key from graphdb/.env ────────────────────────────────────────────
def _load_key() -> str:
    preferred_names = ("llm_api_key_2", "llm_api_key2", "llm_api_key")
    env_candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[1] / ".env",
        Path.cwd().parent / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    ]
    for env_path in env_candidates:
        if not env_path.exists():
            continue
        values: dict[str, str] = {}
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                continue
            name, value = line.split("=", 1)
            values[name.strip().lower()] = value.strip()
        for name in preferred_names:
            if values.get(name):
                return values[name]
    return ""

API_KEY = _load_key()


# ── Embedding ─────────────────────────────────────────────────────────────────
def summary_hash(summary: str) -> str:
    return hashlib.sha1(summary.encode("utf-8")).hexdigest()[:16]


def embedding_cache_key(node_id_value: str, summary: str) -> str:
    return f"{EMBED_MODEL}|{node_id_value}|{summary_hash(summary)}"


def is_nonzero_embedding(vec: list[float] | tuple[float, ...]) -> bool:
    return len(vec) == EMBED_DIMS and any(abs(float(value)) > 1e-12 for value in vec)


def _empty_embedding_cache() -> dict:
    return {
        "model": EMBED_MODEL,
        "dim": EMBED_DIMS,
        "items": {},
    }


def load_embedding_cache() -> dict:
    path = Path(OUT_EMBED_CACHE)
    if not path.exists():
        return _empty_embedding_cache()
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _empty_embedding_cache()
    if cache.get("model") != EMBED_MODEL or cache.get("dim") != EMBED_DIMS:
        return _empty_embedding_cache()
    cache.setdefault("items", {})
    return cache


def save_embedding_cache(cache: dict) -> None:
    Path(OUT_EMBED_CACHE).write_text(json.dumps(cache, indent=2), encoding="utf-8")


def seed_embedding_cache_from_nodes(cache: dict, nodes_path: str = OUT_NODES) -> int:
    path = Path(nodes_path)
    if not path.exists():
        return 0
    try:
        nodes = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0

    seeded = 0
    items = cache.setdefault("items", {})
    for node in nodes:
        if node.get("status") != "active":
            continue
        summary = node.get("text_summary", "")
        embedding = node.get("embedding") or []
        if not summary or not is_nonzero_embedding(embedding):
            continue
        key = embedding_cache_key(node["id"], summary)
        if key not in items:
            items[key] = {
                "node_id": node["id"],
                "summary_hash": summary_hash(summary),
                "embedding": embedding,
            }
            seeded += 1
    return seeded


def load_existing_node_embeddings(nodes_path: str = OUT_NODES) -> dict[str, list[float]]:
    """Return {node_id: embedding} for active nodes that already have a non-zero embedding."""
    path = Path(nodes_path)
    if not path.exists():
        return {}
    try:
        nodes = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    result: dict[str, list[float]] = {}
    for node in nodes:
        emb = node.get("embedding") or []
        if node.get("status") == "active" and is_nonzero_embedding(emb):
            result[node["id"]] = emb
    return result


def cached_embed(node_id_value: str, summary: str, cache: dict) -> tuple[list[float], str]:
    key = embedding_cache_key(node_id_value, summary)
    items = cache.setdefault("items", {})
    entry = items.get(key)
    if entry and is_nonzero_embedding(entry.get("embedding") or []):
        return entry["embedding"], "cache"

    vec = embed(summary)
    if is_nonzero_embedding(vec):
        items[key] = {
            "node_id": node_id_value,
            "summary_hash": summary_hash(summary),
            "embedding": vec,
        }
        return vec, "api"
    return vec, "zero"


def embed(text: str) -> list[float]:
    if not API_KEY or not text.strip():
        return [0.0] * EMBED_DIMS
    try:
        payload = json.dumps({"model": EMBED_MODEL, "input": text[:2000]}).encode()
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/embeddings",
            data=payload,
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=20).read())
        return resp["data"][0]["embedding"]
    except Exception as e:
        print(f"  [embed warn] {e}")
        return [0.0] * EMBED_DIMS


# ── AST helpers ───────────────────────────────────────────────────────────────
def extract_functions(source: str, file_path: str) -> list[dict]:
    """Return list of {name, code, calls, lineno} dicts for all functions in source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    results = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.current_func = None

        def visit_FunctionDef(self, node):
            prev = self.current_func
            self.current_func = node.name
            calls = []
            for n in ast.walk(node):
                if isinstance(n, ast.Call):
                    if isinstance(n.func, ast.Name):
                        calls.append(n.func.id)
                    elif isinstance(n.func, ast.Attribute):
                        calls.append(n.func.attr)
            # get docstring as summary hint
            docstring = ast.get_docstring(node) or ""
            results.append({
                "name":      node.name,
                "code":      ast.unparse(node)[:3000],
                "calls":     list(set(calls)),
                "lineno":    node.lineno,
                "docstring": docstring[:300],
                "file":      file_path,
            })
            self.generic_visit(node)
            self.current_func = prev

        visit_AsyncFunctionDef = visit_FunctionDef

    Visitor().visit(tree)
    return results


def extract_imports(source: str, file_path: str) -> list[str]:
    """Return list of imported module names."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module.split(".")[0])
    return mods


def node_id(file_path: str, func_name: str) -> str:
    return f"{file_path}::{func_name}"


def code_hash(code: str) -> str:
    return hashlib.sha1(code.encode("utf-8")).hexdigest()[:12]


def summarise(func: dict) -> str:
    """Build a short mechanical summary from AST — used if no docstring."""
    if func["docstring"]:
        return func["docstring"].splitlines()[0][:200]
    calls = func["calls"][:5]
    parts = [f"`{func['name']}`"]
    if calls:
        parts.append(f"calls {', '.join(calls)}")
    parts.append("returns a value" if "return" in func["code"] else "no explicit return")
    return " — ".join(parts)


# ── Main ingestion ─────────────────────────────────────────────────────────────
def run():
    repo = git.Repo(AMO_REPO_PATH)
    all_commits = list(repo.iter_commits("HEAD"))
    all_commits.reverse()                          # oldest first
    first_100 = all_commits[:N_COMMITS]
    commit_100 = first_100[-1]                     # HEAD of the slice

    print(f"Processing {len(first_100)} commits: {first_100[0].hexsha[:7]} -> {commit_100.hexsha[:7]}")
    print(f"Active state will be from: [{commit_100.hexsha[:7]}] {commit_100.message.strip()[:60]}")
    print()

    # ── Pass 1: build function registry across all commits ────────────────────
    # func_versions[node_id] = list of {commit, code, summary}
    func_versions: dict[str, list[dict]] = defaultdict(list)
    # calls_registry[node_id] = set of callee names (latest seen)
    calls_registry: dict[str, set[str]] = defaultdict(set)
    # file_imports[file] = set of imported modules
    file_imports: dict[str, set[str]] = defaultdict(set)
    file_latest_funcs: dict[str, dict[str, str]] = {}
    function_files: dict[str, str] = {}
    function_touch_commits: dict[str, set[str]] = defaultdict(set)
    commit_changed_functions: dict[str, set[str]] = {}
    pair_commits: dict[tuple[str, str], set[str]] = defaultdict(set)
    commit_timestamps: dict[str, str] = {}
    commit_messages: dict[str, str] = {}

    SKIP = {"__pycache__", ".pyc"}

    print("Pass 1: extracting functions from all commits...")
    for i, commit in enumerate(first_100):
        h = commit.hexsha[:7]
        changed = (
            list(commit.stats.files.keys()) if not commit.parents
            else [d.b_path for d in commit.parents[0].diff(commit) if d.b_path]
        )
        py_changed = [f for f in changed if f.endswith(".py") and not any(s in f for s in SKIP)]

        commit_timestamps[h] = commit.committed_datetime.isoformat()
        commit_messages[h] = commit.message.strip()
        actual_changed: set[str] = set()

        for file_path in py_changed:
            try:
                blob   = commit.tree / file_path
                source = blob.data_stream.read().decode("utf-8", errors="replace")
            except (KeyError, AttributeError):
                source = ""

            funcs   = extract_functions(source, file_path) if source else []
            imports = extract_imports(source, file_path) if source else []
            if imports:
                file_imports[file_path].update(imports)

            previous_hashes = file_latest_funcs.get(file_path, {})
            current_hashes: dict[str, str] = {}

            for func in funcs:
                nid     = node_id(file_path, func["name"])
                f_hash  = code_hash(func["code"])
                summary = summarise(func)
                current_hashes[nid] = f_hash
                function_files[nid] = file_path
                if previous_hashes.get(nid) != f_hash:
                    actual_changed.add(nid)
                    function_touch_commits[nid].add(h)
                    func_versions[nid].append({
                        "commit":  h,
                        "code":    func["code"],
                        "code_hash": f_hash,
                        "summary": summary,
                        "calls":   func["calls"],
                        "file":    file_path,
                        "name":    func["name"],
                    })
                    calls_registry[nid] = set(func["calls"])

            removed = set(previous_hashes) - set(current_hashes)
            for nid in removed:
                actual_changed.add(nid)
                function_touch_commits[nid].add(h)

            file_latest_funcs[file_path] = current_hashes

        commit_changed_functions[h] = actual_changed
        if len(actual_changed) >= 2:
            add_commit_pairs(pair_commits, actual_changed, h)

        if (i + 1) % 10 == 0:
            print(f"  [{i+1}/100] {h} — {len(func_versions)} unique functions so far")

    print(f"\nPass 1 done. {len(func_versions)} unique function identities found.")

    # ── Pass 2: determine active functions at commit 100 ─────────────────────
    active_files = set(repo.git.ls_tree("-r", "--name-only", commit_100.hexsha).splitlines())
    active_py    = {f for f in active_files if f.endswith(".py")}

    active_funcs: dict[str, dict] = {}
    for file_path in active_py:
        try:
            blob   = commit_100.tree / file_path
            source = blob.data_stream.read().decode("utf-8", errors="replace")
        except (KeyError, AttributeError):
            continue
        for func in extract_functions(source, file_path):
            nid = node_id(file_path, func["name"])
            active_funcs[nid] = func

    print(f"Active functions at commit 100: {len(active_funcs)}")

    # ── Pass 3: batch embed active functions ──────────────────────────────────
    print(f"\nPass 3: embedding {len(active_funcs)} active functions...")
    if SKIP_EMBEDDINGS:
        print("  AMO_SKIP_EMBEDDINGS=1 — preserving existing embeddings, skipping API calls.")
        existing = load_existing_node_embeddings()
        embeddings: dict[str, list[float]] = {}
        skipped = preserved = 0
        for nid in active_funcs:
            if nid in existing:
                embeddings[nid] = existing[nid]
                preserved += 1
            else:
                embeddings[nid] = [0.0] * EMBED_DIMS
                skipped += 1
        embedding_cache = load_embedding_cache()
        print(f"  Preserved {preserved} existing embeddings, zeroed {skipped} without cache.")
        embedding_stats: Counter[str] = Counter({"cache": preserved, "zero": skipped})
    else:
        print(f"  Model: {EMBED_MODEL}  (free tier, {EMBED_DELAY}s delay between calls)")

        embedding_cache = load_embedding_cache()
        seeded = seed_embedding_cache_from_nodes(embedding_cache)
        if seeded:
            print(f"  Seeded embedding cache from existing nodes: {seeded}")
        else:
            print("  No non-zero existing node embeddings found to seed cache")

        embeddings: dict[str, list[float]] = {}
        embedding_stats: Counter[str] = Counter()
        total = len(active_funcs)
        for idx, (nid, func) in enumerate(active_funcs.items()):
            summary = summarise(func)
            vec, source = cached_embed(nid, summary, embedding_cache)
            embeddings[nid] = vec
            embedding_stats[source] += 1
            if (idx + 1) % 50 == 0:
                print(
                    f"  embedded {idx+1}/{total} "
                    f"(cache={embedding_stats['cache']}, api={embedding_stats['api']}, zero={embedding_stats['zero']})..."
                )
            if source == "api":
                time.sleep(EMBED_DELAY)

        save_embedding_cache(embedding_cache)
        print(
            f"  Done. {len(embeddings)} embeddings ready "
            f"(cache={embedding_stats['cache']}, api={embedding_stats['api']}, zero={embedding_stats['zero']})."
        )
        print(f"  Cache entries: {len(embedding_cache.get('items', {}))} -> {OUT_EMBED_CACHE}")

    # ── Build nodes.json ──────────────────────────────────────────────────────
    print("\nBuilding nodes.json...")
    nodes = []

    # Active nodes — full data + embedding
    for nid, func in active_funcs.items():
        summary = summarise(func)
        versions = func_versions.get(nid, [])
        nodes.append({
            "id":           nid,
            "label":        "FunctionIdentity",
            "file":         func["file"],
            "name":         func["name"],
            "code":         func["code"][:3000],
            "text_summary": summary,
            "embedding":    embeddings.get(nid, [0.0] * EMBED_DIMS),
            "status":       "active",
            "version_count": len(versions),
        })

    # Superseded nodes — graph structure only, no embedding
    for nid, versions in func_versions.items():
        if nid not in active_funcs:
            last = versions[-1]
            nodes.append({
                "id":           nid,
                "label":        "FunctionIdentity",
                "file":         last["file"],
                "name":         last["name"],
                "code":         last["code"][:3000],
                "text_summary": last["summary"],
                "embedding":    [],           # not embedded — superseded
                "status":       "superseded",
                "version_count": len(versions),
            })

    print(f"  {len(nodes)} total nodes ({len(active_funcs)} active, {len(nodes)-len(active_funcs)} superseded)")

    # ── Build edges.json ──────────────────────────────────────────────────────
    print("Building edges.json...")
    edges = []
    edge_seen = set()

    # CALLS edges
    all_func_names = {nid.split("::")[-1]: nid for nid in func_versions}
    calls_pairs: set[tuple[str, str]] = set()
    for nid, callees in calls_registry.items():
        for callee_name in callees:
            if callee_name in all_func_names:
                target_nid = all_func_names[callee_name]
                if target_nid != nid:
                    key = (nid, target_nid, "CALLS")
                    if key not in edge_seen:
                        edge_seen.add(key)
                        edges.append({
                            "source":           nid,
                            "target":           target_nid,
                            "type":             "CALLS",
                            "co_change_count":  0,
                            "ast_relation_type": "direct_call",
                        })
                        calls_pairs.add(pair_key(nid, target_nid))

    # File/function lookup used by IMPORTS edges and co-change explanations.
    func_by_file: dict[str, list[str]] = defaultdict(list)
    for nid in func_versions:
        fpath = function_files.get(nid, nid.split("::")[0])
        func_by_file[fpath].append(nid)

    # IMPORTS edges (file → file, mapped to functions)
    import_pairs: set[tuple[str, str]] = set()
    for file_path, imported_mods in file_imports.items():
        for mod in imported_mods:
            # find files matching this module name
            for other_file in active_py:
                if Path(other_file).stem == mod or other_file.endswith(f"/{mod}.py"):
                    if file_path != other_file:
                        import_pairs.add(pair_key(file_path, other_file))
                    for nid_a in func_by_file.get(file_path, []):
                        for nid_b in func_by_file.get(other_file, []):
                            key = (nid_a, nid_b, "IMPORTS")
                            if key not in edge_seen:
                                edge_seen.add(key)
                                edges.append({
                                    "source":            nid_a,
                                    "target":            nid_b,
                                    "type":              "IMPORTS",
                                    "co_change_count":   0,
                                    "ast_relation_type": "import",
                                })

    cochange = analyze_cochanges(
        pair_commits=pair_commits,
        function_touch_commits=function_touch_commits,
        function_files=function_files,
        calls_pairs=calls_pairs,
        import_pairs=import_pairs,
        commit_changed_functions=commit_changed_functions,
        commit_timestamps=commit_timestamps,
        commit_messages=commit_messages,
    )
    edges.extend(cochange["edges"])

    print(f"  {len(edges)} edges ({sum(1 for e in edges if e['type']=='CALLS')} CALLS, "
          f"{sum(1 for e in edges if e['type']=='CO_CHANGE')} CO_CHANGE, "
          f"{sum(1 for e in edges if e['type']=='IMPORTS')} IMPORTS)")

    # ── Write output ──────────────────────────────────────────────────────────
    with open(OUT_NODES, "w", encoding="utf-8") as f:
        json.dump(nodes, f, indent=2)
    with open(OUT_EDGES, "w", encoding="utf-8") as f:
        json.dump(edges, f, indent=2)
    with open(OUT_COCHANGE_PAIRS, "w", encoding="utf-8") as f:
        json.dump(cochange["pairs"], f, indent=2)
    with open(OUT_COCHANGE_THEMES, "w", encoding="utf-8") as f:
        json.dump(cochange["themes"], f, indent=2)
    with open(OUT_COCHANGE_THRESHOLDS, "w", encoding="utf-8") as f:
        json.dump(cochange["threshold_report"], f, indent=2)

    print(f"\nWritten: {OUT_NODES} ({len(nodes)} nodes), {OUT_EDGES} ({len(edges)} edges)")
    print(
        "Co-change reports: "
        f"{OUT_COCHANGE_PAIRS} ({len(cochange['pairs'])} pairs), "
        f"{OUT_COCHANGE_THEMES} ({len(cochange['themes'])} themes), "
        f"{OUT_COCHANGE_THRESHOLDS}"
    )
    print("Done.")


if __name__ == "__main__":
    run()
