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
import re
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

import git

# ── Config ────────────────────────────────────────────────────────────────────
AMO_REPO_PATH = "../../agent-memory-orchestrator"
N_COMMITS     = 100
EMBED_MODEL   = "nvidia/llama-nemotron-embed-vl-1b-v2:free"
EMBED_DIMS    = 2048
OUT_NODES     = "sandbox/amo_nodes.json"
OUT_EDGES     = "sandbox/amo_edges.json"
EMBED_DELAY   = 0.2

# ── Load API key from graphdb/.env ────────────────────────────────────────────
def _load_key() -> str:
    try:
        for line in open("../.env"):
            if "llm_api_key" in line.lower() and "=" in line and "2" not in line.split("=")[0]:
                return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return ""

API_KEY = _load_key()


# ── Embedding ─────────────────────────────────────────────────────────────────
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
    # co_change[file_a][file_b] = count of commits where both changed
    file_cochange: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    # calls_registry[node_id] = set of callee names (latest seen)
    calls_registry: dict[str, set[str]] = defaultdict(set)
    # file_imports[file] = set of imported modules
    file_imports: dict[str, set[str]] = defaultdict(set)

    SKIP = {"__pycache__", ".pyc"}

    print("Pass 1: extracting functions from all commits...")
    for i, commit in enumerate(first_100):
        h = commit.hexsha[:7]
        changed = (
            list(commit.stats.files.keys()) if not commit.parents
            else [d.b_path for d in commit.parents[0].diff(commit) if d.b_path]
        )
        py_changed = [f for f in changed if f.endswith(".py") and not any(s in f for s in SKIP)]

        if py_changed:
            # co-change: every pair of py files changed together
            for a in py_changed:
                for b in py_changed:
                    if a != b:
                        file_cochange[a][b] += 1

        for file_path in py_changed:
            try:
                blob   = commit.tree / file_path
                source = blob.data_stream.read().decode("utf-8", errors="replace")
            except (KeyError, AttributeError):
                continue

            funcs   = extract_functions(source, file_path)
            imports = extract_imports(source, file_path)
            file_imports[file_path].update(imports)

            for func in funcs:
                nid     = node_id(file_path, func["name"])
                summary = summarise(func)
                func_versions[nid].append({
                    "commit":  h,
                    "code":    func["code"],
                    "summary": summary,
                    "calls":   func["calls"],
                    "file":    file_path,
                    "name":    func["name"],
                })
                calls_registry[nid].update(func["calls"])

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
    print(f"  Model: {EMBED_MODEL}  (free tier, {EMBED_DELAY}s delay between calls)")

    embeddings: dict[str, list[float]] = {}
    total = len(active_funcs)
    for idx, (nid, func) in enumerate(active_funcs.items()):
        summary = summarise(func)
        vec = embed(summary)
        embeddings[nid] = vec
        if (idx + 1) % 50 == 0:
            print(f"  embedded {idx+1}/{total}...")
        time.sleep(EMBED_DELAY)

    print(f"  Done. {len(embeddings)} embeddings computed.")

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

    # CO_CHANGE edges (file-level, propagated to all functions in those files)
    func_by_file: dict[str, list[str]] = defaultdict(list)
    for nid in func_versions:
        fpath = nid.split("::")[0]
        func_by_file[fpath].append(nid)

    for file_a, neighbors in file_cochange.items():
        for file_b, count in neighbors.items():
            if count < 2:           # only meaningful co-changes
                continue
            for nid_a in func_by_file.get(file_a, []):
                for nid_b in func_by_file.get(file_b, []):
                    key = (nid_a, nid_b, "CO_CHANGE")
                    if key not in edge_seen:
                        edge_seen.add(key)
                        edges.append({
                            "source":            nid_a,
                            "target":            nid_b,
                            "type":              "CO_CHANGE",
                            "co_change_count":   count,
                            "ast_relation_type": "co_change",
                        })

    # IMPORTS edges (file → file, mapped to functions)
    for file_path, imported_mods in file_imports.items():
        for mod in imported_mods:
            # find files matching this module name
            for other_file in active_py:
                if Path(other_file).stem == mod or other_file.endswith(f"/{mod}.py"):
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

    print(f"  {len(edges)} edges ({sum(1 for e in edges if e['type']=='CALLS')} CALLS, "
          f"{sum(1 for e in edges if e['type']=='CO_CHANGE')} CO_CHANGE, "
          f"{sum(1 for e in edges if e['type']=='IMPORTS')} IMPORTS)")

    # ── Write output ──────────────────────────────────────────────────────────
    with open(OUT_NODES, "w", encoding="utf-8") as f:
        json.dump(nodes, f, indent=2)
    with open(OUT_EDGES, "w", encoding="utf-8") as f:
        json.dump(edges, f, indent=2)

    print(f"\nWritten: {OUT_NODES} ({len(nodes)} nodes), {OUT_EDGES} ({len(edges)} edges)")
    print("Done.")


if __name__ == "__main__":
    run()
