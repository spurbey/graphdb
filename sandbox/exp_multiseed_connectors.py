"""Evaluate multi-seed connector discovery on the live Dograh CALLS graph.

This is deliberately not a natural-language retrieval experiment.  The input is
an already-known set of functions (the state a coding agent reaches after
semantic search/grep/read).  The question is whether graph algorithms can find
the small set of functions that structurally connect those anchors.

The graph is exported directly from live HelixDB, canonicalized in memory, and
discarded when the process exits.  graph_payload.json and inline Helix vectors
are not used.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import igraph as ig
from helixdb import Client, Projection, g, read_batch


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HELIX_URL = "http://127.0.0.1:6969"


@dataclass(frozen=True)
class NodeMeta:
    node_id: str
    name: str
    file: str


@dataclass(frozen=True)
class ManualCase:
    case_id: str
    description: str
    seeds: tuple[str, ...]
    expected_connectors: tuple[str, ...]


MANUAL_CASES = (
    ManualCase(
        case_id="pipecat_pipeline_builders",
        description=(
            "Four production pipeline-builder functions whose shared orchestration "
            "function is intentionally hidden."
        ),
        seeds=(
            "dograh:func_api_services_pipecat_pipeline_builder_create_pipeline_components",
            "dograh:func_api_services_pipecat_pipeline_builder_build_pipeline",
            "dograh:func_api_services_pipecat_pipeline_builder_build_realtime_pipeline",
            "dograh:func_api_services_pipecat_pipeline_builder_create_pipeline_task",
        ),
        expected_connectors=(
            "dograh:func_api_services_pipecat_run_pipeline__run_pipeline",
        ),
    ),
    ManualCase(
        case_id="recording_audio_cache",
        description=(
            "Two public cache paths plus the final trim operation; the shared "
            "download/convert helper is intentionally hidden."
        ),
        seeds=(
            "dograh:func_api_services_pipecat_recording_audio_cache_create_recording_audio_fetcher",
            "dograh:func_api_services_pipecat_recording_audio_cache_warm_recording_cache",
            "dograh:func_api_services_pipecat_recording_audio_cache__trim_silence",
        ),
        expected_connectors=(
            "dograh:func_api_services_pipecat_recording_audio_cache__download_and_convert",
        ),
    ),
)


def _is_product_node(meta: NodeMeta) -> bool:
    name = meta.name or ""
    path = (meta.file or "").replace("\\", "/").lower()
    base = path.rsplit("/", 1)[-1]
    return not (
        name.startswith("test_")
        or base.startswith("test_")
        or path.startswith("tests/")
        or "/tests/" in f"/{path}"
    )


def _canonical_id(row: dict) -> str:
    return row.get("code_vector_source_node_id") or row.get("node_id") or ""


def load_live_helix_calls(
    helix_url: str,
    node_limit: int = 20_000,
    edge_limit: int = 100_000,
) -> tuple[ig.Graph, ig.Graph, list[NodeMeta], dict[str, int], dict]:
    """Export canonical FunctionIdentity nodes and CALLS edges from HelixDB."""
    client = Client(helix_url)

    node_query = (
        read_batch()
        .var_as(
            "nodes",
            g()
            .n_with_label("FunctionIdentity")
            .limit(node_limit)
            .project(
                [
                    Projection.property("$id", "helix_id"),
                    Projection.property("node_id"),
                    Projection.property("code_vector_source_node_id"),
                    Projection.property("name"),
                    Projection.property("file"),
                ]
            ),
        )
        .returning(["nodes"])
    )
    edge_query = (
        read_batch()
        .var_as("edges", g().e_with_label("CALLS").limit(edge_limit))
        .returning(["edges"])
    )

    t0 = time.perf_counter()
    node_result = client.query().dynamic(node_query.to_dynamic_request()).send()
    node_ms = (time.perf_counter() - t0) * 1000
    t1 = time.perf_counter()
    edge_result = client.query().dynamic(edge_query.to_dynamic_request()).send()
    edge_ms = (time.perf_counter() - t1) * 1000

    node_rows = node_result.get("nodes", {}).get("properties", [])
    edge_rows = edge_result.get("edges", {}).get("edges", [])
    if len(node_rows) >= node_limit:
        raise RuntimeError(
            f"FunctionIdentity export hit node_limit={node_limit}; paginate before trusting results"
        )
    if len(edge_rows) >= edge_limit:
        raise RuntimeError(
            f"CALLS export hit edge_limit={edge_limit}; paginate before trusting results"
        )

    helix_to_canonical: dict[int, str] = {}
    meta_by_id: dict[str, NodeMeta] = {}
    duplicate_rows = 0
    for row in node_rows:
        node_id = _canonical_id(row)
        if not node_id:
            continue
        file_path = row.get("file", "")
        helix_to_canonical[int(row["helix_id"])] = node_id
        meta = NodeMeta(node_id, row.get("name", ""), file_path)
        if node_id in meta_by_id:
            duplicate_rows += 1
        else:
            meta_by_id[node_id] = meta

    canonical_edges: set[tuple[str, str]] = set()
    unresolved_edges = 0
    for edge in edge_rows:
        source = helix_to_canonical.get(int(edge["from"]))
        target = helix_to_canonical.get(int(edge["to"]))
        if not source or not target:
            unresolved_edges += 1
            continue
        if source != target:
            canonical_edges.add((source, target))

    node_ids = sorted(meta_by_id)
    id_to_idx = {node_id: idx for idx, node_id in enumerate(node_ids)}
    meta = [meta_by_id[node_id] for node_id in node_ids]
    edge_tuples = [
        (id_to_idx[source], id_to_idx[target])
        for source, target in sorted(canonical_edges)
    ]

    directed = ig.Graph(n=len(node_ids), edges=edge_tuples, directed=True)
    undirected = directed.as_undirected(mode="collapse")
    diagnostics = {
        "helix_rows": len(node_rows),
        "canonical_nodes": len(node_ids),
        "duplicate_rows_collapsed": duplicate_rows,
        "helix_call_edges": len(edge_rows),
        "canonical_call_edges": len(edge_tuples),
        "unresolved_edges": unresolved_edges,
        "node_export_ms": round(node_ms, 2),
        "edge_export_ms": round(edge_ms, 2),
    }
    return directed, undirected, meta, id_to_idx, diagnostics


def load_local_cochange() -> dict[str, set[str]]:
    """Load Dograh co-change only as supporting evidence, never as graph truth."""
    path = ROOT / "sandbox" / "dograh_cochange_pairs.json"
    adjacency: dict[str, set[str]] = defaultdict(set)
    if not path.exists():
        return adjacency
    for pair in json.loads(path.read_text(encoding="utf-8")):
        source = pair.get("source") or pair.get("func_a")
        target = pair.get("target") or pair.get("func_b")
        if source and target:
            adjacency[source].add(target)
            adjacency[target].add(source)
    return adjacency


def _rank_of(ranked: list[int], targets: set[int]) -> int | None:
    for rank, idx in enumerate(ranked, 1):
        if idx in targets:
            return rank
    return None


def _row(idx: int, meta: list[NodeMeta], **values) -> dict:
    return {
        "id": meta[idx].node_id,
        "name": meta[idx].name,
        "file": meta[idx].file,
        **values,
    }


def _distance_ranking(
    graph: ig.Graph,
    seed_indices: list[int],
    product_indices: set[int],
    radius: int,
) -> tuple[list[int], dict[int, dict], list[list[float]]]:
    distances = graph.distances(source=seed_indices, mode="all")
    details: dict[int, dict] = {}
    for idx in product_indices - set(seed_indices):
        ds = [row[idx] for row in distances]
        coverage = sum(1 for d in ds if not math.isinf(d) and d <= radius)
        if coverage < 2:
            continue
        finite = [int(d) for d in ds if not math.isinf(d)]
        details[idx] = {
            "coverage": coverage,
            "direct_seed_links": sum(1 for d in ds if d == 1),
            "max_distance": max(finite) if finite else 10**9,
            "distance_sum": sum(finite),
            "degree": graph.degree(idx),
        }
    ranked = sorted(
        details,
        key=lambda idx: (
            -details[idx]["coverage"],
            details[idx]["max_distance"],
            details[idx]["distance_sum"],
            -details[idx]["direct_seed_links"],
            details[idx]["degree"],
            idx,
        ),
    )
    return ranked, details, distances


def _seed_path_ranking(
    graph: ig.Graph,
    seed_indices: list[int],
    product_indices: set[int],
    max_paths_per_pair: int = 2_000,
) -> tuple[list[int], dict[int, dict], set[int], list[dict]]:
    stats: dict[int, dict] = defaultdict(
        lambda: {"pair_coverage": 0, "path_count": 0, "fractional_paths": 0.0}
    )
    path_union: set[int] = set(seed_indices)
    pair_rows: list[dict] = []
    for left_pos, source in enumerate(seed_indices):
        for target in seed_indices[left_pos + 1 :]:
            paths = graph.get_all_shortest_paths(source, to=target, mode="all")
            if len(paths) > max_paths_per_pair:
                paths = paths[:max_paths_per_pair]
            if not paths:
                pair_rows.append({"source": source, "target": target, "paths": 0})
                continue
            pair_rows.append(
                {
                    "source": source,
                    "target": target,
                    "paths": len(paths),
                    "hops": len(paths[0]) - 1,
                }
            )
            nodes_in_pair: dict[int, int] = defaultdict(int)
            for path in paths:
                path_union.update(path)
                for idx in set(path[1:-1]):
                    if idx in product_indices:
                        nodes_in_pair[idx] += 1
                        stats[idx]["path_count"] += 1
            for idx, count in nodes_in_pair.items():
                stats[idx]["pair_coverage"] += 1
                stats[idx]["fractional_paths"] += count / len(paths)

    ranked = sorted(
        stats,
        key=lambda idx: (
            -stats[idx]["pair_coverage"],
            -stats[idx]["fractional_paths"],
            -stats[idx]["path_count"],
            graph.degree(idx),
            idx,
        ),
    )
    return ranked, stats, path_union, pair_rows


def _terminal_mst_paths(
    graph: ig.Graph,
    seed_indices: list[int],
    weights: list[float] | None,
) -> set[int]:
    """Standard metric-closure Steiner approximation expanded into graph paths."""
    terminal_edges: list[tuple[float, int, int, list[int]]] = []
    for left_pos, source in enumerate(seed_indices):
        for target in seed_indices[left_pos + 1 :]:
            path = graph.get_shortest_path(
                source, to=target, weights=weights, mode="all", output="vpath"
            )
            if not path:
                continue
            if weights is None:
                cost = float(len(path) - 1)
            else:
                edge_path = graph.get_shortest_path(
                    source, to=target, weights=weights, mode="all", output="epath"
                )
                cost = sum(weights[eid] for eid in edge_path)
            terminal_edges.append((cost, source, target, path))

    parent = {idx: idx for idx in seed_indices}

    def find(idx: int) -> int:
        while parent[idx] != idx:
            parent[idx] = parent[parent[idx]]
            idx = parent[idx]
        return idx

    tree_nodes = set(seed_indices)
    for _, source, target, path in sorted(
        terminal_edges, key=lambda item: (item[0], item[1], item[2])
    ):
        root_source, root_target = find(source), find(target)
        if root_source == root_target:
            continue
        parent[root_target] = root_source
        tree_nodes.update(path)
        if len({find(idx) for idx in seed_indices}) == 1:
            break
    return tree_nodes


def _ppr_rankings(
    graph: ig.Graph,
    seed_indices: list[int],
    product_indices: set[int],
) -> tuple[list[int], list[int], list[float]]:
    reset = [0.0] * graph.vcount()
    for idx in seed_indices:
        reset[idx] = 1.0 / len(seed_indices)
    scores = graph.personalized_pagerank(
        directed=False, damping=0.85, reset=reset, weights=None
    )
    candidates = product_indices - set(seed_indices)
    raw = sorted(candidates, key=lambda idx: (-scores[idx], idx))
    normalized = sorted(
        candidates,
        key=lambda idx: (-scores[idx] / math.sqrt(max(graph.degree(idx), 1)), idx),
    )
    return raw, normalized, scores


def _direction_counts(
    directed: ig.Graph, candidate: int, seed_indices: list[int]
) -> tuple[int, int]:
    out_neighbors = set(directed.neighbors(candidate, mode="out"))
    in_neighbors = set(directed.neighbors(candidate, mode="in"))
    common_caller = sum(1 for seed in seed_indices if seed in out_neighbors)
    common_callee = sum(1 for seed in seed_indices if seed in in_neighbors)
    return common_caller, common_callee


def evaluate_case(
    case: ManualCase,
    directed: ig.Graph,
    undirected: ig.Graph,
    meta: list[NodeMeta],
    id_to_idx: dict[str, int],
    product_indices: set[int],
    communities: list[int],
    global_betweenness: list[float],
    cochange: dict[str, set[str]],
    radius: int = 3,
) -> dict:
    missing = [node_id for node_id in (*case.seeds, *case.expected_connectors) if node_id not in id_to_idx]
    if missing:
        return {"case_id": case.case_id, "error": f"Missing nodes: {missing}"}

    seeds = [id_to_idx[node_id] for node_id in case.seeds]
    targets = {id_to_idx[node_id] for node_id in case.expected_connectors}

    distance_rank, distance_details, _ = _distance_ranking(
        undirected, seeds, product_indices, radius
    )
    path_rank, path_details, path_union, pair_rows = _seed_path_ranking(
        undirected, seeds, product_indices
    )
    ppr_raw, ppr_norm, ppr_scores = _ppr_rankings(
        undirected, seeds, product_indices
    )

    degrees = undirected.degree()
    hub_weights = [
        1.0
        + 0.35
        * (
            math.log1p(max(degrees[edge.source] - 2, 0))
            + math.log1p(max(degrees[edge.target] - 2, 0))
        )
        / 2.0
        for edge in undirected.es
    ]
    steiner_plain = _terminal_mst_paths(undirected, seeds, weights=None)
    steiner_hub = _terminal_mst_paths(undirected, seeds, weights=hub_weights)

    local_candidates = set(distance_rank)
    betweenness_rank = sorted(
        local_candidates,
        key=lambda idx: (-global_betweenness[idx], degrees[idx], idx),
    )

    def top_distance(limit: int = 8) -> list[dict]:
        rows = []
        for idx in distance_rank[:limit]:
            caller_count, callee_count = _direction_counts(directed, idx, seeds)
            seed_ids = set(case.seeds)
            cc_count = len(cochange.get(meta[idx].node_id, set()) & seed_ids)
            rows.append(
                _row(
                    idx,
                    meta,
                    **distance_details[idx],
                    common_caller_count=caller_count,
                    common_callee_count=callee_count,
                    seed_path_pair_coverage=path_details.get(idx, {}).get(
                        "pair_coverage", 0
                    ),
                    ppr=round(ppr_scores[idx], 8),
                    betweenness=round(global_betweenness[idx], 4),
                    community=communities[idx],
                    cochange_seed_count=cc_count,
                    in_plain_steiner=idx in steiner_plain,
                    in_hub_penalized_steiner=idx in steiner_hub,
                )
            )
        return rows

    def top_path(limit: int = 8) -> list[dict]:
        return [
            _row(idx, meta, **path_details[idx]) for idx in path_rank[:limit]
        ]

    target_details = []
    for target in sorted(targets):
        caller_count, callee_count = _direction_counts(directed, target, seeds)
        target_details.append(
            _row(
                target,
                meta,
                distance_rank=_rank_of(distance_rank, {target}),
                seed_path_rank=_rank_of(path_rank, {target}),
                ppr_raw_rank=_rank_of(ppr_raw, {target}),
                ppr_degree_normalized_rank=_rank_of(ppr_norm, {target}),
                local_betweenness_rank=_rank_of(betweenness_rank, {target}),
                common_caller_count=caller_count,
                common_callee_count=callee_count,
                in_shortest_path_union=target in path_union,
                in_plain_steiner=target in steiner_plain,
                in_hub_penalized_steiner=target in steiner_hub,
                community=communities[target],
                cochange_seed_count=len(
                    cochange.get(meta[target].node_id, set()) & set(case.seeds)
                ),
            )
        )

    return {
        "case_id": case.case_id,
        "description": case.description,
        "seeds": [_row(idx, meta, community=communities[idx]) for idx in seeds],
        "expected": target_details,
        "pairwise_shortest_paths": [
            {
                **pair,
                "source": meta[pair["source"]].name,
                "target": meta[pair["target"]].name,
            }
            for pair in pair_rows
        ],
        "top_distance_centers": top_distance(),
        "top_seed_path_nodes": top_path(),
        "plain_steiner_nodes": [
            _row(idx, meta) for idx in sorted(steiner_plain - set(seeds))
        ],
        "hub_penalized_steiner_nodes": [
            _row(idx, meta) for idx in sorted(steiner_hub - set(seeds))
        ],
    }


def generate_structural_holdouts(
    undirected: ig.Graph,
    meta: list[NodeMeta],
    product_indices: set[int],
    count: int,
    rng_seed: int = 42,
) -> list[tuple[int, list[int]]]:
    """Create deterministic 2-hop, three-branch connector holdouts.

    Each expected connector is removed from the seed set.  Seeds are selected at
    distance two through distinct first-hop branches, so a direct common-neighbor
    lookup alone is insufficient.
    """
    rng = random.Random(rng_seed)
    centers = [
        idx
        for idx in product_indices
        if 3 <= undirected.degree(idx) <= 25
        and meta[idx].name not in {"get", "set", "run", "close", "append", "main"}
    ]
    rng.shuffle(centers)
    cases: list[tuple[int, list[int]]] = []
    for center in centers:
        branches: list[tuple[int, int]] = []
        center_neighbors = set(undirected.neighbors(center))
        for first_hop in sorted(center_neighbors):
            candidates = [
                idx
                for idx in undirected.neighbors(first_hop)
                if idx != center
                and idx in product_indices
                and idx not in center_neighbors
                and undirected.degree(idx) <= 30
            ]
            if candidates:
                candidates.sort(key=lambda idx: (undirected.degree(idx), meta[idx].node_id))
                branches.append((first_hop, candidates[0]))
        if len(branches) < 3:
            continue
        seeds = [seed for _, seed in branches[:3]]
        if len(set(seeds)) != 3:
            continue
        cases.append((center, seeds))
        if len(cases) >= count:
            break
    return cases


def run_holdout_benchmark(
    cases: list[tuple[int, list[int]]],
    undirected: ig.Graph,
    product_indices: set[int],
    global_betweenness: list[float],
    radius: int = 4,
) -> dict:
    methods = {
        "distance_center": [],
        "seed_path": [],
        "ppr_raw": [],
        "ppr_degree_normalized": [],
        "local_betweenness": [],
    }
    steiner_plain_hits = 0
    steiner_hub_hits = 0
    rows = []
    degrees = undirected.degree()
    hub_weights = [
        1.0
        + 0.35
        * (
            math.log1p(max(degrees[edge.source] - 2, 0))
            + math.log1p(max(degrees[edge.target] - 2, 0))
        )
        / 2.0
        for edge in undirected.es
    ]
    for center, seeds in cases:
        distance_rank, _, _ = _distance_ranking(
            undirected, seeds, product_indices, radius
        )
        path_rank, _, _, _ = _seed_path_ranking(
            undirected, seeds, product_indices
        )
        ppr_raw, ppr_norm, _ = _ppr_rankings(undirected, seeds, product_indices)
        local = set(distance_rank)
        bet_rank = sorted(
            local,
            key=lambda idx: (-global_betweenness[idx], degrees[idx], idx),
        )
        rankings = {
            "distance_center": distance_rank,
            "seed_path": path_rank,
            "ppr_raw": ppr_raw,
            "ppr_degree_normalized": ppr_norm,
            "local_betweenness": bet_rank,
        }
        row = {"center": center, "seeds": seeds, "ranks": {}}
        for name, ranking in rankings.items():
            rank = _rank_of(ranking, {center})
            methods[name].append(rank)
            row["ranks"][name] = rank
        plain = _terminal_mst_paths(undirected, seeds, weights=None)
        hub = _terminal_mst_paths(undirected, seeds, weights=hub_weights)
        if center in plain:
            steiner_plain_hits += 1
        if center in hub:
            steiner_hub_hits += 1
        row["plain_steiner"] = center in plain
        row["hub_steiner"] = center in hub
        rows.append(row)

    summary = {}
    for name, ranks in methods.items():
        summary[name] = {
            "hit_at_1": sum(rank == 1 for rank in ranks),
            "hit_at_5": sum(rank is not None and rank <= 5 for rank in ranks),
            "found": sum(rank is not None for rank in ranks),
            "cases": len(ranks),
            "median_rank": (
                sorted(rank for rank in ranks if rank is not None)[
                    len([rank for rank in ranks if rank is not None]) // 2
                ]
                if any(rank is not None for rank in ranks)
                else None
            ),
        }
    summary["plain_steiner"] = {
        "contains_expected": steiner_plain_hits,
        "cases": len(cases),
    }
    summary["hub_penalized_steiner"] = {
        "contains_expected": steiner_hub_hits,
        "cases": len(cases),
    }
    return {"summary": summary, "cases": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--helix-url", default=DEFAULT_HELIX_URL)
    parser.add_argument("--holdouts", type=int, default=100)
    parser.add_argument("--json", action="store_true", help="Print full JSON")
    args = parser.parse_args()

    directed, undirected, meta, id_to_idx, diagnostics = load_live_helix_calls(
        args.helix_url
    )
    product_indices = {idx for idx, item in enumerate(meta) if _is_product_node(item)}
    cochange = load_local_cochange()

    t0 = time.perf_counter()
    ig.set_random_number_generator(random.Random(42))
    communities = directed.community_infomap(trials=5).membership
    infomap_ms = (time.perf_counter() - t0) * 1000

    t1 = time.perf_counter()
    global_betweenness = directed.betweenness(directed=True)
    betweenness_ms = (time.perf_counter() - t1) * 1000

    manual = [
        evaluate_case(
            case,
            directed,
            undirected,
            meta,
            id_to_idx,
            product_indices,
            communities,
            global_betweenness,
            cochange,
        )
        for case in MANUAL_CASES
    ]
    holdout_cases = generate_structural_holdouts(
        undirected, meta, product_indices, args.holdouts
    )
    benchmark = run_holdout_benchmark(
        holdout_cases, undirected, product_indices, global_betweenness
    )

    result = {
        "source": "live_helix_calls_exported_to_ephemeral_igraph",
        "diagnostics": {
            **diagnostics,
            "product_nodes": len(product_indices),
            "communities": len(set(communities)),
            "local_cochange_pairs": sum(len(v) for v in cochange.values()) // 2,
            "infomap_ms": round(infomap_ms, 2),
            "betweenness_ms": round(betweenness_ms, 2),
        },
        "manual_cases": manual,
        "holdout_benchmark": benchmark,
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print("LIVE HELIX SNAPSHOT")
    print(json.dumps(result["diagnostics"], indent=2))
    for case in manual:
        print("\n" + "=" * 78)
        print(case.get("case_id"), "-", case.get("description", ""))
        if case.get("error"):
            print(case["error"])
            continue
        print("Seeds:", ", ".join(seed["name"] for seed in case["seeds"]))
        for target in case["expected"]:
            print("Expected connector:", json.dumps(target, indent=2))
        print("Top distance centers:")
        for row in case["top_distance_centers"][:5]:
            print(
                f"  {row['name']:<38} cov={row['coverage']} "
                f"maxd={row['max_distance']} sum={row['distance_sum']} "
                f"caller={row['common_caller_count']} callee={row['common_callee_count']} "
                f"path_pairs={row['seed_path_pair_coverage']} degree={row['degree']}"
            )
        print("Top seed-path nodes:")
        for row in case["top_seed_path_nodes"][:5]:
            print(
                f"  {row['name']:<38} pair_coverage={row['pair_coverage']} "
                f"fractional={row['fractional_paths']:.3f} paths={row['path_count']}"
            )
        print(
            "Hub-penalized Steiner connectors:",
            ", ".join(row["name"] for row in case["hub_penalized_steiner_nodes"])
            or "(none)",
        )

    print("\n" + "=" * 78)
    print("DETERMINISTIC 2-HOP HOLDOUT BENCHMARK")
    print(json.dumps(benchmark["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
