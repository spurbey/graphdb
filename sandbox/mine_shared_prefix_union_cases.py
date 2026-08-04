"""Mine real Dograh call paths where union selection can beat independent paths.

This is a topology-first adversarial case miner. It uses the ephemeral
current-source overlay, enumerates bounded simple paths, and compares a
deterministic per-seed shortest-path choice with the minimum unique-edge union.
It deliberately does not select a query or claim semantic optimality; shortlisted
cases must be inspected in source and then run through the query-conditioned
experiment.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from itertools import combinations, islice, product
from pathlib import Path

import igraph as ig

from exp_multiseed_connectors import DEFAULT_HELIX_URL, NodeMeta, _is_product_node
from exp_multiseed_connectors import load_live_helix_calls
from exp_query_conditioned_connectors import DEFAULT_SOURCE_REPO
from exp_query_conditioned_connectors import build_current_calls_overlay


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "sandbox" / "out" / "shared_prefix_union_candidates.json"


def _trusted_status(status: str) -> bool:
    return status.startswith(
        (
            "raw_current_static_",
            "added_current_static_",
            "raw_current_same_file_name",
            "added_current_same_file_name",
        )
    )


def _path_record(graph: ig.Graph, path: list[int]) -> dict:
    edge_ids = [
        graph.get_eid(left, right, directed=True)
        for left, right in zip(path, path[1:])
    ]
    statuses = [graph.es[edge_id]["current_status"] for edge_id in edge_ids]
    return {
        "path": tuple(path),
        "edge_ids": tuple(edge_ids),
        "hops": len(path) - 1,
        "statuses": tuple(statuses),
    }


def _path_sort_key(item: dict) -> tuple:
    return item["hops"], item["path"]


def _combination_summary(paths: tuple[dict, ...]) -> dict:
    union_edges = {edge_id for path in paths for edge_id in path["edge_ids"]}
    union_nodes = {node for path in paths for node in path["path"]}
    total_hops = sum(path["hops"] for path in paths)
    return {
        "paths": paths,
        "union_edges": union_edges,
        "union_nodes": union_nodes,
        "union_edge_count": len(union_edges),
        "union_node_count": len(union_nodes),
        "total_hops": total_hops,
        "shared_edge_savings": total_hops - len(union_edges),
    }


def _combination_key(summary: dict) -> tuple:
    return (
        summary["union_edge_count"],
        summary["total_hops"],
        summary["union_node_count"],
        tuple(path["path"] for path in summary["paths"]),
    )


def _public_path(item: dict, meta: list[NodeMeta]) -> dict:
    return {
        "path": [meta[idx].node_id for idx in item["path"]],
        "hops": item["hops"],
        "edge_statuses": list(item["statuses"]),
    }


def _public_summary(summary: dict, meta: list[NodeMeta]) -> dict:
    return {
        "paths": [_public_path(path, meta) for path in summary["paths"]],
        "union_edge_count": summary["union_edge_count"],
        "union_node_count": summary["union_node_count"],
        "total_hops": summary["total_hops"],
        "shared_edge_savings": summary["shared_edge_savings"],
    }


def _node(idx: int, meta: list[NodeMeta]) -> dict:
    return {
        "id": meta[idx].node_id,
        "name": meta[idx].name,
        "file": meta[idx].file,
    }


def _candidate_paths(
    graph: ig.Graph,
    source: int,
    target: int,
    product_indices: set[int],
    max_hops: int,
    max_path_results: int,
    max_options_per_seed: int,
) -> tuple[list[dict], int]:
    raw_paths = graph.get_all_simple_paths(
        source,
        to=target,
        maxlen=max_hops,
        mode="out",
        max_results=max_path_results,
    )
    paths = []
    for path in raw_paths:
        if not all(idx in product_indices for idx in path):
            continue
        item = _path_record(graph, path)
        if not all(_trusted_status(status) for status in item["statuses"]):
            continue
        paths.append(item)
    paths.sort(key=_path_sort_key)
    return paths[:max_options_per_seed], len(paths)


def mine_direction(
    graph: ig.Graph,
    meta: list[NodeMeta],
    product_indices: set[int],
    direction_mode: str,
    max_hops: int,
    seed_count: int,
    max_seed_candidates: int,
    max_options_per_seed: int,
    max_path_results: int,
    max_union_combinations: int,
    max_results: int,
) -> tuple[list[dict], dict]:
    if direction_mode not in {"shared_callee", "common_caller"}:
        raise ValueError(direction_mode)
    neighborhood_mode = "in" if direction_mode == "shared_callee" else "out"
    results = []
    roots_scanned = 0
    seed_sets_evaluated = 0
    combination_cap_skips = 0

    for root in sorted(product_indices):
        neighbors = graph.neighborhood(
            vertices=root,
            order=max_hops,
            mode=neighborhood_mode,
            mindist=2,
        )
        path_options: dict[int, list[dict]] = {}
        full_option_counts: dict[int, int] = {}
        for seed in neighbors:
            if seed not in product_indices or seed == root:
                continue
            source, target = (
                (seed, root)
                if direction_mode == "shared_callee"
                else (root, seed)
            )
            options, full_count = _candidate_paths(
                graph,
                source,
                target,
                product_indices,
                max_hops,
                max_path_results,
                max_options_per_seed,
            )
            if len(options) < 2 or options[0]["hops"] < 2:
                continue
            path_options[seed] = options
            full_option_counts[seed] = full_count

        if len(path_options) < seed_count:
            continue
        roots_scanned += 1
        candidate_seeds = sorted(
            path_options,
            key=lambda seed: (
                -len(path_options[seed]),
                path_options[seed][0]["hops"],
                meta[seed].node_id,
            ),
        )[:max_seed_candidates]

        for seeds in combinations(candidate_seeds, seed_count):
            option_lists = [path_options[seed] for seed in seeds]
            combination_count = math.prod(len(options) for options in option_lists)
            if combination_count > max_union_combinations:
                combination_cap_skips += 1
                continue
            seed_sets_evaluated += 1
            independent = _combination_summary(
                tuple(options[0] for options in option_lists)
            )
            best = min(
                (
                    _combination_summary(choice)
                    for choice in islice(
                        product(*option_lists), max_union_combinations
                    )
                ),
                key=_combination_key,
            )
            changed = any(
                left["path"] != right["path"]
                for left, right in zip(independent["paths"], best["paths"])
            )
            edge_improvement = (
                independent["union_edge_count"] - best["union_edge_count"]
            )
            if not changed or edge_improvement <= 0:
                continue
            results.append(
                {
                    "direction_mode": direction_mode,
                    "max_hops": max_hops,
                    "root": _node(root, meta),
                    "seeds": [_node(seed, meta) for seed in seeds],
                    "combination_count": combination_count,
                    "full_path_option_counts": [
                        full_option_counts[seed] for seed in seeds
                    ],
                    "enumerated_path_option_counts": [
                        len(path_options[seed]) for seed in seeds
                    ],
                    "independent": _public_summary(independent, meta),
                    "union_selected": _public_summary(best, meta),
                    "union_edge_improvement": edge_improvement,
                    "selected_path_change_count": sum(
                        left["path"] != right["path"]
                        for left, right in zip(independent["paths"], best["paths"])
                    ),
                }
            )

    results.sort(
        key=lambda row: (
            -row["union_edge_improvement"],
            -row["union_selected"]["shared_edge_savings"],
            row["union_selected"]["total_hops"],
            row["combination_count"],
            row["root"]["id"],
            tuple(seed["id"] for seed in row["seeds"]),
        )
    )
    diagnostics = {
        "roots_with_enough_multi_path_seeds": roots_scanned,
        "seed_sets_evaluated": seed_sets_evaluated,
        "combination_cap_skips": combination_cap_skips,
        "qualifying_results_before_limit": len(results),
    }
    return results[:max_results], diagnostics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--helix-url", default=DEFAULT_HELIX_URL)
    parser.add_argument("--source-repo", type=Path, default=DEFAULT_SOURCE_REPO)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-hops", type=int, default=3)
    parser.add_argument("--seed-count", type=int, default=3)
    parser.add_argument("--max-seed-candidates", type=int, default=10)
    parser.add_argument("--max-options-per-seed", type=int, default=8)
    parser.add_argument("--max-path-results", type=int, default=100)
    parser.add_argument("--max-union-combinations", type=int, default=20_000)
    parser.add_argument("--max-results-per-direction", type=int, default=25)
    args = parser.parse_args()

    raw_directed, _, meta, _, helix_diagnostics = load_live_helix_calls(
        args.helix_url
    )
    graph, overlay_diagnostics = build_current_calls_overlay(
        raw_directed, meta, args.source_repo
    )
    product_indices = {
        idx
        for idx, item in enumerate(meta)
        if _is_product_node(item) and graph.vs[idx]["current_source_available"]
    }

    mined = {}
    mining_diagnostics = {}
    for direction_mode in ("shared_callee", "common_caller"):
        rows, diagnostics = mine_direction(
            graph,
            meta,
            product_indices,
            direction_mode,
            args.max_hops,
            args.seed_count,
            args.max_seed_candidates,
            args.max_options_per_seed,
            args.max_path_results,
            args.max_union_combinations,
            args.max_results_per_direction,
        )
        mined[direction_mode] = rows
        mining_diagnostics[direction_mode] = diagnostics

    result = {
        "experiment": "real_dograh_shared_prefix_union_case_mining",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "graph": "live_helix_calls",
            "edge_view": "current-overlay",
            "helix_diagnostics": helix_diagnostics,
            "overlay_diagnostics": overlay_diagnostics,
        },
        "bounds": {
            "max_hops": args.max_hops,
            "seed_count": args.seed_count,
            "max_seed_candidates_per_root": args.max_seed_candidates,
            "max_options_per_seed": args.max_options_per_seed,
            "max_path_results_per_pair": args.max_path_results,
            "max_union_combinations": args.max_union_combinations,
        },
        "selection_note": (
            "Topology-only shortlist. Independent paths minimize hop count per seed; "
            "union selection minimizes unique directed edges. Paths containing "
            "ambiguous or cross-file unique-name edges are excluded; every retained "
            "edge is statically resolved or same-file name evidence. "
            "Query-conditioned weighted validation is still required."
        ),
        "diagnostics": mining_diagnostics,
        "candidates": mined,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print("REAL DOGRAH SHARED-PREFIX UNION CANDIDATES")
    print(json.dumps(mining_diagnostics, indent=2))
    for direction_mode, rows in mined.items():
        print(f"\n{direction_mode}: {len(rows)} saved")
        for row in rows[:10]:
            print(
                row["root"]["name"],
                "edge_improvement=",
                row["union_edge_improvement"],
                "combinations=",
                row["combination_count"],
                "seeds=",
                ", ".join(seed["name"] for seed in row["seeds"]),
            )
    print("\nWrote", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
