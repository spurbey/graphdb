"""Bounded HelixDB neighborhood reads for the product traversal core."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from typing import Any, Iterable

from graph_traversal import build_overlay_edges, connect_seeds


@dataclass(frozen=True)
class Neighborhood:
    structural_edges: tuple[dict[str, Any], ...]
    affinity_edges: tuple[dict[str, Any], ...]
    nodes: tuple[str, ...]
    truncated: bool
    queries: int


class HelixNeighborhoodReader:
    def __init__(self, url: str = "http://127.0.0.1:6969"):
        from helixdb import Client

        self.client = Client(url)

    @staticmethod
    def _rows(response: dict, key: str) -> list[dict[str, Any]]:
        return response.get(key, {}).get("properties", [])

    def _neighbor_query(
        self,
        node_id: str,
        *,
        labels: tuple[str, ...],
        direction: str,
        limit: int,
    ) -> dict[str, list[dict[str, Any]]]:
        from helixdb import Predicate, Projection, g, read_batch

        batch = read_batch()
        returns: list[str] = []
        for index, label in enumerate(labels):
            key = f"neighbors_{index}"
            start = g().n_with_label("FunctionIdentity").where(
                Predicate.eq("node_id", node_id)
            )
            if direction == "in":
                traversal = start.in_e(label).other_n()
            else:
                traversal = start.out_e(label).other_n()
            batch = batch.var_as(
                key,
                traversal.limit(limit).project(
                    [
                        Projection.property("node_id"),
                        Projection.property("name"),
                        Projection.property("file"),
                    ]
                ),
            )
            returns.append(key)
        response = self.client.query().dynamic(
            batch.returning(returns).to_dynamic_request()
        ).send()
        return {
            label: self._rows(response, key)
            for label, key in zip(labels, returns)
        }

    def _affinity_query(self, source_id: str, limit: int) -> list[dict[str, Any]]:
        from helixdb import Predicate, g, read_batch

        response = self.client.query().dynamic(
            read_batch()
            .var_as(
                "affinity",
                g()
                .e_with_label_where(
                    "WORK_AFFINITY", Predicate.eq("source_function_id", source_id)
                )
                .limit(limit)
                .edge_properties(),
            )
            .returning(["affinity"])
            .to_dynamic_request()
        ).send()
        rows = []
        for row in self._rows(response, "affinity"):
            payload_text = row.get("payload_json")
            try:
                payload = json.loads(payload_text) if payload_text else {}
            except (TypeError, json.JSONDecodeError):
                continue
            rows.append(
                {
                    "source": row.get("source_function_id"),
                    "target": row.get("target_function_id"),
                    "payload": payload,
                }
            )
        return rows

    def load(
        self,
        seeds: Iterable[str],
        *,
        depth: int = 3,
        direction: str = "all",
        structural_labels: tuple[str, ...] = ("CALLS", "IMPORTS", "INHERITS"),
        per_label_limit: int = 64,
        affinity_limit: int = 32,
        node_budget: int = 512,
        edge_budget: int = 2048,
    ) -> Neighborhood:
        if depth < 1 or depth > 6:
            raise ValueError("depth must be between 1 and 6")
        seed_ids = tuple(dict.fromkeys(str(seed) for seed in seeds if seed))
        seen = set(seed_ids)
        queue = deque((seed, 0) for seed in seed_ids)
        structural: dict[tuple[str, str, str], dict[str, Any]] = {}
        affinity: dict[tuple[str, str], dict[str, Any]] = {}
        truncated = False
        queries = 0
        while queue:
            node_id, level = queue.popleft()
            if level >= depth:
                continue
            directions = ("out", "in") if direction == "all" else (direction,)
            for current_direction in directions:
                response = self._neighbor_query(
                    node_id,
                    labels=structural_labels,
                    direction=current_direction,
                    limit=per_label_limit,
                )
                queries += 1
                for label, rows in response.items():
                    if len(rows) >= per_label_limit:
                        truncated = True
                    for row in rows:
                        neighbor = row.get("node_id")
                        if not neighbor:
                            continue
                        source, target = (
                            (node_id, neighbor)
                            if current_direction == "out"
                            else (neighbor, node_id)
                        )
                        structural[(source, target, label)] = {
                            "source": source,
                            "target": target,
                            "label": label,
                        }
                        if neighbor not in seen:
                            if len(seen) >= node_budget:
                                truncated = True
                            else:
                                seen.add(neighbor)
                                queue.append((neighbor, level + 1))
                        if len(structural) >= edge_budget:
                            truncated = True
                            queue.clear()
                            break
                    if len(structural) >= edge_budget:
                        break
                if len(structural) >= edge_budget:
                    break

            rows = self._affinity_query(node_id, affinity_limit)
            queries += 1
            if len(rows) >= affinity_limit:
                truncated = True
            for row in rows:
                if row.get("source") and row.get("target"):
                    affinity[(row["source"], row["target"])] = row
                    target = row["target"]
                    if target not in seen and len(seen) < node_budget:
                        seen.add(target)
                        queue.append((target, level + 1))
            if len(seen) >= node_budget:
                truncated = True

        return Neighborhood(
            structural_edges=tuple(structural.values()),
            affinity_edges=tuple(affinity.values()),
            nodes=tuple(sorted(seen)),
            truncated=truncated,
            queries=queries,
        )


def connect_functions_helix(
    seed_ids: Iterable[str],
    *,
    root_ids: Iterable[str] | None = None,
    domain: str | None = None,
    change_kind: str | None = None,
    direction: str = "all",
    depth: int = 3,
    helix_url: str = "http://127.0.0.1:6969",
    node_budget: int = 512,
    edge_budget: int = 2048,
) -> dict[str, Any]:
    reader = HelixNeighborhoodReader(helix_url)
    seeds = tuple(dict.fromkeys(str(seed) for seed in seed_ids if seed))
    roots = tuple(dict.fromkeys(str(root) for root in (root_ids or ()) if root))
    neighborhood = reader.load(
        (*seeds, *roots),
        depth=depth,
        direction=direction,
        node_budget=node_budget,
        edge_budget=edge_budget,
    )
    edges, overlay_audit = build_overlay_edges(
        neighborhood.structural_edges,
        neighborhood.affinity_edges,
        domain=domain,
        change_kind=change_kind,
    )
    result = connect_seeds(
        seeds,
        edges,
        root_ids=roots or None,
        mode=direction,
        max_depth=depth,
    )
    result["snapshot"] = {
        "source": "helixdb",
        "node_count": len(neighborhood.nodes),
        "structural_edge_count": len(neighborhood.structural_edges),
        "affinity_edge_count": len(neighborhood.affinity_edges),
        "helix_query_count": neighborhood.queries,
        "truncated": neighborhood.truncated,
        "node_budget": node_budget,
        "edge_budget": edge_budget,
    }
    result["affinity"] = overlay_audit
    result["exact_within_snapshot"] = not neighborhood.truncated and not result[
        "audit"
    ].get("combination_truncated", False)
    return result

