"""
Live MCP test ? starts instrumented server, runs treatment (via HTTP) and
baseline (file/git ops), logs every step, produces comparison.

Usage:
    python eval/live_test.py

Environment:
    REPO_PATH=path/to/dograh  (required for baseline git commands)
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LOG_DIR = Path(__file__).resolve().parent / "report"
LOG_DIR.mkdir(parents=True, exist_ok=True)

REPO_PATH = os.environ.get("REPO_PATH", str(ROOT))

TASKS = [
    {
        "id": "semantic-search-voice",
        "prompt": "voice call handling and audio processing",
        "k": 5,
        "type": "search",
    },
    {
        "id": "explain-campaign-routes",
        "func_a": "dograh:func_api_routes_campaign_resume_campaign",
        "func_b": "dograh:func_api_routes_campaign_start_campaign",
        "type": "explain_coupling",
    },
    {
        "id": "commit-review-pipeline",
        "changed_ids": ["dograh:func_api_services_pipecat_pipeline_builder_build_pipeline"],
        "type": "commit_review",
    },
]


def est_tokens(obj):
    text = json.dumps(obj) if not isinstance(obj, str) else obj
    return len(text) // 4


# -- MCP Client (treatment) ------------------------------------------------

def mcp_call(tool: str, params: dict) -> dict:
    body = json.dumps({"name": tool, "parameters": params}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:7700/call",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    resp = urllib.request.urlopen(req, timeout=60)
    elapsed = time.time() - t0
    result = json.loads(resp.read())
    return {"result": result, "latency_s": round(elapsed, 4)}


def run_treatment(task: dict) -> list[dict]:
    steps = []
    t = task["type"]
    if t == "search":
        mcp_resp = mcp_call("search_code_semantics", {"prompt": task["prompt"], "k": task.get("k", 5)})
        steps.append({
            "tool": "search_code_semantics",
            "params": {"prompt": task["prompt"], "k": task.get("k", 5)},
            "latency_s": mcp_resp["latency_s"],
            "result_summary": _summarize(mcp_resp["result"].get("result", {})),
            "tokens_sent": est_tokens(task["prompt"]),
            "tokens_received": est_tokens(mcp_resp["result"]),
        })
    elif t == "explain_coupling":
        mcp_resp = mcp_call("explain_coupling", {"func_id_a": task["func_a"], "func_id_b": task["func_b"]})
        steps.append({
            "tool": "explain_coupling",
            "params": {"func_a": task["func_a"], "func_b": task["func_b"]},
            "latency_s": mcp_resp["latency_s"],
            "result_summary": _summarize(mcp_resp["result"].get("result", {})),
            "tokens_sent": est_tokens(task["func_a"] + task["func_b"]),
            "tokens_received": est_tokens(mcp_resp["result"]),
        })
    elif t == "commit_review":
        mcp_resp = mcp_call("commit_review", {"changed_function_ids": task["changed_ids"]})
        steps.append({
            "tool": "commit_review",
            "params": {"changed_ids": task["changed_ids"]},
            "latency_s": mcp_resp["latency_s"],
            "result_summary": _summarize(mcp_resp["result"].get("result", [])),
            "tokens_sent": est_tokens(task["changed_ids"]),
            "tokens_received": est_tokens(mcp_resp["result"]),
        })
    return steps


# -- Baseline (file/git ops) ----------------------------------------------?

def _py_grep(pattern: str, root: str) -> list[str]:
    """Pure Python recursive file grep (no rg dependency)."""
    matches = []
    try:
        pat = re.compile(pattern, re.IGNORECASE)
    except re.error:
        pat = re.compile(re.escape(pattern), re.IGNORECASE)
    for p in Path(root).rglob("*.py"):
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            if pat.search(text):
                matches.append(str(p))
        except Exception:
            pass
    return matches


def run_baseline(task: dict) -> list[dict]:
    steps = []
    t = task["type"]
    if t == "search":
        prompt = task["prompt"]
        keywords = [w for w in prompt.split() if len(w) > 3]

        t0 = time.time()
        pattern = "|".join(keywords)
        files = _py_grep(pattern, REPO_PATH)
        elapsed = time.time() - t0
        tokens_scanned = _count_py_tokens()
        steps.append({
            "tool": "grep_files",
            "params": {"keywords": keywords},
            "latency_s": round(elapsed, 4),
            "result_summary": f"{len(files)} files matched",
            "tokens_sent": tokens_scanned,
            "tokens_received": est_tokens(files),
        })

        if files:
            t0 = time.time()
            bodies = []
            for fp in files[:10]:
                try:
                    text = open(fp, encoding="utf-8", errors="replace").read()
                    bodies.append(text)
                except Exception:
                    pass
            elapsed = time.time() - t0
            steps.append({
                "tool": "read_files",
                "params": {"files": files[:10]},
                "latency_s": round(elapsed, 4),
                "result_summary": f"read {len(bodies)} files",
                "tokens_sent": 0,
                "tokens_received": est_tokens("\n".join(bodies)),
            })

    elif t == "explain_coupling":
        def _get_shas(name):
            r = subprocess.run(
                ["git", "-C", REPO_PATH, "log", "--format=%H", "--all", "-S", name],
                capture_output=True, text=True, timeout=30
            )
            return set(l.strip() for l in r.stdout.strip().split("\n") if l.strip())

        t0 = time.time()
        a_shas = set()
        b_shas = set()
        try:
            a_shas = _get_shas(task["func_a"].split("_")[-1])
            b_shas = _get_shas(task["func_b"].split("_")[-1])
        except Exception:
            pass
        elapsed = time.time() - t0
        common = a_shas & b_shas
        jaccard = len(common) / len(a_shas | b_shas) if (a_shas | b_shas) else 0
        steps.append({
            "tool": "git_log_intersect",
            "params": {"func_a": task["func_a"], "func_b": task["func_b"]},
            "latency_s": round(elapsed, 4),
            "result_summary": f"{len(common)} common, jaccard={jaccard:.2f}",
            "tokens_sent": 0,
            "tokens_received": est_tokens(str(common)),
        })

    elif t == "commit_review":
        func_id = task["changed_ids"][0]
        name = func_id.split("_")[-1]

        # grep for function references
        t0 = time.time()
        files = _py_grep(name, REPO_PATH)
        elapsed = time.time() - t0
        tokens_scanned = _count_py_tokens()
        steps.append({
            "tool": "grep_callers",
            "params": {"name": name},
            "latency_s": round(elapsed, 4),
            "result_summary": f"{len(files)} references",
            "tokens_sent": tokens_scanned,
            "tokens_received": est_tokens(files),
        })

        # git log
        t0 = time.time()
        result = subprocess.run(
            ["git", "-C", REPO_PATH, "log", "--oneline", "--all", "-S", name],
            capture_output=True, text=True, timeout=30
        )
        shas = [l.split()[0] for l in result.stdout.strip().split("\n") if l.strip()]
        elapsed = time.time() - t0
        steps.append({
            "tool": "git_log",
            "params": {"search": name},
            "latency_s": round(elapsed, 4),
            "result_summary": f"{len(shas)} commits",
            "tokens_sent": 0,
            "tokens_received": est_tokens(shas),
        })

    return steps


def _count_py_tokens() -> int:
    total = 0
    for p in Path(REPO_PATH).rglob("*.py"):
        if p.is_file():
            try:
                total += len(p.read_text(encoding="utf-8", errors="replace")) // 4
            except Exception:
                pass
    return total


def _summarize(result) -> str:
    if result is None:
        return "None"
    if isinstance(result, dict):
        if "nodes" in result:
            return f"{len(result['nodes'])} nodes, {len(result.get('edges',[]))} edges"
        if "category" in result:
            return f"category={result['category']}, count={result.get('co_change_count',0)}"
    if isinstance(result, list):
        if result and isinstance(result[0], dict):
            return f"{len(result)} items"
        return str(len(result))
    return str(result)[:80]


# -- Runner ----------------------------------------------------------------

def main():
    print("=" * 72)
    print("LIVE MCP TEST ? Real MCP Server vs File-Only Baseline")
    print("=" * 72)
    print()

    all_treatment = []
    all_baseline = []

    # -- Start instrumented MCP server --
    print("Starting instrumented MCP server...")
    server_proc = subprocess.Popen(
        [sys.executable, str(ROOT / "eval" / "mcp_instrumented.py")],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        cwd=str(ROOT),
    )
    time.sleep(8)  # wait for initialization

    # Verify server is up
    try:
        resp = urllib.request.urlopen("http://127.0.0.1:7700/manifest", timeout=5)
        manifest = json.loads(resp.read())
        print(f"  Server OK ? {len(manifest['tools'])} tools available")
    except Exception as e:
        print(f"  Server FAILED: {e}")
        server_proc.kill()
        return

    # -- Run treatment --
    print("\n--- TREATMENT (MCP Server) ---\n")
    for task in TASKS:
        print(f"  [{task['id']}]... ", end="", flush=True)
        steps = run_treatment(task)
        all_treatment.append({"task_id": task["id"], "steps": steps})
        total_s = sum(s["latency_s"] for s in steps)
        tokens = sum(s.get("tokens_sent", 0) + s.get("tokens_received", 0) for s in steps)
        print(f"{len(steps)} calls, {total_s:.2f}s, {tokens} tokens")

    # -- Stop server --
    server_proc.terminate()
    server_proc.wait()
    print("\n  MCP server stopped.")

    # -- Run baseline --
    print("\n--- BASELINE (File/Git Only) ---\n")
    for task in TASKS:
        print(f"  [{task['id']}]... ", end="", flush=True)
        steps = run_baseline(task)
        all_baseline.append({"task_id": task["id"], "steps": steps})
        total_s = sum(s["latency_s"] for s in steps)
        tokens = sum(s.get("tokens_sent", 0) + s.get("tokens_received", 0) for s in steps)
        print(f"{len(steps)} calls, {total_s:.2f}s, {tokens} tokens")

    # -- Generate report --
    print("\n" + "=" * 72)
    print("COMPARISON REPORT")
    print("=" * 72)
    print()

    grand_t_calls = 0
    grand_t_tokens = 0
    grand_t_time = 0.0
    grand_b_calls = 0
    grand_b_tokens = 0
    grand_b_time = 0.0

    for i, task in enumerate(TASKS):
        tid = task["id"]
        t_steps = all_treatment[i]["steps"]
        b_steps = all_baseline[i]["steps"]

        t_calls = len(t_steps)
        t_tokens = sum(s.get("tokens_sent", 0) + s.get("tokens_received", 0) for s in t_steps)
        t_time = sum(s["latency_s"] for s in t_steps)
        b_calls = len(b_steps)
        b_tokens = sum(s.get("tokens_sent", 0) + s.get("tokens_received", 0) for s in b_steps)
        b_time = sum(s["latency_s"] for s in b_steps)

        grand_t_calls += t_calls
        grand_t_tokens += t_tokens
        grand_t_time += t_time
        grand_b_calls += b_calls
        grand_b_tokens += b_tokens
        grand_b_time += b_time

        print(f"-- {tid} --")
        print(f"  {'':30s} {'Baseline':>12s} {'Treatment':>12s}  {'Delta':>8s}")
        print(f"  {'-'*62}")
        print(f"  {'Tool calls':30s} {b_calls:>12d} {t_calls:>12d}  {_pct(b_calls, t_calls):>8s}")
        print(f"  {'Tokens':30s} {b_tokens:>12d} {t_tokens:>12d}  {_pct(b_tokens, t_tokens):>8s}")
        print(f"  {'Latency (s)':30s} {b_time:>12.2f} {t_time:>12.2f}  {_pct(b_time, t_time):>8s}")

        # Step details
        print(f"\n  Baseline steps:")
        for s in b_steps:
            print(f"    {s['tool']:25s} {s['latency_s']:>6.2f}s  "
                  f"S:{s.get('tokens_sent',0):>7d}  R:{s.get('tokens_received',0):>7d}  {s['result_summary'][:50]}")
        print(f"\n  Treatment steps:")
        for s in t_steps:
            print(f"    {s['tool']:25s} {s['latency_s']:>6.2f}s  "
                  f"S:{s.get('tokens_sent',0):>7d}  R:{s.get('tokens_received',0):>7d}  {s['result_summary'][:50]}")
        print()

    print("-- AGGREGATE --")
    print(f"  {'':30s} {'Baseline':>12s} {'Treatment':>12s}  {'Delta':>8s}")
    print(f"  {'-'*62}")
    print(f"  {'Total tool calls':30s} {grand_b_calls:>12d} {grand_t_calls:>12d}  {_pct(grand_b_calls, grand_t_calls):>8s}")
    print(f"  {'Total tokens':30s} {grand_b_tokens:>12d} {grand_t_tokens:>12d}  {_pct(grand_b_tokens, grand_t_tokens):>8s}")
    print(f"  {'Total latency (s)':30s} {grand_b_time:>12.2f} {grand_t_time:>12.2f}  {_pct(grand_b_time, grand_t_time):>8s}")
    print()


def _pct(a, b):
    if a == 0 and b == 0:
        return " 0%"
    if a == 0:
        return "+inf"
    diff = ((b - a) / a) * 100
    if diff < 0:
        return f"-{abs(diff):.0f}%"
    return f"+{diff:.0f}%"


if __name__ == "__main__":
    main()
