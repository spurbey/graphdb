import ast
import json
import os
import subprocess
import time
from collections import defaultdict
from pathlib import Path

from eval.metrics.token_counter import count, count_file, count_lines

ROOT = Path(__file__).resolve().parents[2]
REPO_PATH = os.environ.get("REPO_PATH", str(ROOT))


def _run_grep(pattern: str, glob: str) -> list[str]:
    result = subprocess.run(
        ["rg", "-l", "--glob", glob, pattern, REPO_PATH],
        capture_output=True, text=True, timeout=30
    )
    files = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
    return files


def _read_file_headers(file_paths: list[str], n_lines: int = 30) -> list[str]:
    lines = []
    for fp in file_paths[:20]:
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    if i >= n_lines:
                        break
                    lines.append(f"{Path(fp).name}:{i+1}: {line.rstrip()}")
        except Exception:
            pass
    return lines


def _extract_function_names(file_paths: list[str]) -> list[str]:
    names = []
    for fp in file_paths[:20]:
        try:
            tree = ast.parse(open(fp, encoding="utf-8", errors="replace").read())
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.append(f"{Path(fp).name}::{node.name}")
        except SyntaxError:
            pass
    return names


def _grep_git_log(pattern: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", REPO_PATH, "log", "--oneline", "--all", "--grep", pattern],
        capture_output=True, text=True, timeout=30
    )
    if not result.stdout.strip():
        result = subprocess.run(
            ["git", "-C", REPO_PATH, "log", "--oneline", "--all", "-S", pattern],
            capture_output=True, text=True, timeout=30
        )
    shas = [l.split()[0] for l in result.stdout.strip().split("\n") if l.strip()]
    return shas


def _intersect_commits(set_a: list[str], set_b: list[str]) -> list[str]:
    return list(set(set_a) & set(set_b))


def _compute_jaccard(set_a: list[str], set_b: list[str]) -> float:
    a, b = set(set_a), set(set_b)
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def _read_commit_messages(shas: list[str], max_messages: int = 10) -> list[str]:
    msgs = []
    for sha in shas[:max_messages]:
        result = subprocess.run(
            ["git", "-C", REPO_PATH, "log", "--format=%B", "-1", sha],
            capture_output=True, text=True, timeout=10
        )
        msgs.append(f"{sha}: {result.stdout.strip()[:200]}")
    return msgs


def _find_callers_recursive(func_name: str, depth: int = 3) -> dict:
    found = {}
    current = {func_name}
    for d in range(depth):
        next_set = set()
        for name in current:
            result = subprocess.run(
                ["rg", "-l", name, "--glob", "**/*.py", REPO_PATH],
                capture_output=True, text=True, timeout=30
            )
            files = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
            found[f"depth_{d}"] = {"function": name, "files": files}
            for fp in files:
                next_set.add(Path(fp).stem)
        current = next_set - {f.split("::")[-1] for f in found}
        if not current:
            break
    return found


def _read_function_bodies(file_paths: list[str], max_functions: int = 20) -> list[str]:
    bodies = []
    count = 0
    for fp in file_paths:
        if count >= max_functions:
            break
        try:
            tree = ast.parse(open(fp, encoding="utf-8", errors="replace").read())
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    bodies.append(f"{Path(fp).name}::{node.name}:\n{ast.unparse(node)[:500]}")
                    count += 1
                    if count >= max_functions:
                        break
        except SyntaxError:
            pass
    return bodies


