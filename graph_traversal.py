"""Bounded, query-conditioned traversal over structural and learned edges.

The module contains no HelixDB code.  It accepts a bounded neighborhood read
from any backend and returns an auditable multi-seed connector result.  Learned
affinity is deliberately subordinate to structural relationships.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from itertools import product
from typing import Any, Iterable, Mapping


STRUCTURAL_COSTS = {
    "CALLS": 1.0,
    "IMPORTS": 1.1,
    "INHERITS": 1.1,
    "CONTAINS": 1.2,
    "HAS_STATE": 1.5,
}


@dataclass(frozen=True)
class TraversalEdge:
    source: str
    target: str
    label: str
    cost: float
    structural: bool
    affinity_score: float = 0.0
    episodes: int = 0
    physical_key: tuple[str, str, str] | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        return self.physical_key or (self.source, self.target, self.label)


def select_affinity_score(
    payload: Mapping[str, Any],
    *,
    domain: str | None = None,
    change_kind: str | None = None,
) -> tuple[float, str]:
    """Select the most-specific available dimension without combining scores."""

    domains = payload.get("domains") or {}
    if domain and isinstance(domains, Mapping) and domain in domains:
        return float(domains[domain]), f"domain:{domain}"
    change_kinds = payload.get("change_kinds") or {}
    if change_kind and isinstance(change_kinds, Mapping) and change_kind in change_kinds:
        return float(change_kinds[change_kind]), f"change_kind:{change_kind}"
    value = payload.get("global")
    return (float(value), "global") if value is not None else (0.0, "none")


def build_overlay_edges(
    structural_edges: Iterable[Mapping[str, Any]],
    affinity_edges: Iterable[Mapping[str, Any]],
    *,
    domain: str | None = None,
    change_kind: str | None = None,
    min_shortcut_episodes: int = 2,
    max_shortcuts_per_source: int = 3,
) -> tuple[list[TraversalEdge], dict[str, Any]]:
    """Combine a bounded structural read with WORK_AFFINITY payloads."""

    result: dict[tuple[str, str, str], TraversalEdge] = {}
    structural_pairs: set[tuple[str, str]] = set()
    for row in structural_edges:
        source = str(row.get("source") or row.get("from") or "")
        target = str(row.get("target") or row.get("to") or "")
        label = str(row.get("label") or row.get("type") or "")
        if not source or not target or not label:
            continue
        base = float(row.get("cost", STRUCTURAL_COSTS.get(label, 1.5)))
        key = (source, target, label)
        structural_pairs.add((source, target))
        result[key] = TraversalEdge(source, target, label, base, True)

    overlay_count = 0
    shortcut_candidates: dict[str, list[TraversalEdge]] = {}
    selected_dimensions: dict[str, str] = {}
    for row in affinity_edges:
        source = str(row.get("source") or row.get("from") or "")
        target = str(row.get("target") or row.get("to") or "")
        if not source or not target or source == target:
            continue
        payload = row.get("payload") or row
        score, dimension = select_affinity_score(
            payload, domain=domain, change_kind=change_kind
        )
        score = max(0.0, min(1.0, score))
        if score <= 0.0:
            continue
        episodes = int(payload.get("episode_count", row.get("episode_count", 0)) or 0)
        selected_dimensions[f"{source}->{target}"] = dimension
        if (source, target) in structural_pairs:
            # A learned signal may improve a direct structural relationship by
            # at most 25%; it never creates a second semantic edge.
            for key, edge in list(result.items()):
                if edge.source == source and edge.target == target:
                    result[key] = TraversalEdge(
                        source,
                        target,
                        edge.label,
                        max(0.75, edge.cost * (1.0 - 0.25 * score)),
                        True,
                        score,
                        episodes,
                    )
            overlay_count += 1
            continue
        if episodes < min_shortcut_episodes:
            continue
        # 0 affinity -> 2.0 hops; 1 affinity -> 1.25 hops.
        shortcut_candidates.setdefault(source, []).append(
            TraversalEdge(
                source,
                target,
                "WORK_AFFINITY",
                2.0 - 0.75 * score,
                False,
                score,
                episodes,
            )
        )

    shortcut_count = 0
    for source, candidates in shortcut_candidates.items():
        candidates.sort(
            key=lambda edge: (-edge.affinity_score, -edge.episodes, edge.target)
        )
        for edge in candidates[:max_shortcuts_per_source]:
            result[edge.key] = edge
            shortcut_count += 1

    return list(result.values()), {
        "structural_edge_count": sum(edge.structural for edge in result.values()),
        "affinity_overlay_count": overlay_count,
        "affinity_shortcut_count": shortcut_count,
        "selected_dimensions": selected_dimensions,
    }


def _adjacency(edges: Iterable[TraversalEdge], mode: str) -> dict[str, list[TraversalEdge]]:
    adjacency: dict[str, list[TraversalEdge]] = {}
    for edge in edges:
        adjacency.setdefault(edge.source, []).append(edge)
        if mode == "all":
            adjacency.setdefault(edge.target, []).append(
                TraversalEdge(
                    edge.target,
                    edge.source,
                    edge.label,
                    edge.cost,
                    edge.structural,
                    edge.affinity_score,
                    edge.episodes,
                    edge.key,
                )
            )
    for rows in adjacency.values():
        rows.sort(key=lambda edge: (edge.cost, edge.target, edge.label))
    return adjacency


def bounded_shortest_paths(
    edges: Iterable[TraversalEdge],
    source: str,
    target: str,
    *,
    mode: str = "out",
    max_depth: int = 4,
    max_paths: int = 4,
    max_expansions: int = 2000,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return a few simple paths with deterministic bounded search."""

    if mode == "in":
        reversed_edges = [
            TraversalEdge(
                edge.target,
                edge.source,
                edge.label,
                edge.cost,
                edge.structural,
                edge.affinity_score,
                edge.episodes,
                edge.key,
            )
            for edge in edges
        ]
        adjacency = _adjacency(reversed_edges, "out")
    else:
        adjacency = _adjacency(edges, mode)
    sequence = 0
    queue: list[
        tuple[float, tuple[str, ...], int, tuple[TraversalEdge, ...]]
    ] = [
        (0.0, (source,), sequence, ())
    ]
    found: list[tuple[float, tuple[str, ...], tuple[TraversalEdge, ...]]] = []
    expansions = 0
    truncated = False
    while queue and expansions < max_expansions:
        cost, nodes, _, path_edges = heapq.heappop(queue)
        current = nodes[-1]
        if current == target:
            found.append((cost, nodes, path_edges))
            if len(found) >= max_paths:
                break
            continue
        if len(path_edges) >= max_depth:
            continue
        expansions += 1
        for edge in adjacency.get(current, []):
            if edge.target in nodes:
                continue
            sequence += 1
            heapq.heappush(
                queue,
                (
                    cost + edge.cost,
                    nodes + (edge.target,),
                    sequence,
                    path_edges + (edge,),
                ),
            )
    if queue and len(found) < max_paths:
        truncated = True
    found.sort(key=lambda row: (row[0], len(row[1]), row[1]))
    return [
        {
            "nodes": list(nodes),
            "edges": [edge.key for edge in path_edges],
            "edge_details": [
                {
                    "source": edge.source,
                    "target": edge.target,
                    "label": edge.label,
                    "cost": round(edge.cost, 8),
                    "structural": edge.structural,
                    "affinity_score": round(edge.affinity_score, 8),
                    "episodes": edge.episodes,
                }
                for edge in path_edges
            ],
            "hops": len(path_edges),
            "cost": round(cost, 8),
        }
        for cost, nodes, path_edges in found
    ], {"expansions": expansions, "truncated": truncated}


