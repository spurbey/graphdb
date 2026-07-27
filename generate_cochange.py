"""
Generate CO_CHANGE pairs from git history of any repo.

Usage:
    set REPO_PATH=C:/path/to/repo
    set REPO_NAME=dograh
    set MAX_COMMITS=200
    python generate_cochange.py
"""

import ast
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from sandbox.cochange_analysis import add_commit_pairs, analyze_cochanges, pair_key

REPO_PATH = os.environ.get("REPO_PATH", ".")
REPO_NAME = os.environ.get("REPO_NAME", Path(REPO_PATH).resolve().name)
N_COMMITS = int(os.environ.get("MAX_COMMITS", "0"))
SKIP_FILES = {"__init__.py", "__main__.py", "conftest.py"}


def _nid(template, *args):
    return f"{REPO_NAME}:{template.format(*args)}"


def _safe_file(path):
    return path.replace("\\", "/").replace("/", "_").replace(".py", "")


def extract_functions(source, file_path):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    results = []
    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            calls = []
            for n in ast.walk(node):
                if isinstance(n, ast.Call):
                    if isinstance(n.func, ast.Name):
                        calls.append(n.func.id)
                    elif isinstance(n.func, ast.Attribute):
                        calls.append(n.func.attr)
            results.append({
                "name": node.name,
                "code": ast.unparse(node)[:3000],
                "calls": list(set(calls)),
            })
        visit_AsyncFunctionDef = visit_FunctionDef
    Visitor().visit(tree)
    return results


def code_hash(code):
    return hashlib.sha1(code.encode("utf-8")).hexdigest()[:12]


def main():
    # Use native git log (much faster than gitpython iter_commits + per-commit diff)
    log_cmd = [
        "git", "-C", REPO_PATH, "log",
        "--name-only", "--pretty=format:%H||%at||%s",
        "--diff-filter=AM",  # Added or Modified
    ]
    import subprocess
    result = subprocess.check_output(log_cmd, encoding="utf-8", errors="replace")
    blocks = result.strip().split("\n\n")
    commits_info = []
    for block in blocks:
        lines = block.strip().split("\n")
        header = lines[0]
        if "||" not in header:
            continue
        sha, ts, msg = header.split("||", 2)
        h = sha[:7]
        files = [f.replace("\\", "/") for f in lines[1:] if f.strip()]
        py_files = [
            f for f in files
            if f.endswith(".py") and f.split("/")[-1] not in SKIP_FILES
            and not any(p.startswith(".") for p in f.split("/"))
        ]
        if py_files:
            commits_info.append((sha, h, py_files, int(ts), msg.strip()))

    if N_COMMITS > 0:
        commits_info = commits_info[-N_COMMITS:]

    total = len(commits_info)
    print(f"Walking {total} commits with .py changes ({REPO_NAME})...")

    pair_commits = defaultdict(set)
    function_touch_commits = defaultdict(set)
    function_files = {}
    commit_changed_functions = {}
    commit_timestamps = {}
    commit_messages = {}
    calls_registry = defaultdict(set)
    file_tracker = {}

    def _read_blob(sha, file_path):
        try:
            return subprocess.check_output(
                ["git", "-C", REPO_PATH, "show", f"{sha}:{file_path}"],
                encoding="utf-8", errors="replace"
            )
        except subprocess.CalledProcessError:
            return ""

    t0 = time.time()
    for i, (sha, h, py_files, ts, msg) in enumerate(commits_info):
        commit_timestamps[h] = str(ts)
        commit_messages[h] = msg
        actual_changed = set()

        for file_path in py_files:
            source = _read_blob(sha, file_path)
            if not source:
                continue

            safe = _safe_file(file_path)
            prev_funcs = file_tracker.get(file_path, {})
            cur_funcs = {}

            for func in extract_functions(source, file_path):
                nid = _nid("func_{}_{}", safe, func["name"])
                fh = code_hash(func["code"])
                cur_funcs[nid] = fh
                function_files[nid] = file_path
                if prev_funcs.get(nid) != fh:
                    actual_changed.add(nid)
                    function_touch_commits[nid].add(h)
                    calls_registry[nid] = set(func["calls"])

            file_tracker[file_path] = cur_funcs

        commit_changed_functions[h] = actual_changed
        if len(actual_changed) >= 2:
            add_commit_pairs(pair_commits, actual_changed, h)

        if (i + 1) % max(1, total // 10) == 0:
            print(f"  [{i+1}/{total}] {h} — {len(function_files)} unique functions")

    t1 = time.time()
    print(f"Pass 1 done: {len(function_files)} functions, {len(pair_commits)} candidate pairs in {t1-t0:.1f}s")

    if not pair_commits:
        print("No co-change candidate pairs found. Nothing to do.")
        return

    # Build CALLS pairs from latest registry
    all_func_names = {nid.split("_")[-1]: nid for nid in function_files}
    calls_pairs = set()
    for nid, callees in calls_registry.items():
        for callee_name in callees:
            target = all_func_names.get(callee_name)
            if target and target != nid:
                calls_pairs.add(pair_key(nid, target))

    print(f"Running co-change analysis ({len(pair_commits)} pairs)...")
    cochange = analyze_cochanges(
        pair_commits=pair_commits,
        function_touch_commits=function_touch_commits,
        function_files=function_files,
        calls_pairs=calls_pairs,
        import_pairs=set(),
        commit_changed_functions=commit_changed_functions,
        commit_timestamps=commit_timestamps,
        commit_messages=commit_messages,
    )

    t2 = time.time()
    print(f"Analysis done: {len(cochange['edges'])} CO_CHANGE edges in {t2-t1:.1f}s")

    out_dir = ROOT / "sandbox"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / f"{REPO_NAME}_cochange"
    with open(f"{base}_pairs.json", "w", encoding="utf-8") as f:
        json.dump(cochange["pairs"], f, indent=2)
    with open(f"{base}_themes.json", "w", encoding="utf-8") as f:
        json.dump(cochange["themes"], f, indent=2)
    with open(f"{base}_thresholds.json", "w", encoding="utf-8") as f:
        json.dump(cochange["threshold_report"], f, indent=2)

    print(f"Written:")
    print(f"  {base}_pairs.json ({len(cochange['pairs'])} pairs)")
    print(f"  {base}_themes.json ({len(cochange['themes'])} themes)")
    print(f"  {base}_thresholds.json")
    print(f"Total time: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
