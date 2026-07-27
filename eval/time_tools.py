"""
Starts instrumented MCP server, calls every tool once, records latency.
Exits with FAIL if any tool > 60s.
"""

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TOOLS_TO_TEST = [
    {"name": "search_code_semantics", "params": {"prompt": "voice call handling", "k": 3}},
    {"name": "search_code_semantics_helix", "params": {"prompt": "voice call handling", "k": 3}},
    {"name": "explain_coupling", "params": {"func_id_a": "dograh:func_api_routes_campaign_resume_campaign", "func_id_b": "dograh:func_api_routes_campaign_start_campaign"}},
    {"name": "pipeline_status", "params": {}},
    {"name": "commit_review", "params": {"changed_function_ids": ["dograh:func_api_services_pipecat_pipeline_builder_build_pipeline"]}},
    {"name": "select_tests", "params": {"changed_function_ids": ["dograh:func_api_services_pipecat_pipeline_builder_build_pipeline"]}},
    {"name": "trace_blast_radius", "params": {"function_identity_id": "dograh:func_api_services_pipecat_pipeline_builder_build_pipeline", "depth": 2}},
    {"name": "find_structural_siblings", "params": {"func_id": "dograh:func_api_services_pipecat_pipeline_builder_build_pipeline", "k": 5}},
    {"name": "trace_semantic_evolution", "params": {"func_id": "dograh:func_api_services_pipecat_pipeline_builder_build_pipeline"}},
]


def mcp_call(tool, params):
    body = json.dumps({"name": tool, "parameters": params}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:7700/call", data=body,
        headers={"Content-Type": "application/json"}
    )
    t0 = time.time()
    resp = urllib.request.urlopen(req, timeout=120)
    elapsed = time.time() - t0
    result = json.loads(resp.read())
    return result, elapsed


def main():
    print("Starting MCP server...")
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "tools" / "graph_mcp_server.py")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=str(ROOT),
    )
    time.sleep(10)

    # Verify
    try:
        urllib.request.urlopen("http://127.0.0.1:7700/manifest", timeout=5)
    except Exception:
        print("Server failed to start")
        proc.kill()
        return 1

    print(f"{'Tool':45s} {'Status':8s} {'Latency':8s}  {'Summary'}")
    print("-" * 80)

    all_ok = True
    for t in TOOLS_TO_TEST:
        try:
            result, elapsed = mcp_call(t["name"], t["params"])
            status = "OK" if elapsed < 60 else "SLOW"
            summary = str(result.get("result", ""))[:50] if "result" in result else str(result)[:50]
            if elapsed >= 60:
                all_ok = False
        except Exception as e:
            status = "ERR"
            elapsed = -1
            summary = str(e)[:50]
            all_ok = False

        print(f"{t['name']:45s} {status:8s} {elapsed:>6.2f}s  {summary}")

    proc.terminate()
    proc.wait()

    if all_ok:
        print("\nAll tools under 60s.")
        return 0
    else:
        print("\nSome tools exceeded 60s or failed.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
