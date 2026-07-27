import json
import time
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pipeline_api
from eval.metrics.token_counter import count

_initialized = False


def _ensure_init():
    global _initialized
    if not _initialized:
        pipeline_api.initialize(source="helixdb")
        _initialized = True


def run(task: dict) -> dict:
    _ensure_init()

    steps_log = []
    total_tokens_sent = 0
    total_tokens_received = 0
    total_time = 0.0
    answer = {}

    for tool_name in task.get("treatment_tools", []):
        t0 = time.time()
        tokens_sent = 0
        tokens_received = 0
        result = None
        error = None

        try:
            if tool_name == "search_code_semantics":
                prompt = task["prompt"]
                k = task.get("k", 5)
                tokens_sent = count(prompt)
                result = pipeline_api.search(prompt, k=k)
                tokens_received = count(json.dumps(result))
                answer["search_result"] = result

            elif tool_name == "explain_coupling":
                func_a = task["func_a"]
                func_b = task["func_b"]
                tokens_sent = count(func_a + func_b)
                result = pipeline_api.explain_coupling(func_a, func_b)
                tokens_received = count(json.dumps(result) if result else "null")
                answer["coupling_result"] = result

            elif tool_name == "commit_review":
                changed_ids = task.get("changed_ids", [])
                tokens_sent = count(str(changed_ids))
                result = pipeline_api.commit_review(changed_ids)
                tokens_received = count(json.dumps(result))
                answer["review_result"] = result

        except Exception as e:
            error = str(e)
            result = {"error": error}

        elapsed = time.time() - t0
        steps_log.append({
            "tool": tool_name,
            "latency_s": round(elapsed, 3),
            "tokens_sent": tokens_sent,
            "tokens_received": tokens_received,
            "result_summary": _summarize(result, tool_name),
            "error": error,
        })
        total_tokens_sent += tokens_sent
        total_tokens_received += tokens_received
        total_time += elapsed

    return {
        "condition": "treatment",
        "steps": steps_log,
        "total_tokens_sent": total_tokens_sent,
        "total_tokens_received": total_tokens_received,
        "total_time_s": round(total_time, 3),
        "answer": answer,
    }


def _summarize(result, tool_name: str) -> str:
    if result is None:
        return "None"
    if isinstance(result, dict) and "error" in result:
        return f"error: {result['error'][:60]}"
    if tool_name == "search_code_semantics":
        nodes = len(result.get("nodes", []))
        edges = len(result.get("edges", []))
        return f"{nodes} nodes, {edges} edges"
    if tool_name == "explain_coupling":
        if isinstance(result, dict):
            cat = result.get("category", result.get("ast_relation_type", "?"))
            cnt = result.get("co_change_count", result.get("occurrence_count", 0))
            return f"category={cat}, count={cnt}"
        return str(result)[:80]
    if tool_name == "commit_review":
        if isinstance(result, list):
            return f"{len(result)} functions reviewed"
        return str(result)[:80]
    return str(result)[:80]
