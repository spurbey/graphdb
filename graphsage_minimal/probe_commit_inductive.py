from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import git
import numpy as np
import torch

from train_graphsage import GraphSAGE


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "graphsage_minimal" / "data"
OUT = ROOT / "graphsage_minimal" / "out"
AMO_REPO = ROOT.parent / "agent-memory-orchestrator"

EMBED_MODEL = "nvidia/llama-nemotron-embed-vl-1b-v2:free"
EMBED_DIMS = 2048
AMBIGUOUS_CALL_NAMES = {
    "all",
    "any",
    "append",
    "as_dict",
    "close",
    "connect",
    "dumps",
    "float",
    "get",
    "int",
    "items",
    "join",
    "keys",
    "len",
    "list",
    "lower",
    "max",
    "min",
    "replace",
    "round",
    "sort",
    "str",
    "strip",
    "sub",
    "tuple",
    "values",
}


@dataclass
class FunctionInfo:
    node_id: str
    file: str
    name: str
    code: str
    code_hash: str
    calls: list[str]
    summary: str


def load_api_key() -> str:
    for name in ("OPENROUTER_API_KEY", "LLM_API_KEY", "llm_api_key"):
        value = os.environ.get(name)
        if value:
            return value.strip()
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().lower()
            if key in {"openrouter_api_key", "llm_api_key", "api_key"} or "api" in key:
                return value.strip()
    return ""


def embed_summary(text: str, api_key: str) -> np.ndarray:
    if not api_key or not text.strip():
        return np.zeros(EMBED_DIMS, dtype=np.float32)
    payload = json.dumps({"model": EMBED_MODEL, "input": text[:2000]}).encode("utf-8")
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/embeddings",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    response = json.loads(urllib.request.urlopen(request, timeout=30).read())
    return np.asarray(response["data"][0]["embedding"], dtype=np.float32)


def code_hash(code: str) -> str:
    return hashlib.sha1(code.encode("utf-8")).hexdigest()[:12]


def node_id(file_path: str, func_name: str) -> str:
    return f"{file_path}::{func_name}"


def summarize(func: dict) -> str:
    if func["docstring"]:
        return func["docstring"].splitlines()[0][:200]
    calls = func["calls"][:5]
    parts = [f"`{func['name']}`"]
    if calls:
        parts.append(f"calls {', '.join(calls)}")
    parts.append("returns a value" if "return" in func["code"] else "no explicit return")
    return " - ".join(parts)


def extract_functions(source: str, file_path: str) -> dict[str, FunctionInfo]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}

    functions: dict[str, FunctionInfo] = {}

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            calls = []
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Name):
                        calls.append(child.func.id)
                    elif isinstance(child.func, ast.Attribute):
                        calls.append(child.func.attr)
            code = ast.unparse(node)[:3000]
            row = {
                "name": node.name,
                "code": code,
                "calls": sorted(set(calls)),
                "docstring": (ast.get_docstring(node) or "")[:300],
            }
            fid = node_id(file_path, node.name)
            functions[fid] = FunctionInfo(
                node_id=fid,
                file=file_path,
                name=node.name,
                code=code,
                code_hash=code_hash(code),
                calls=row["calls"],
                summary=summarize(row),
            )
            self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

    Visitor().visit(tree)
    return functions


def file_functions_at(repo: git.Repo, commit_sha: str, file_path: str) -> dict[str, FunctionInfo]:
    try:
        blob = repo.commit(commit_sha).tree / file_path
    except KeyError:
        return {}
    source = blob.data_stream.read().decode("utf-8", errors="replace")
    return extract_functions(source, file_path)


def changed_src_py_files(repo: git.Repo, commit_sha: str) -> list[str]:
    commit = repo.commit(commit_sha)
    parent = commit.parents[0]
    files = []
    for diff in parent.diff(commit):
        path = diff.b_path or diff.a_path
        if path and path.endswith(".py") and path.startswith("src/"):
            files.append(path)
    return sorted(set(files))


def find_first_code_commit_after(repo: git.Repo, base_count: int) -> str:
    commits = list(repo.iter_commits("HEAD"))
    commits.reverse()
    for commit in commits[base_count:]:
        if changed_src_py_files(repo, commit.hexsha):
            return commit.hexsha
    raise RuntimeError("No code commit found after base slice")


