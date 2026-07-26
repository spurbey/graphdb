"""
MCP stdio bridge for graphdb HTTP MCP server.

Translates standard MCP JSON-RPC (over stdio) to the graphdb custom HTTP protocol.
OpenCode connects via stdio; the bridge spawns graph_mcp_server.py and proxies calls.

Usage in opencode.json:
    "graphdb": {
        "type": "local",
        "command": ["python", "mcp_stdio_bridge.py"]
    }
"""

from __future__ import annotations

import atexit
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SERVER_URL = "http://127.0.0.1:7700"
_server_proc: subprocess.Popen | None = None
_BRIDGE_DIR = Path(__file__).resolve().parent


def _start_server() -> subprocess.Popen:
    global _server_proc
    if _server_proc and _server_proc.poll() is None:
        return _server_proc
    server_script = str(_BRIDGE_DIR / "tools" / "graph_mcp_server.py")
    proc = subprocess.Popen(
        [sys.executable, server_script],
        cwd=str(_BRIDGE_DIR),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    _server_proc = proc
    atexit.register(lambda: proc.kill() if proc.poll() is None else None)
    return proc


def _server_responds() -> bool:
    try:
        urllib.request.urlopen(f"{SERVER_URL}/manifest", timeout=2)
        return True
    except Exception:
        return False


def _wait_for_server(max_retries: int = 40, delay: float = 1.0) -> bool:
    for i in range(max_retries):
        try:
            urllib.request.urlopen(f"{SERVER_URL}/manifest", timeout=2)
            return True
        except Exception:
            if _server_proc and _server_proc.poll() is not None:
                stderr = _server_proc.stderr.read() if _server_proc.stderr else b""
                print(f"[bridge] server exited early: {stderr.decode()[:200]}", file=sys.stderr)
                return False
            time.sleep(delay)
    return False


def _ensure_server() -> bool:
    if _server_responds():
        return True
    _start_server()
    return _wait_for_server()


def _http_get(path: str) -> dict | list:
    if not _ensure_server():
        return {"error": "graphdb HTTP server failed to start"}
    try:
        resp = urllib.request.urlopen(f"{SERVER_URL}{path}", timeout=10)
        return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def _http_post(path: str, body: dict) -> dict | list:
    if not _ensure_server():
        return {"error": "graphdb HTTP server failed to start"}
    try:
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{SERVER_URL}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=110)
        return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def _send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_id = req.get("id")
        method = req.get("method", "")

        if method == "initialize":
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "graphdb", "version": "1.0.0"},
                    },
                }
            )

        elif method == "ping":
            _send({"jsonrpc": "2.0", "id": msg_id, "result": {}})

        elif method == "notifications/initialized":
            pass

        elif method == "tools/list":
            manifest = _http_get("/manifest")
            raw_tools = manifest.get("tools", []) if isinstance(manifest, dict) else []
            mcp_tools = []
            for t in raw_tools:
                mcp_tools.append(
                    {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "inputSchema": t.get(
                            "parameters", {"type": "object", "properties": {}}
                        ),
                    }
                )
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"tools": mcp_tools},
                }
            )

        elif method == "tools/call":
            params = req.get("params", {})
            name = params.get("name", "")
            arguments = params.get("arguments", {})
            result = _http_post("/call", {"name": name, "parameters": arguments})
            payload = result.get("result", result) if isinstance(result, dict) else result
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(payload, indent=2),
                            }
                        ]
                    },
                }
            )

        else:
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {method}",
                    },
                }
            )


if __name__ == "__main__":
    main()