def run(task: dict) -> dict:
    steps_log = []
    total_tokens_read = 0
    total_tokens_written = 0
    total_time = 0.0
    answer_parts = {}

    for step in task.get("baseline_steps", []):
        t0 = time.time()
        result = None
        tokens_step_read = 0
        tokens_step_written = 0

        action = step["action"]
        try:
            if action == "grep_files":
                files = _run_grep(step["pattern"], step.get("glob", "**/*.py"))
                tokens_step_read = count_files_scanned(step.get("glob", "**/*.py"))
                tokens_step_written = count(str(files))
                result = files
                answer_parts["grep_files"] = files

            elif action == "read_file_headers":
                files = answer_parts.get("grep_files", [])
                headers = _read_file_headers(files)
                tokens_step_read = sum(count_file(f) for f in files[:20])
                tokens_step_written = count_lines(headers)
                result = headers

            elif action == "extract_function_names":
                files = answer_parts.get("grep_files", [])
                names = _extract_function_names(files)
                tokens_step_written = count_lines(names)
                result = names
                answer_parts["func_names"] = names

            elif action == "read_function_bodies":
                files = answer_parts.get("grep_files", [])
                bodies = _read_function_bodies(files, step.get("max_functions", 20))
                tokens_step_read = sum(count_file(f) for f in files)
                tokens_step_written = count_lines(bodies)
                result = bodies

            elif action == "grep_git_log":
                shas = _grep_git_log(step["pattern"])
                tokens_step_read = count(f"git log --grep {step['pattern']}")
                tokens_step_written = count(str(shas))
                result = shas
                answer_parts[f"git_log_{step['pattern'][:15]}"] = shas

            elif action == "extract_commit_shas":
                shas = answer_parts.get("grep_git_log", [])
                if not shas:
                    for k, v in answer_parts.items():
                        if k.startswith("git_log_"):
                            shas = v
                            break
                tokens_step_written = count(str(shas))
                result = shas

            elif action == "intersect_commits":
                logs = [v for k, v in answer_parts.items() if k.startswith("git_log_")]
                if len(logs) >= 2:
                    common = _intersect_commits(logs[0], logs[1])
                else:
                    common = []
                tokens_step_written = count(str(common))
                result = common
                answer_parts["intersected"] = common

            elif action == "compute_jaccard":
                logs = [v for k, v in answer_parts.items() if k.startswith("git_log_")]
                if len(logs) >= 2:
                    j = _compute_jaccard(logs[0], logs[1])
                else:
                    j = 0.0
                tokens_step_written = count(str(j))
                result = {"jaccard": j}
                answer_parts["jaccard"] = j

            elif action == "read_commit_messages":
                shas = answer_parts.get("intersected", [])
                msgs = _read_commit_messages(shas, step.get("max_messages", 10))
                tokens_step_written = count_lines(msgs)
                result = msgs

            elif action == "find_callers_recursive":
                func_name = step.get("func_name", "")
                if not func_name and "changed_ids" in task:
                    full_id = task["changed_ids"][0]
                    parts = full_id.rsplit("_", 1)
                    func_name = parts[-1] if len(parts) > 1 else full_id
                callers = _find_callers_recursive(func_name, step.get("depth", 3))
                tokens_step_read = count_files_scanned("**/*.py")
                tokens_step_written = count(json.dumps(callers))
                result = callers
                answer_parts["callers"] = callers

            elif action == "read_each_caller_code":
                callers = answer_parts.get("callers", {})
                all_files = set()
                for depth_key, data in callers.items():
                    all_files.update(data.get("files", []))
                bodies = _read_function_bodies(list(all_files), 30)
                tokens_step_read = sum(count_file(f) for f in all_files)
                tokens_step_written = count_lines(bodies)
                result = bodies

        except Exception as e:
            result = {"error": str(e)}

        elapsed = time.time() - t0
        steps_log.append({
            "step": action,
            "latency_s": round(elapsed, 3),
            "tokens_read": tokens_step_read,
            "tokens_written": tokens_step_written,
            "result_summary": _summarize(result),
        })
        total_tokens_read += tokens_step_read
        total_tokens_written += tokens_step_written
        total_time += elapsed

    return {
        "condition": "baseline",
        "steps": steps_log,
        "total_tokens_read": total_tokens_read,
        "total_tokens_written": total_tokens_written,
        "total_time_s": round(total_time, 3),
        "answer": answer_parts,
    }


def _summarize(obj) -> str:
    if isinstance(obj, list):
        return f"{len(obj)} items"
    if isinstance(obj, dict):
        return f"{len(obj)} keys"
    if isinstance(obj, str):
        return obj[:80]
    return str(obj)[:80]


def count_files_scanned(glob_pattern: str) -> int:
    """Estimate tokens in all Python files under REPO_PATH."""
    total = 0
    for p in Path(REPO_PATH).rglob("*.py"):
        if p.is_file():
            total += count_file(p)
    return total