def connect_seeds(
    seed_ids: Iterable[str],
    edges: Iterable[TraversalEdge],
    *,
    root_ids: Iterable[str] | None = None,
    mode: str = "out",
    max_depth: int = 4,
    max_paths_per_seed: int = 3,
    max_roots: int = 128,
    max_union_combinations: int = 256,
) -> dict[str, Any]:
    """Find a bounded shared connector and optimize its unique edge union."""

    seeds = tuple(dict.fromkeys(str(seed) for seed in seed_ids if seed))
    if len(seeds) < 2:
        raise ValueError("at least two seeds are required")
    edge_list = list(edges)
    all_nodes = set(seeds)
    for edge in edge_list:
        all_nodes.update((edge.source, edge.target))
    candidate_nodes = (
        tuple(dict.fromkeys(str(root) for root in root_ids if root))
        if root_ids is not None
        else tuple(sorted(all_nodes))
    )
    candidates: list[tuple[float, str, list[list[dict[str, Any]]]]] = []
    path_audit: dict[str, Any] = {}
    for root in candidate_nodes:
        options: list[list[dict[str, Any]]] = []
        feasible = True
        for seed in seeds:
            source, target = (seed, root) if mode != "in" else (root, seed)
            paths, audit = bounded_shortest_paths(
                edge_list,
                source,
                target,
                mode="all" if mode == "all" else "out",
                max_depth=max_depth,
                max_paths=max_paths_per_seed,
            )
            path_audit[f"{root}:{seed}"] = audit
            if not paths:
                feasible = False
                break
            options.append(paths)
        if not feasible:
            continue
        independent = tuple(rows[0] for rows in options)
        independent_cost = sum(row["cost"] for row in independent)
        candidates.append((independent_cost, root, options))
    candidates.sort(key=lambda row: (row[0], row[1]))
    candidates = candidates[:max_roots]
    if not candidates:
        return {
            "seeds": list(seeds),
            "mode": mode,
            "connected": False,
            "paths": [],
            "union_edges": [],
            "audit": {"candidate_roots": 0, "path_audit": path_audit},
        }

    best: tuple[tuple[Any, ...], str, tuple[dict[str, Any], ...], set[tuple[str, str, str]]] | None = None
    evaluated = 0
    total_combinations = 0
    for _, root, options in candidates:
        total_combinations += _combination_count(options)
        for combo in product(*options):
            if evaluated >= max_union_combinations:
                break
            evaluated += 1
            union_edges = {
                tuple(edge) for path in combo for edge in path["edges"]
            }
            union_cost = sum(
                _edge_cost(edge_list, edge_key) for edge_key in union_edges
            )
            path_cost = sum(path["cost"] for path in combo)
            union_nodes = {node for path in combo for node in path["nodes"]}
            key = (
                union_cost,
                len(union_edges),
                len(union_nodes - set(seeds)),
                path_cost,
                root,
                tuple(tuple(path["nodes"]) for path in combo),
            )
            if best is None or key < best[0]:
                best = (key, root, combo, union_edges)
        if evaluated >= max_union_combinations:
            break
    assert best is not None
    key, root, combo, union_edges = best
    details = {
        "seeds": list(seeds),
        "mode": mode,
        "connected": True,
        "root": root,
        "paths": [
            {"seed": seed, **path} for seed, path in zip(seeds, combo)
        ],
        "union_edges": [
            _edge_public(edge_list, edge_key) for edge_key in sorted(union_edges)
        ],
        "union_node_count": len({node for path in combo for node in path["nodes"]}),
        "connector_node_count": len(
            {node for path in combo for node in path["nodes"]} - set(seeds)
        ),
        "union_edge_count": len(union_edges),
        "union_cost": round(float(key[0]), 8),
        "path_cost_sum": round(float(key[3]), 8),
        "audit": {
            "candidate_roots": len(candidates),
            "combination_count_total": total_combinations,
            "combination_count_evaluated": evaluated,
            "combination_truncated": evaluated < total_combinations,
            "path_audit": path_audit,
        },
    }
    return details


def _combination_count(options: list[list[dict[str, Any]]]) -> int:
    total = 1
    for rows in options:
        total *= len(rows)
    return total


def _edge_cost(edges: list[TraversalEdge], key: tuple[str, str, str]) -> float:
    for edge in edges:
        if edge.key == key:
            return edge.cost
    return 0.0


def _edge_public(edges: list[TraversalEdge], key: tuple[str, str, str]) -> dict[str, Any]:
    for edge in edges:
        if edge.key == key:
            return {
                "source": edge.source,
                "target": edge.target,
                "label": edge.label,
                "cost": round(edge.cost, 8),
                "structural": edge.structural,
                "affinity_score": round(edge.affinity_score, 8),
                "episodes": edge.episodes,
            }
    return {"source": key[0], "target": key[1], "label": key[2]}