def transform_raw_embedding(raw: np.ndarray, feature_mean: np.ndarray) -> np.ndarray:
    vec = raw.reshape(1, -1).astype(np.float32) - feature_mean
    vec = vec / np.maximum(np.linalg.norm(vec, axis=1, keepdims=True), 1e-8)
    return vec[0]


def mirror_edges(edge_index: torch.Tensor) -> torch.Tensor:
    return torch.cat([edge_index, edge_index.flip(0)], dim=1)


def cosine_scores(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    q = query / max(np.linalg.norm(query), 1e-8)
    m = matrix / np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-8)
    return m @ q


def top_neighbors(scores: np.ndarray, meta: list[dict], exclude: set[int], limit: int = 8) -> list[dict]:
    rows = []
    for idx in np.argsort(-scores):
        idx = int(idx)
        if idx in exclude:
            continue
        item = meta[idx]
        rows.append({
            "idx": idx,
            "score": float(scores[idx]),
            "name": item["name"],
            "file": item["file"],
        })
        if len(rows) >= limit:
            break
    return rows


def resolve_call_targets(
    *,
    call_name: str,
    caller_file: str,
    name_to_indices: dict[str, list[int]],
    meta: list[dict],
) -> list[int]:
    matches = name_to_indices.get(call_name, [])
    if not matches:
        return []

    same_file = [idx for idx in matches if meta[idx]["file"] == caller_file]
    if same_file:
        return same_file

    if call_name in AMBIGUOUS_CALL_NAMES:
        return []

    if len(matches) == 1:
        return matches

    # If the name is duplicated across modules and we cannot prove the import
    # target from this lightweight probe, skip it instead of creating false edges.
    return []


