"""
eval/runner.py — Automated Evaluation Loop

Usage:
    python eval/runner.py

Runs each task in eval_config.json through two conditions:
    A) Baseline — simulated grep-only agent
    B) Treatment — real pipeline_api tool calls

Outputs comparison report to eval/report/latest_report.txt
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.metrics import ground_truth
from eval.conditions import baseline
from eval.conditions import treatment
from eval.report import reporter


def load_tasks(path: str | Path = None) -> list[dict]:
    if path is None:
        path = Path(__file__).resolve().parent / "eval_config.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run_all(tasks: list[dict]) -> list[dict]:
    records = []
    n = len(tasks)

    for i, task in enumerate(tasks):
        tid = task["id"]
        print(f"[{i+1}/{n}] {tid} ({task['capability']})...")
        sys.stdout.flush()

        t0 = time.time()

        try:
            result_b = baseline.run(task)
        except Exception as e:
            print(f"  Baseline error: {e}")
            result_b = {"condition": "baseline", "steps": [], "total_tokens_read": 0,
                        "total_tokens_written": 0, "total_time_s": 0, "answer": {}}

        try:
            result_t = treatment.run(task)
        except Exception as e:
            print(f"  Treatment error: {e}")
            result_t = {"condition": "treatment", "steps": [], "total_tokens_sent": 0,
                        "total_tokens_received": 0, "total_time_s": 0, "answer": {}}

        elapsed = time.time() - t0

        baseline_answer = result_b.get("answer", {})
        treatment_answer = result_t.get("answer", {})

        baseline_correct = ground_truth.check(task, _extract_answer(task, result_b), "baseline")
        treatment_correct = ground_truth.check(task, _extract_answer(task, result_t), "treatment")

        records.append({
            "task_id": tid,
            "capability": task["capability"],
            "baseline": result_b,
            "treatment": result_t,
            "baseline_correct": baseline_correct.get("pass", False),
            "treatment_correct": treatment_correct.get("pass", False),
            "baseline_check_details": baseline_correct,
            "treatment_check_details": treatment_correct,
            "total_elapsed_s": round(elapsed, 2),
        })

        b_score = "PASS" if baseline_correct.get("pass") else "FAIL"
        t_score = "PASS" if treatment_correct.get("pass") else "FAIL"
        print(f"  Baseline: {b_score}  Treatment: {t_score}  ({elapsed:.1f}s)")

    return records


def _extract_answer(task: dict, result: dict) -> dict:
    """Extract the answer from a condition result for validation.

    Treatment returns tool-shaped answers (search_result, coupling_result, etc).
    Baseline returns answer_parts dict from simulated steps.
    Both get reshaped into a common validation format.
    """
    answer = result.get("answer", {})
    cap = task["capability"]
    cond = result.get("condition", "treatment")

    if cond == "treatment":
        if cap == "semantic_search":
            return answer.get("search_result", {})
        elif cap == "explain_coupling":
            return answer.get("coupling_result")
        elif cap == "commit_review":
            return answer.get("review_result", [])
        return answer

    # Baseline: reshape answer_parts into tool-shaped output
    if cap == "semantic_search":
        grep_files = answer.get("grep_files", [])
        func_names = answer.get("func_names", [])
        return {
            "nodes": [{"id": f, "name": f.split("::")[-1]} for f in func_names],
            "edges": [],
        }
    elif cap == "explain_coupling":
        jaccard = answer.get("jaccard", 0.0)
        intersected = answer.get("intersected", [])
        return {
            "category": "shared_commit_only" if len(intersected) >= 3 else "unknown",
            "co_change_count": len(intersected),
            "jaccard": jaccard,
            "occurrence_count": len(intersected),
        }
    elif cap == "commit_review":
        callers = answer.get("callers", {})
        return [
            {
                "function_id": task.get("changed_ids", [""])[0],
                "severity": 0.1 if callers else 0,
                "blast_radius": list(set(
                    f for d in callers.values() for f in d.get("files", [])
                )),
            }
        ]
    return answer


def main():
    tasks = load_tasks()
    print(f"Loaded {len(tasks)} evaluation tasks")
    print()

    records = run_all(tasks)

    report = reporter.generate(records)
    print()
    print(report)

    out_path = Path(__file__).resolve().parent / "report" / "latest_report.txt"
    reporter.write_to_file(report, str(out_path))


if __name__ == "__main__":
    main()
