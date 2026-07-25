"""
helix_to_igraph.py — Bridge HelixDB data into igraph JSON format.

Reads graph_payload.json (from scalable_ingest.py) for structural edges and
FunctionIdentity metadata.  Queries HelixDB for active FunctionState embeddings.

Produces sandbox/amo_nodes.json + sandbox/amo_edges.json compatible with the
igraph cold-discovery pipeline (igraph_sandbox.py).

Usage:
    cd /path/to/target-repo
    python /path/to/graphdb/scalable_ingest.py
    python /path/to/graphdb/helix_to_igraph.py --out-dir /tmp/repo-graph

    cd /path/to/graphdb
    python tools/graph_mcp_server.py --data-dir /tmp/repo-graph
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def _extract_embedding(state_data: dict) -> list[float]:
    """Return a non-zero 2048-dim embedding, or [] if absent/zero."""
    vec = state_data.get("ai_summary_vec", [])
    if isinstance(vec, list) and len(vec) == 2048 and any(abs(v) > 1e-12 for v in vec):
        return vec
    return []


def _query_helixdb_active_states(helix_url: str) -> dict[str, dict]:
    """Query HelixDB for active FunctionState nodes.

    Returns {FunctionIdentity_node_id: state_data} with code, ai_summary,
    ai_summary_vec, commit.
    """
    from helixdb import Client, g, read_batch, Predicate, Projection

    c = Client(helix_url)
    batch = (
        read_batch()
        .var_as(
            "states",
            g()
            .n_with_label("FunctionState")
            .where(Predicate.eq("status", "active"))
            .project(
                [
                    Projection.property("node_id"),
                    Projection.property("function_id"),
                    Projection.property("code"),
                    Projection.property("ai_summary"),
                    Projection.property("ai_summary_vec"),
                    Projection.property("commit"),
                ]
            ),
        )
        .returning(["states"])
    )
    result = c.query().dynamic(batch.to_dynamic_request()).send()
    rows = result.get("states", {}).get("properties", [])

    seen: dict[str, dict] = {}
    for r in rows:
        fid = r.get("function_id", "")
        if fid and fid not in seen:
            seen[fid] = r
    return seen


def build_igraph_json(
    payload_path: str | Path,
    out_dir: str | Path,
    helix_url: str | None = None,
) -> None:
    payload_path = Path(payload_path)
    if not payload_path.exists():
        print(f"Error: {payload_path} not found.", file=sys.stderr)
        print("Run scalable_ingest.py in the target repo first.", file=sys.stderr)
        sys.exit(1)

    with open(payload_path, encoding="utf-8") as f:
        data = json.load(f)

    all_nodes = data.get("nodes", [])
    all_edges = data.get("edges", [])

    # ── Separate payload node types ───────────────────────────────────────
    func_identities = [n for n in all_nodes if n.get("type") == "FunctionIdentity"]
    func_states = [n for n in all_nodes if n.get("type") == "FunctionState"]
    file_identities = [n for n in all_nodes if n.get("type") == "FileIdentity"]

    # ── Optional HelixDB supplement ───────────────────────────────────────
    helix_states: dict[str, dict] = {}
    if helix_url:
        try:
            helix_states = _query_helixdb_active_states(helix_url)
            print(f"HelixDB: {len(helix_states)} active states queried")
        except Exception as e:
            print(f"HelixDB query failed ({e}), using graph_payload.json only")

    # ── Latest state per function (payload first, HelixDB wins) ───────────
    state_by_func: dict[str, dict] = {}
    for s in func_states:
        fid = s.get("function_id", "")
        if not fid:
            continue
        cur = state_by_func.get(fid)
        if not cur or s.get("status") == "active":
            state_by_func[fid] = s
    state_by_func.update(helix_states)  # HelixDB always has correct active status

    # ── Build igraph nodes ────────────────────────────────────────────────
    fi_id_to_igraph_id: dict[str, str] = {}
    igraph_nodes: list[dict] = []
    func_ids_by_file: dict[str, list[str]] = defaultdict(list)

    for fi in func_identities:
        fi_id = fi["id"]  # e.g. "func_src_auth_service_login"
        name = fi.get("name", "")
        file = fi.get("file", "")
        igraph_id = f"{file}::{name}"  # e.g. "src/auth/service.py::login"
        fi_id_to_igraph_id[fi_id] = igraph_id
        func_ids_by_file[file].append(igraph_id)

        state = state_by_func.get(fi_id, {})
        code = (state.get("code") or "")[:3000]
        ai_summary = state.get("ai_summary", "")
        status = state.get("status", "superseded")
        version_count = sum(
            1 for s in func_states if s.get("function_id") == fi_id
        )

        igraph_nodes.append(
            {
                "id": igraph_id,
                "label": "FunctionIdentity",
                "file": file,
                "name": name,
                "code": code,
                "text_summary": ai_summary,
                "embedding": _extract_embedding(state),
                "status": status,
                "version_count": version_count,
            }
        )

    n_active = sum(1 for n in igraph_nodes if n["status"] == "active")
    print(f"Nodes: {len(igraph_nodes)} ({n_active} active)")

    # ── Build igraph edges ────────────────────────────────────────────────
    file_id_to_path: dict[str, str] = {
        fi["id"]: fi.get("file", "") for fi in file_identities
    }

    igraph_edges: list[dict] = []
    edge_seen: set[tuple[str, str, str]] = set()

    for e in all_edges:
        label = e["label"]
        src_raw = e["from"]
        tgt_raw = e["to"]

        if label == "CALLS":
            src = fi_id_to_igraph_id.get(src_raw)
            tgt = fi_id_to_igraph_id.get(tgt_raw)
            if src and tgt and src != tgt:
                key = (src, tgt, "CALLS")
                if key not in edge_seen:
                    edge_seen.add(key)
                    igraph_edges.append(
                        {
                            "source": src,
                            "target": tgt,
                            "type": "CALLS",
                            "co_change_count": 0,
                            "ast_relation_type": "direct_call",
                        }
                    )

        elif label == "IMPORTS":
            src_file = file_id_to_path.get(src_raw)
            tgt_file = file_id_to_path.get(tgt_raw)
            if src_file and tgt_file and src_file != tgt_file:
                src_funcs = func_ids_by_file.get(src_file, [])
                tgt_funcs = func_ids_by_file.get(tgt_file, [])
                for fa in src_funcs:
                    for fb in tgt_funcs:
                        if fa != fb:
                            key = (fa, fb, "IMPORTS")
                            if key not in edge_seen:
                                edge_seen.add(key)
                                igraph_edges.append(
                                    {
                                        "source": fa,
                                        "target": fb,
                                        "type": "IMPORTS",
                                        "co_change_count": 0,
                                        "ast_relation_type": "import",
                                    }
                                )

    n_calls = sum(1 for e in igraph_edges if e["type"] == "CALLS")
    n_imports = sum(1 for e in igraph_edges if e["type"] == "IMPORTS")
    print(f"Edges: {len(igraph_edges)} ({n_calls} CALLS, {n_imports} IMPORTS)")

    # ── Write output ──────────────────────────────────────────────────────
    sandbox_dir = Path(out_dir) / "sandbox"
    sandbox_dir.mkdir(parents=True, exist_ok=True)

    nodes_path = sandbox_dir / "amo_nodes.json"
    edges_path = sandbox_dir / "amo_edges.json"

    with open(nodes_path, "w", encoding="utf-8") as f:
        json.dump(igraph_nodes, f, indent=2)
    with open(edges_path, "w", encoding="utf-8") as f:
        json.dump(igraph_edges, f, indent=2)

    print(f"Written: {nodes_path} ({len(igraph_nodes)} nodes)")
    print(f"         {edges_path} ({len(igraph_edges)} edges)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bridge HelixDB data into igraph JSON format"
    )
    parser.add_argument(
        "--payload",
        default="./graph_payload.json",
        help="Path to graph_payload.json from scalable_ingest.py",
    )
    parser.add_argument(
        "--out-dir",
        default=".",
        help="Output directory (sandbox/ subdir created inside)",
    )
    parser.add_argument(
        "--helix-url",
        default="http://127.0.0.1:6969",
        help="HelixDB URL",
    )
    parser.add_argument(
        "--skip-helix",
        action="store_true",
        help="Skip HelixDB query, use graph_payload.json only",
    )
    args = parser.parse_args()

    build_igraph_json(
        payload_path=args.payload,
        out_dir=args.out_dir,
        helix_url=None if args.skip_helix else args.helix_url,
    )


if __name__ == "__main__":
    main()