def instantiate_model(state: dict[str, torch.Tensor], in_dim: int) -> GraphSAGE:
    hidden_dim = state["layer1.self_linear.weight"].shape[0]
    out_dim = state["layer2.self_linear.weight"].shape[0]
    model = GraphSAGE(in_dim, hidden_dim, out_dim, dropout=0.0)
    model.load_state_dict(state)
    model.eval()
    return model


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-count", type=int, default=100)
    parser.add_argument("--commit", default="")
    args = parser.parse_args()

    repo = git.Repo(AMO_REPO)
    commits = list(repo.iter_commits("HEAD"))
    commits.reverse()
    base_commit = commits[args.base_count - 1].hexsha
    probe_commit = args.commit or find_first_code_commit_after(repo, args.base_count)
    probe = repo.commit(probe_commit)
    parent = probe.parents[0].hexsha
    files = changed_src_py_files(repo, probe_commit)

    arr = np.load(DATA / "amo_calls_active.npz")
    base_x = arr["x"].astype(np.float32)
    feature_mean = arr["feature_mean"].astype(np.float32)
    base_edges = arr["edge_index"].astype(np.int64)
    meta = json.loads((DATA / "node_meta.json").read_text(encoding="utf-8"))
    id_to_idx = {row["id"]: row["idx"] for row in meta}
    name_to_indices: dict[str, list[int]] = {}
    for row in meta:
        name_to_indices.setdefault(row["name"], []).append(row["idx"])

    changed: list[dict] = []
    for file_path in files:
        before = file_functions_at(repo, parent, file_path)
        after = file_functions_at(repo, probe_commit, file_path)
        for fid, func in after.items():
            prev = before.get(fid)
            if prev is None:
                status = "new"
            elif prev.code_hash != func.code_hash:
                status = "changed"
            else:
                continue
            changed.append({"status": status, "function": func})

    if not changed:
        raise SystemExit(f"No changed/new src functions found in {probe.hexsha[:7]}")

    api_key = load_api_key()
    x_aug = [base_x[i] for i in range(base_x.shape[0])]
    meta_aug = list(meta)
    changed_indices = []

    for item in changed:
        func = item["function"]
        raw = embed_summary(func.summary, api_key)
        transformed = transform_raw_embedding(raw, feature_mean)
        existing_idx = id_to_idx.get(func.node_id)
        if existing_idx is None:
            idx = len(x_aug)
            x_aug.append(transformed)
            meta_aug.append({
                "idx": idx,
                "id": func.node_id,
                "name": func.name,
                "file": func.file,
                "version_count": 1,
            })
            id_to_idx[func.node_id] = idx
            name_to_indices.setdefault(func.name, []).append(idx)
        else:
            idx = existing_idx
            x_aug[idx] = transformed
        item["idx"] = idx
        item["summary"] = func.summary
        changed_indices.append(idx)

    edge_pairs = {tuple(sorted((int(a), int(b)))) for a, b in base_edges.T if int(a) != int(b)}
    resolved_calls: dict[int, list[dict]] = {}
    for item in changed:
        func = item["function"]
        src_idx = item["idx"]
        resolved = []
        for call_name in func.calls:
            targets = resolve_call_targets(
                call_name=call_name,
                caller_file=func.file,
                name_to_indices=name_to_indices,
                meta=meta_aug,
            )
            for target_idx in targets:
                if target_idx == src_idx:
                    continue
                edge_pairs.add(tuple(sorted((src_idx, int(target_idx)))))
                target_meta = meta_aug[int(target_idx)]
                resolved.append({
                    "call": call_name,
                    "target_idx": int(target_idx),
                    "target_id": target_meta["id"],
                    "target_file": target_meta["file"],
                })
        resolved_calls[src_idx] = resolved

    edge_index_aug = np.array(sorted(edge_pairs), dtype=np.int64).T
    x_aug_np = np.vstack(x_aug).astype(np.float32)

    state = torch.load(OUT / "graphsage_state.pt", map_location="cpu")
    model = instantiate_model(state, in_dim=base_x.shape[1])

    with torch.no_grad():
        base_z = model(
            torch.from_numpy(base_x),
            mirror_edges(torch.from_numpy(base_edges).long()),
        ).numpy()
        aug_z = model(
            torch.from_numpy(x_aug_np),
            mirror_edges(torch.from_numpy(edge_index_aug).long()),
        ).numpy()

    report = {
        "base_commit": base_commit[:7],
        "probe_commit": probe.hexsha[:7],
        "probe_subject": probe.message.strip().splitlines()[0],
        "changed_src_files": files,
        "base_nodes": int(base_x.shape[0]),
        "augmented_nodes": int(x_aug_np.shape[0]),
        "base_edges": int(base_edges.shape[1]),
        "augmented_edges": int(edge_index_aug.shape[1]),
        "functions": [],
    }

    for item in changed:
        func = item["function"]
        idx = item["idx"]
        raw_scores = cosine_scores(x_aug_np[idx], x_aug_np)
        sage_scores = cosine_scores(aug_z[idx], aug_z)
        row = {
            "status": item["status"],
            "idx": int(idx),
            "id": func.node_id,
            "name": func.name,
            "file": func.file,
            "summary": item["summary"],
            "calls": func.calls,
            "resolved_calls": resolved_calls.get(idx, [])[:20],
            "raw_vector_neighbors": top_neighbors(raw_scores, meta_aug, {idx}),
            "graphsage_neighbors": top_neighbors(sage_scores, meta_aug, {idx}),
        }
        if item["status"] == "changed" and idx < base_z.shape[0]:
            old_raw = base_x[idx]
            row["raw_vector_drift_cosine"] = float(cosine_scores(x_aug_np[idx], old_raw.reshape(1, -1))[0])
            row["graphsage_drift_cosine"] = float(cosine_scores(aug_z[idx], base_z[idx].reshape(1, -1))[0])
            row["old_graphsage_neighbors"] = top_neighbors(
                cosine_scores(base_z[idx], base_z),
                meta,
                {idx},
            )
        report["functions"].append(row)

    OUT.mkdir(parents=True, exist_ok=True)
    out_path = OUT / f"inductive_probe_{probe.hexsha[:7]}.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"Base commit:  {report['base_commit']}")
    print(f"Probe commit: {report['probe_commit']} {report['probe_subject']}")
    print(f"Changed files: {len(files)}")
    print(f"Changed/new functions: {len(report['functions'])}")
    print(f"Augmented graph: {report['base_nodes']} -> {report['augmented_nodes']} nodes, {report['base_edges']} -> {report['augmented_edges']} edges")
    print()
    for row in report["functions"][:10]:
        print(f"{row['status'].upper()} {row['id']}")
        if "raw_vector_drift_cosine" in row:
            print(f"  raw drift cosine: {row['raw_vector_drift_cosine']:.3f}")
            print(f"  graphsage drift cosine: {row['graphsage_drift_cosine']:.3f}")
        print("  calls resolved:", len(row["resolved_calls"]))
        print("  raw top3:", ", ".join(f"{n['name']}({n['score']:.2f})" for n in row["raw_vector_neighbors"][:3]))
        print("  sage top3:", ", ".join(f"{n['name']}({n['score']:.2f})" for n in row["graphsage_neighbors"][:3]))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
