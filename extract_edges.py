"""
Quick edge extractor — generates graph_payload.json with CALLS/IMPORTS/INHERITS edges
that match HelixDB's dograh node IDs. Uses Python ast (no tree-sitter).
"""

import ast
import json
import os
from collections import defaultdict
from pathlib import Path

REPO_PATH = os.environ.get("REPO_PATH", ".")
REPO_NAME = os.environ.get("REPO_NAME", Path(REPO_PATH).resolve().name)
SKIP_FILES = {"__init__.py", "__main__.py", "conftest.py"}


def _nid(template: str, *args) -> str:
    return f"{REPO_NAME}:{template.format(*args)}"


def _safe_file(path: str) -> str:
    return path.replace("\\", "/").replace("/", "_").replace(".py", "")


def _extract(source: str, file_path: str):
    """Return (name_to_nid, calls_list, inherits_list, imports_set)"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}, [], [], set()

    name_to_nid: dict[str, str] = {}
    calls: list[tuple[str, str]] = []
    inherits: list[tuple[str, str]] = []
    imports: set[str] = set()

    class Visitor(ast.NodeVisitor):
        current_func = None
        current_class = None

        def visit_FunctionDef(self, node):
            prev_func = self.current_func
            self.current_func = node.name
            nid = _nid("func_{}_{}", _safe_file(file_path), node.name)
            name_to_nid[node.name] = nid

            for n in ast.walk(node):
                if isinstance(n, ast.Call):
                    if isinstance(n.func, ast.Name):
                        calls.append((node.name, n.func.id))
                    elif isinstance(n.func, ast.Attribute):
                        calls.append((node.name, n.func.attr))

            self.generic_visit(node)
            self.current_func = prev_func

        def visit_AsyncFunctionDef(self, node):
            self.visit_FunctionDef(node)

        def visit_ClassDef(self, node):
            prev_class = self.current_class
            self.current_class = node.name
            for base in node.bases:
                if isinstance(base, ast.Name):
                    inherits.append((node.name, base.id))
            self.generic_visit(node)
            self.current_class = prev_class

        def visit_Import(self, node):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])

        def visit_ImportFrom(self, node):
            if node.module:
                imports.add(node.module.split(".")[0])

    Visitor().visit(tree)
    return name_to_nid, calls, inherits, imports


def main():
    root = Path(REPO_PATH).resolve()
    py_files = sorted(root.rglob("*.py"))
    py_files = [f for f in py_files
                if f.name not in SKIP_FILES
                and not any(p.startswith(".") or p == "__pycache__" for p in f.relative_to(root).parts)]

    print(f"Scanning {len(py_files)} Python files in {root}...")

    all_name_to_nid: dict[str, str] = {}
    func_to_file: dict[str, str] = {}
    file_funcs: dict[str, list[str]] = defaultdict(list)
    all_calls: list[tuple[str, str]] = []
    all_inherits: list[tuple[str, str]] = []
    file_imports: dict[str, set[str]] = defaultdict(set)

    for fp in py_files:
        rel = str(fp.relative_to(root)).replace("\\", "/")
        try:
            source = fp.read_text("utf-8")
        except Exception:
            continue

        name_to_nid, calls, inherits, imports = _extract(source, rel)
        all_name_to_nid.update(name_to_nid)
        all_calls.extend(calls)
        all_inherits.extend(inherits)
        file_imports[rel] = imports
        for name, nid in name_to_nid.items():
            func_to_file[nid] = rel
            file_funcs[rel].append(nid)

    print(f"  {len(all_name_to_nid)} functions found")

    # ── CALLS edges ──────────────────────────────────────────────────────────
    edges = []
    edge_seen: set[tuple] = set()

    for caller_name, callee_name in all_calls:
        if caller_name == callee_name:
            continue
        caller_nid = all_name_to_nid.get(caller_name)
        callee_nid = all_name_to_nid.get(callee_name)
        if caller_nid and callee_nid:
            key = (caller_nid, callee_nid, "CALLS")
            if key not in edge_seen:
                edge_seen.add(key)
                edges.append({"from": caller_nid, "label": "CALLS", "to": callee_nid})

    print(f"  {sum(1 for e in edges if e['label']=='CALLS')} CALLS edges")

    # ── IMPORTS edges (module-to-module, mapped to functions) ────────────────
    mod_to_files: dict[str, list[str]] = defaultdict(list)
    for fp in py_files:
        rel = str(fp.relative_to(root)).replace("\\", "/")
        mod_name = fp.stem
        mod_to_files[mod_name].append(rel)

    for file_path, imps in file_imports.items():
        for mod in imps:
            for other_file in mod_to_files.get(mod, []):
                if file_path != other_file:
                    for nid_a in file_funcs.get(file_path, []):
                        for nid_b in file_funcs.get(other_file, []):
                            key = (nid_a, nid_b, "IMPORTS")
                            if key not in edge_seen:
                                edge_seen.add(key)
                                edges.append({"from": nid_a, "label": "IMPORTS", "to": nid_b})

    print(f"  {sum(1 for e in edges if e['label']=='IMPORTS')} IMPORTS edges")

    # ── INHERITS edges ───────────────────────────────────────────────────────
    for child_name, parent_name in all_inherits:
        child_nid = all_name_to_nid.get(child_name)
        parent_nid = all_name_to_nid.get(parent_name)
        if child_nid and parent_nid:
            key = (child_nid, parent_nid, "INHERITS")
            if key not in edge_seen:
                edge_seen.add(key)
                edges.append({"from": child_nid, "label": "INHERITS", "to": parent_nid})

    print(f"  {sum(1 for e in edges if e['label']=='INHERITS')} INHERITS edges")

    # ── Write graph_payload.json ─────────────────────────────────────────────
    out_path = Path.cwd() / "graph_payload.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"nodes": [], "edges": edges}, f, indent=2)

    print(f"\nWritten {out_path} ({len(edges)} total edges)")


if __name__ == "__main__":
    main()
