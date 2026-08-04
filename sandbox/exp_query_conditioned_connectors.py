"""Observable query-conditioned connector experiments on live Dograh data.

This stage deliberately stops before claiming an optimal path or subgraph. It
audits bounded directed candidate generation and compares feature ablations with
all intermediate values exposed. HelixDB is the graph source. The local
TurboVec legacy cache is used only to inspect exact stored node embeddings; the
compressed TurboVec index remains the intended runtime retrieval mechanism.
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice, product
from pathlib import Path

import igraph as ig
import numpy as np
from helixdb import (
    Client,
    Predicate,
    Projection,
    RepeatConfig,
    SubTraversal,
    define_params,
    g,
    param,
    read_batch,
)

from exp_multiseed_connectors import (
    DEFAULT_HELIX_URL,
    NodeMeta,
    _is_product_node,
    load_live_helix_calls,
    load_local_cochange,
)


ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "sandbox" / "multiseed_query_cases.json"
LEGACY_CODE_VECTORS = ROOT / ".turbovec_code.json"
DEFAULT_SOURCE_REPO = ROOT.parent / "dograh"


@dataclass(frozen=True)
class QueryCase:
    case_id: str
    query: str
    direction_mode: str
    max_hops: int
    seed_ids: tuple[str, ...]
    expected_nodes: tuple[str, ...]
    acceptable_nodes: tuple[str, ...]
    generic_negative_names: tuple[str, ...]
    source_evidence: tuple[str, ...]


class CurrentSourceCalls:
    def __init__(self, source_repo: Path):
        self.source_repo = source_repo
        self._trees: dict[str, ast.AST | None] = {}
        self._functions: dict[
            str, dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]]
        ] = {}
        self._classes: dict[str, dict[str, set[str]]] = {}
        self._module_imports: dict[str, list[ast.Import | ast.ImportFrom]] = {}
        self._module_files: dict[tuple[str, str | None, int], str | None] = {}
        self._calls: dict[tuple[str, str], set[str] | None] = {}
        self._direct_calls: dict[tuple[str, str], set[str] | None] = {}
        self._function_owner_classes: dict[str, dict[int, str]] = {}
        self._resolved_static_calls: dict[
            tuple[str, str], dict[tuple[str, str], str]
        ] = {}
        self._unresolved_attribute_calls: dict[tuple[str, str], set[str]] = {}

    def _tree(self, file_path: str) -> ast.AST | None:
        normalized = file_path.replace("\\", "/")
        if normalized in self._trees:
            return self._trees[normalized]
        path = self.source_repo / normalized
        if not path.exists():
            self._trees[normalized] = None
            return None
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception:
            tree = None
        self._trees[normalized] = tree
        return tree

    def _function_index(
        self, file_path: str
    ) -> dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]]:
        normalized = file_path.replace("\\", "/")
        if normalized in self._functions:
            return self._functions[normalized]
        tree = self._tree(normalized)
        index: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    index.setdefault(node.name, []).append(node)
        self._functions[normalized] = index
        return index

    def _matches(
        self, file_path: str, function_name: str
    ) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
        return self._function_index(file_path).get(function_name, [])

    def _class_index(self, file_path: str) -> dict[str, set[str]]:
        normalized = file_path.replace("\\", "/")
        if normalized in self._classes:
            return self._classes[normalized]
        tree = self._tree(normalized)
        index: dict[str, set[str]] = {}
        if tree is not None:
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                methods = {
                    child.name
                    for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                index[node.name] = methods
        self._classes[normalized] = index
        return index

    def calls(self, file_path: str, function_name: str) -> set[str] | None:
        key = (file_path.replace("\\", "/"), function_name)
        if key in self._calls:
            return self._calls[key]
        tree = self._tree(key[0])
        if tree is None:
            self._calls[key] = None
            return None
        matches = self._matches(key[0], function_name)
        if not matches:
            self._calls[key] = None
            return None
        names: set[str] = set()
        for node in matches:
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                if isinstance(child.func, ast.Name):
                    names.add(child.func.id)
                elif isinstance(child.func, ast.Attribute):
                    names.add(child.func.attr)
        self._calls[key] = names
        return names

    def direct_calls(self, file_path: str, function_name: str) -> set[str] | None:
        key = (file_path.replace("\\", "/"), function_name)
        if key in self._direct_calls:
            return self._direct_calls[key]
        tree = self._tree(key[0])
        if tree is None:
            self._direct_calls[key] = None
            return None
        matches = self._matches(key[0], function_name)
        if not matches:
            self._direct_calls[key] = None
            return None
        names = {
            child.func.id
            for node in matches
            for child in ast.walk(node)
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
        }
        self._direct_calls[key] = names
        return names

    def _owner_class(
        self,
        file_path: str,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> str | None:
        normalized = file_path.replace("\\", "/")
        if normalized not in self._function_owner_classes:
            owners: dict[int, str] = {}
            tree = self._tree(normalized)
            if tree is not None:
                for node in ast.walk(tree):
                    if not isinstance(node, ast.ClassDef):
                        continue
                    for child in node.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            owners[id(child)] = node.name
            self._function_owner_classes[normalized] = owners
        return self._function_owner_classes[normalized].get(id(function))

    def _module_file(
        self, source_file: str, module: str | None, level: int
    ) -> str | None:
        normalized = source_file.replace("\\", "/")
        cache_key = (normalized, module, level)
        if cache_key in self._module_files:
            return self._module_files[cache_key]
        source_parts = list(Path(normalized).parent.parts)
        if level:
            parents_to_drop = level - 1
            if parents_to_drop > len(source_parts):
                return None
            module_parts = source_parts[: len(source_parts) - parents_to_drop]
        else:
            module_parts = []
        if module:
            module_parts.extend(module.split("."))
        if not module_parts:
            self._module_files[cache_key] = None
            return None
        candidates = [
            Path(*module_parts).with_suffix(".py"),
            Path(*module_parts) / "__init__.py",
        ]
        for candidate in candidates:
            if (self.source_repo / candidate).exists():
                resolved = candidate.as_posix()
                self._module_files[cache_key] = resolved
                return resolved
        self._module_files[cache_key] = None
        return None

    @staticmethod
    def _scope_imports(node: ast.AST) -> list[ast.Import | ast.ImportFrom]:
        imports: list[ast.Import | ast.ImportFrom] = []

        def visit(current: ast.AST, *, root: bool = False) -> None:
            if not root and isinstance(
                current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                return
            if isinstance(current, (ast.Import, ast.ImportFrom)):
                imports.append(current)
            for child in ast.iter_child_nodes(current):
                visit(child)

        visit(node, root=True)
        return imports

    @staticmethod
    def _attribute_chain(node: ast.AST) -> tuple[str, ...] | None:
        parts: list[str] = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if not isinstance(current, ast.Name):
            return None
        parts.append(current.id)
        return tuple(reversed(parts))

    @staticmethod
    def _module_name_for_file(file_path: str) -> str:
        path = Path(file_path.replace("\\", "/"))
        if path.name == "__init__.py":
            path = path.parent
        else:
            path = path.with_suffix("")
        return ".".join(path.parts)

    def resolved_static_calls(
        self, file_path: str, function_name: str
    ) -> dict[tuple[str, str], str]:
        normalized = file_path.replace("\\", "/")
        key = (normalized, function_name)
        if key in self._resolved_static_calls:
            return self._resolved_static_calls[key]
        tree = self._tree(normalized)
        if tree is None:
            self._resolved_static_calls[key] = {}
            self._unresolved_attribute_calls[key] = set()
            return {}
        matches = self._matches(normalized, function_name)
        if not matches:
            self._resolved_static_calls[key] = {}
            self._unresolved_attribute_calls[key] = set()
            return {}

        if normalized not in self._module_imports:
            module_imports: list[ast.Import | ast.ImportFrom] = []
            for statement in getattr(tree, "body", []):
                if isinstance(
                    statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    continue
                module_imports.extend(self._scope_imports(statement))
            self._module_imports[normalized] = module_imports
        module_imports = self._module_imports[normalized]

        resolved: dict[tuple[str, str], str] = {}
        unresolved_attributes: set[str] = set()
        same_file_classes = self._class_index(normalized)
        for function in matches:
            direct_bindings: dict[str, tuple[str, str]] = {}
            module_bindings: dict[str, tuple[str, tuple[str, ...]]] = {}
            class_bindings: dict[str, tuple[str, str]] = {}
            for import_node in module_imports + self._scope_imports(function):
                if isinstance(import_node, ast.Import):
                    for alias in import_node.names:
                        module_file = self._module_file(normalized, alias.name, 0)
                        if module_file is None:
                            continue
                        local_name = alias.asname or alias.name.split(".")[0]
                        prefix = (
                            (local_name,)
                            if alias.asname
                            else tuple(alias.name.split("."))
                        )
                        module_bindings[local_name] = (module_file, prefix)
                    continue

                module_file = self._module_file(
                    normalized, import_node.module, import_node.level
                )
                if module_file is None:
                    continue
                for alias in import_node.names:
                    if alias.name == "*":
                        continue
                    local_name = alias.asname or alias.name
                    if self.calls(module_file, alias.name) is not None:
                        direct_bindings[local_name] = (module_file, alias.name)
                        continue
                    if alias.name in self._class_index(module_file):
                        class_bindings[local_name] = (module_file, alias.name)
                        continue
                    child_module = (
                        f"{import_node.module}.{alias.name}"
                        if import_node.module
                        else alias.name
                    )
                    child_file = self._module_file(
                        normalized, child_module, import_node.level
                    )
                    if child_file is not None:
                        module_bindings[local_name] = (child_file, (local_name,))

            for child in ast.walk(function):
                if not isinstance(child, ast.Call):
                    continue
                if isinstance(child.func, ast.Name):
                    target = direct_bindings.get(child.func.id)
                    if target is not None:
                        target_file, target_name = target
                        if self.calls(target_file, target_name) is not None:
                            resolved[target] = "imported_function"
                    continue
                if not isinstance(child.func, ast.Attribute):
                    continue
                chain = self._attribute_chain(child.func)
                if chain is None or len(chain) < 2:
                    unresolved_attributes.add(child.func.attr)
                    continue
                root_name, target_name = chain[0], chain[-1]

                if root_name in {"self", "cls"}:
                    owner_class = self._owner_class(normalized, function)
                    if (
                        owner_class is not None
                        and target_name in same_file_classes.get(owner_class, set())
                    ):
                        resolved[(normalized, target_name)] = "same_file_method"
                    else:
                        unresolved_attributes.add(target_name)
                    continue

                if (
                    len(chain) == 2
                    and target_name in same_file_classes.get(root_name, set())
                ):
                    resolved[(normalized, target_name)] = "same_file_class_method"
                    continue

                class_binding = class_bindings.get(root_name)
                if class_binding is not None and len(chain) == 2:
                    target_file, class_name = class_binding
                    if target_name in self._class_index(target_file).get(class_name, set()):
                        resolved[(target_file, target_name)] = "imported_class_method"
                        continue

                module_binding = module_bindings.get(root_name)
                if module_binding is not None:
                    base_file, prefix = module_binding
                    if chain[: len(prefix)] == prefix:
                        extra_modules = chain[len(prefix) : -1]
                        target_file = base_file
                        if extra_modules:
                            base_module = self._module_name_for_file(base_file)
                            target_file = self._module_file(
                                normalized,
                                ".".join((base_module, *extra_modules)),
                                0,
                            )
                        if (
                            target_file is not None
                            and self.calls(target_file, target_name) is not None
                        ):
                            resolved[(target_file, target_name)] = "module_attribute"
                            continue

                unresolved_attributes.add(target_name)

        self._resolved_static_calls[key] = resolved
        self._unresolved_attribute_calls[key] = unresolved_attributes
        return resolved

    def unresolved_attribute_calls(
        self, file_path: str, function_name: str
    ) -> set[str]:
        key = (file_path.replace("\\", "/"), function_name)
        if key not in self._resolved_static_calls:
            self.resolved_static_calls(*key)
        return self._unresolved_attribute_calls.get(key, set())


def build_current_calls_overlay(
    raw_directed: ig.Graph,
    meta: list[NodeMeta],
    source_repo: Path,
) -> tuple[ig.Graph, dict]:
    source_calls = CurrentSourceCalls(source_repo)
    current_edges: dict[tuple[int, int], str] = {}
    rejected_stale = 0
    source_unavailable = 0
    target_unavailable = 0
    current_nodes = [
        source_calls.calls(item.file, item.name) is not None for item in meta
    ]

    exact_targets: dict[tuple[str, str], list[int]] = {}
    name_targets: dict[str, list[int]] = {}
    for idx, item in enumerate(meta):
        if not current_nodes[idx]:
            continue
        key = (item.file.replace("\\", "/"), item.name)
        exact_targets.setdefault(key, []).append(idx)
        name_targets.setdefault(item.name, []).append(idx)

    static_targets_by_source = {
        source: source_calls.resolved_static_calls(item.file, item.name)
        for source, item in enumerate(meta)
        if current_nodes[source]
    }

    for edge in raw_directed.es:
        source, target = edge.tuple
        if not current_nodes[source]:
            source_unavailable += 1
            continue
        if not current_nodes[target]:
            target_unavailable += 1
            continue
        calls = source_calls.calls(meta[source].file, meta[source].name)
        if calls is not None and meta[target].name in calls:
            target_identity = (
                meta[target].file.replace("\\", "/"),
                meta[target].name,
            )
            static_targets = static_targets_by_source.get(source, {})
            if (
                target_identity in static_targets
                and len(exact_targets.get(target_identity, [])) == 1
            ):
                category = static_targets[target_identity]
                status = f"raw_current_static_{category}"
            elif meta[source].file.replace("\\", "/") == target_identity[0]:
                status = "raw_current_same_file_name"
            elif len(name_targets.get(meta[target].name, [])) == 1:
                status = "raw_current_unique_name"
            else:
                status = "raw_current_ambiguous_name"
            current_edges[(source, target)] = status
        else:
            rejected_stale += 1

    added_same_file = 0
    ambiguous_same_file = 0
    for source, item in enumerate(meta):
        calls = source_calls.direct_calls(item.file, item.name)
        if not calls:
            continue
        normalized_file = item.file.replace("\\", "/")
        static_target_names = {
            target_name
            for (_, target_name) in static_targets_by_source.get(source, {})
        }
        for target_name in calls:
            if target_name in static_target_names:
                continue
            targets = exact_targets.get((normalized_file, target_name), [])
            if len(targets) > 1:
                ambiguous_same_file += 1
                continue
            if not targets:
                continue
            target = targets[0]
            if source == target:
                continue
            key = (source, target)
            if key not in current_edges:
                current_edges[key] = "added_current_same_file_name"
                added_same_file += 1

    added_static: Counter[str] = Counter()
    ambiguous_static: Counter[str] = Counter()
    unresolved_static: Counter[str] = Counter()
    for source, item in enumerate(meta):
        for (target_file, target_name), category in static_targets_by_source.get(
            source, {}
        ).items():
            targets = exact_targets.get((target_file, target_name), [])
            if len(targets) > 1:
                ambiguous_static[category] += 1
                continue
            if not targets:
                unresolved_static[category] += 1
                continue
            target = targets[0]
            if source == target:
                continue
            key = (source, target)
            if key not in current_edges:
                current_edges[key] = f"added_current_static_{category}"
                added_static[category] += 1

    unresolved_attribute_names: Counter[str] = Counter()
    unresolved_attribute_function_count = 0
    for source, item in enumerate(meta):
        if not current_nodes[source]:
            continue
        unresolved = source_calls.unresolved_attribute_calls(item.file, item.name)
        if unresolved:
            unresolved_attribute_function_count += 1
            unresolved_attribute_names.update(unresolved)

    edge_tuples = sorted(current_edges)
    graph = ig.Graph(n=len(meta), edges=edge_tuples, directed=True)
    graph.es["current_status"] = [current_edges[edge] for edge in edge_tuples]
    graph.vs["current_source_available"] = current_nodes
    diagnostics = {
        "source_repo": str(source_repo),
        "raw_call_edges": raw_directed.ecount(),
        "current_overlay_edges": graph.ecount(),
        "raw_edges_rejected_name_absent": rejected_stale,
        "raw_edges_dropped_source_unavailable": source_unavailable,
        "raw_edges_dropped_target_unavailable": target_unavailable,
        "current_source_function_nodes": sum(current_nodes),
        "unavailable_function_identities": len(current_nodes) - sum(current_nodes),
        "same_file_current_edges_added": added_same_file,
        "ambiguous_same_file_targets_skipped": ambiguous_same_file,
        "static_current_edges_added_by_category": dict(sorted(added_static.items())),
        "ambiguous_static_targets_skipped_by_category": dict(
            sorted(ambiguous_static.items())
        ),
        "unresolved_static_targets_skipped_by_category": dict(
            sorted(unresolved_static.items())
        ),
        "raw_current_ambiguous_name_edges_retained": list(
            current_edges.values()
        ).count("raw_current_ambiguous_name"),
        "unresolved_attribute_functions": unresolved_attribute_function_count,
        "unresolved_attribute_name_occurrences_by_function": sum(
            unresolved_attribute_names.values()
        ),
        "top_unresolved_attribute_names": [
            {"name": name, "function_count": count}
            for name, count in unresolved_attribute_names.most_common(25)
        ],
        "unresolved_attribute_note": (
            "These are attribute-call names that the conservative source overlay "
            "could not bind statically. They include dynamic dispatch and other "
            "receiver-dependent calls; they are diagnostics, not resolved edges."
        ),
        "edge_status_counts": {
            status: graph.es["current_status"].count(status)
            for status in sorted(set(graph.es["current_status"]))
        },
    }
    return graph, diagnostics


def load_cases() -> list[QueryCase]:
    rows = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    return [
        QueryCase(
            case_id=row["case_id"],
            query=row["query"],
            direction_mode=row["direction_mode"],
            max_hops=int(row["max_hops"]),
            seed_ids=tuple(row["seed_ids"]),
            expected_nodes=tuple(row["expected_nodes"]),
            acceptable_nodes=tuple(row.get("acceptable_nodes", [])),
            generic_negative_names=tuple(row.get("generic_negative_names", [])),
            source_evidence=tuple(row.get("source_evidence", [])),
        )
        for row in rows
    ]


def load_embedding_matrix(
    meta: list[NodeMeta],
) -> tuple[np.ndarray, np.ndarray, dict]:
    if not LEGACY_CODE_VECTORS.exists():
        raise FileNotFoundError(
            f"{LEGACY_CODE_VECTORS} is required for exact experimental cosine inspection"
        )
    raw = json.loads(LEGACY_CODE_VECTORS.read_text(encoding="utf-8"))
    matrix = np.zeros((len(meta), 384), dtype=np.float32)
    available = np.zeros(len(meta), dtype=bool)
    invalid = 0
    for idx, item in enumerate(meta):
        vector = raw.get(item.node_id)
        if not isinstance(vector, list) or len(vector) != 384:
            if vector is not None:
                invalid += 1
            continue
        arr = np.asarray(vector, dtype=np.float32)
        norm = float(np.linalg.norm(arr))
        if norm <= 1e-8:
            invalid += 1
            continue
        matrix[idx] = arr / norm
        available[idx] = True
    diagnostics = {
        "legacy_vector_rows": len(raw),
        "canonical_nodes_with_exact_vector": int(available.sum()),
        "canonical_nodes_without_exact_vector": int((~available).sum()),
        "invalid_vectors": invalid,
    }
    return matrix, available, diagnostics


def embed_queries(cases: list[QueryCase]) -> np.ndarray:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("all-MiniLM-L6-v2")
    return np.asarray(
        model.encode(
            [case.query for case in cases],
            show_progress_bar=False,
            normalize_embeddings=True,
        ),
        dtype=np.float32,
    )


def _mode_for_case(case: QueryCase) -> str:
    if case.direction_mode == "shared_callee":
        return "out"
    if case.direction_mode == "common_caller":
        return "in"
    if case.direction_mode == "mixed_center":
        return "all"
    raise ValueError(f"Unsupported direction_mode: {case.direction_mode}")


def _finite_distance(value: float, max_hops: int) -> int | None:
    if math.isinf(value) or value > max_hops:
        return None
    return int(value)


def _rank(ranked: list[int], expected: set[int]) -> int | None:
    for rank, idx in enumerate(ranked, 1):
        if idx in expected:
            return rank
    return None


def _direct_seed_edges(
    directed: ig.Graph, candidate: int, seeds: list[int]
) -> tuple[int, int]:
    seed_set = set(seeds)
    outgoing = sum(1 for node in directed.successors(candidate) if node in seed_set)
    incoming = sum(1 for node in directed.predecessors(candidate) if node in seed_set)
    return outgoing, incoming


def _formula_contributions(row: dict) -> dict[str, dict[str, float]]:
    structural = {
        "coverage": 100.0 * row["coverage_ratio"],
        "max_distance": 4.0 / (1.0 + row["max_distance"]),
        "distance_sum": 2.0 / (1.0 + row["distance_sum"]),
    }
    semantic = {**structural, "semantic": 2.0 * row["semantic_raw"]}
    semantic_hub = {
        **semantic,
        "hub": -0.75 * row["hub_norm"],
        "generic_name": -3.0 if row["is_generic_negative"] else 0.0,
    }
    full = {
        **semantic_hub,
        "cochange": 0.5 * row["cochange_ratio"],
    }
    return {
        "structural": structural,
        "structural_semantic": semantic,
        "structural_semantic_hub": semantic_hub,
        "all_current_features": full,
    }


def _directional_support(row: dict, direction_mode: str) -> int:
    if direction_mode == "common_caller":
        return int(row["direct_outgoing_to_seeds"])
    if direction_mode == "shared_callee":
        return int(row["direct_incoming_from_seeds"])
    return int(row["direct_outgoing_to_seeds"] + row["direct_incoming_from_seeds"])


def _total(contributions: dict[str, float]) -> float:
    return float(sum(contributions.values()))


def evaluate_case(
    case: QueryCase,
    query_vector: np.ndarray,
    directed: ig.Graph,
    undirected: ig.Graph,
    meta: list[NodeMeta],
    id_to_idx: dict[str, int],
    product_indices: set[int],
    embeddings: np.ndarray,
    embedding_available: np.ndarray,
    cochange: dict[str, set[str]],
    max_path_results: int = 5_000,
    max_union_combinations: int = 100_000,
) -> dict:
    missing = [node_id for node_id in case.seed_ids if node_id not in id_to_idx]
    if missing:
        return {"case_id": case.case_id, "error": "missing seeds", "missing": missing}

    seeds = [id_to_idx[node_id] for node_id in case.seed_ids]
    expected = {id_to_idx[node_id] for node_id in case.expected_nodes if node_id in id_to_idx}
    mode = _mode_for_case(case)
    graph = undirected if mode == "all" else directed
    distances = graph.distances(source=seeds, mode=mode)
    semantic_all = embeddings @ query_vector

    frontier_by_seed: list[dict] = []
    for seed_pos, seed_idx in enumerate(seeds):
        exact = {}
        cumulative = {}
        for depth in range(1, case.max_hops + 1):
            exact[str(depth)] = sum(
                1
                for idx in product_indices
                if _finite_distance(distances[seed_pos][idx], case.max_hops) == depth
            )
            cumulative[str(depth)] = sum(
                1
                for idx in product_indices
                if (
                    (d := _finite_distance(distances[seed_pos][idx], case.max_hops))
                    is not None
                    and d <= depth
                )
            )
        frontier_by_seed.append(
            {
                "seed_id": meta[seed_idx].node_id,
                "seed_name": meta[seed_idx].name,
                "exact_depth_counts": exact,
                "cumulative_depth_counts": cumulative,
            }
        )

    max_product_degree = max(directed.degree(idx, mode="all") for idx in product_indices)
    rows: list[dict] = []
    for idx in product_indices:
        if idx in seeds:
            continue
        ds = [
            distance
            for seed_pos in range(len(seeds))
            if (
                distance := _finite_distance(
                    distances[seed_pos][idx], case.max_hops
                )
            )
            is not None
        ]
        if not ds:
            continue
        raw_semantic = (
            float(semantic_all[idx])
            if embedding_available[idx]
            else 0.0
        )
        out_to_seeds, in_from_seeds = _direct_seed_edges(directed, idx, seeds)
        cochange_count = sum(
            1
            for seed_id in case.seed_ids
            if seed_id in cochange.get(meta[idx].node_id, set())
        )
        degree = directed.degree(idx, mode="all")
        row = {
            "idx": idx,
            "id": meta[idx].node_id,
            "name": meta[idx].name,
            "file": meta[idx].file,
            "coverage": len(ds),
            "coverage_ratio": len(ds) / len(seeds),
            "distances": ds,
            "max_distance": max(ds),
            "distance_sum": sum(ds),
            "degree": degree,
            "in_degree": directed.degree(idx, mode="in"),
            "out_degree": directed.degree(idx, mode="out"),
            "hub_norm": math.log1p(degree) / math.log1p(max_product_degree),
            "semantic_raw": raw_semantic,
            "has_exact_embedding": bool(embedding_available[idx]),
            "direct_outgoing_to_seeds": out_to_seeds,
            "direct_incoming_from_seeds": in_from_seeds,
            "cochange_seed_count": cochange_count,
            "cochange_ratio": cochange_count / len(seeds),
            "is_generic_negative": meta[idx].name in case.generic_negative_names,
            "is_expected": idx in expected,
        }
        row["directional_support"] = _directional_support(row, case.direction_mode)
        row["formula_contributions"] = _formula_contributions(row)
        row["formula_totals"] = {
            name: round(_total(parts), 8)
            for name, parts in row["formula_contributions"].items()
        }
        rows.append(row)

    full_coverage = [row for row in rows if row["coverage"] == len(seeds)]
    candidate_pool = full_coverage or rows
    by_idx = {row["idx"]: row for row in rows}

    distance_ranked = sorted(
        (row["idx"] for row in rows),
        key=lambda idx: (
            -by_idx[idx]["coverage"],
            by_idx[idx]["max_distance"],
            by_idx[idx]["distance_sum"],
            by_idx[idx]["degree"],
            idx,
        ),
    )
    semantic_ranked = sorted(
        (row["idx"] for row in candidate_pool),
        key=lambda idx: (-by_idx[idx]["semantic_raw"], idx),
    )
    formula_rankings: dict[str, list[int]] = {}
    for formula_name in (
        "structural",
        "structural_semantic",
        "structural_semantic_hub",
        "all_current_features",
    ):
        formula_rankings[formula_name] = sorted(
            (row["idx"] for row in rows),
            key=lambda idx: (
                -by_idx[idx]["formula_totals"][formula_name],
                by_idx[idx]["max_distance"],
                by_idx[idx]["distance_sum"],
                idx,
            ),
        )

    # This is intentionally lexicographic rather than a fitted weighted sum.
    # Coverage is feasibility; query relevance chooses among feasible nodes;
    # direction evidence breaks semantic ties; compactness is a late tiebreaker.
    pareto_ranked = sorted(
        (row["idx"] for row in rows),
        key=lambda idx: (
            -by_idx[idx]["coverage"],
            -by_idx[idx]["semantic_raw"],
            -by_idx[idx]["directional_support"],
            by_idx[idx]["max_distance"],
            by_idx[idx]["distance_sum"],
            by_idx[idx]["hub_norm"],
            idx,
        ),
    )
    if case.direction_mode == "common_caller":
        mode_conditioned_ranked = sorted(
            (row["idx"] for row in rows),
            key=lambda idx: (
                -by_idx[idx]["coverage"],
                -by_idx[idx]["directional_support"],
                -by_idx[idx]["semantic_raw"],
                by_idx[idx]["max_distance"],
                by_idx[idx]["distance_sum"],
                by_idx[idx]["hub_norm"],
                idx,
            ),
        )
    else:
        mode_conditioned_ranked = pareto_ranked

    def public_row(idx: int) -> dict:
        row = dict(by_idx[idx])
        row.pop("idx", None)
        row["formula_contributions"] = {
            name: {key: round(value, 8) for key, value in values.items()}
            for name, values in row["formula_contributions"].items()
        }
        row["semantic_raw"] = round(row["semantic_raw"], 8)
        row["hub_norm"] = round(row["hub_norm"], 8)
        return row

    max_coverage = max((row["coverage"] for row in rows), default=0)
    route_roots = [row["idx"] for row in rows if row["coverage"] == max_coverage]

    max_degree = max(directed.degree(idx, mode="all") for idx in product_indices)
    hub_norm_all = np.asarray(
        [
            math.log1p(directed.degree(idx, mode="all"))
            / math.log1p(max_degree)
            for idx in range(directed.vcount())
        ],
        dtype=np.float32,
    )
    node_cost = np.maximum(
        0.1,
        1.0
        + 0.25 * hub_norm_all
        - 0.5 * np.maximum(semantic_all, 0.0),
    )
    weighted_graph = undirected if mode == "all" else directed
    weighted_edges = []
    for edge in weighted_graph.es:
        source_idx, target_idx = edge.tuple
        weighted_edges.append(
            float(
                max(
                    0.1,
                    (node_cost[source_idx] + node_cost[target_idx]) / 2.0,
                )
            )
        )

    def route_for_root(root_idx: int, weighted: bool) -> dict | None:
        paths = []
        union_nodes = {root_idx}
        union_edges: set[tuple[int, int]] = set()
        total_hops = 0
        total_cost = 0.0
        for seed_idx in seeds:
            if mode == "in":
                source_idx, target_idx = root_idx, seed_idx
            else:
                source_idx, target_idx = seed_idx, root_idx
            try:
                path = weighted_graph.get_shortest_path(
                    source_idx,
                    to=target_idx,
                    mode="all" if mode == "all" else "out",
                    weights=weighted_edges if weighted else None,
                    output="vpath",
                )
            except Exception:
                path = []
            if not path:
                return None
            path_ids = [meta[idx].node_id for idx in path]
            paths.append(
                {
                    "seed_id": meta[seed_idx].node_id,
                    "path": path_ids,
                    "hops": len(path) - 1,
                }
            )
            total_hops += len(path) - 1
            union_nodes.update(path)
            for left, right in zip(path, path[1:]):
                union_edges.add((min(left, right), max(left, right)))
            if weighted:
                total_cost += sum(
                    weighted_edges[edge_id]
                    for edge_id in weighted_graph.get_shortest_path(
                        source_idx,
                        to=target_idx,
                        mode="all" if mode == "all" else "out",
                        weights=weighted_edges,
                        output="epath",
                    )
                )
        connector_nodes = union_nodes - set(seeds)
        return {
            "root": meta[root_idx].node_id,
            "root_name": meta[root_idx].name,
            "coverage": by_idx[root_idx]["coverage"],
            "paths": paths,
            "union_node_count": len(union_nodes),
            "union_edge_count": len(union_edges),
            "connector_node_count": len(connector_nodes),
            "total_hops": total_hops,
            "weighted_cost": round(total_cost, 8) if weighted else None,
            "connector_semantic_sum": round(
                float(sum(semantic_all[idx] for idx in connector_nodes)), 8
            ),
            "connector_hub_sum": round(
                float(sum(hub_norm_all[idx] for idx in connector_nodes)), 8
            ),
            "root_feature_row": public_row(root_idx),
        }

    def bounded_route_for_root(root_idx: int) -> dict | None:
        path_options: list[list[dict]] = []
        path_audits: list[dict] = []
        any_truncated = False
        for seed_idx in seeds:
            if mode == "in":
                source_idx, target_idx = root_idx, seed_idx
            else:
                source_idx, target_idx = seed_idx, root_idx
            candidates = weighted_graph.get_all_simple_paths(
                source_idx,
                to=target_idx,
                maxlen=case.max_hops,
                mode="all" if mode == "all" else "out",
                max_results=max_path_results,
            )
            product_candidates = [
                path
                for path in candidates
                if all(idx in product_indices or idx in seeds for idx in path)
            ]
            scored = []
            for path in product_candidates:
                edge_ids = [
                    weighted_graph.get_eid(
                        left,
                        right,
                        directed=mode != "all",
                    )
                    for left, right in zip(path, path[1:])
                ]
                cost = float(sum(weighted_edges[edge_id] for edge_id in edge_ids))
                internal = set(path[1:-1])
                scored.append(
                    {
                        "path_indices": path,
                        "edge_ids": edge_ids,
                        "cost": cost,
                        "hops": len(path) - 1,
                        "internal_semantic_sum": float(
                            sum(semantic_all[idx] for idx in internal)
                        ),
                        "internal_hub_sum": float(
                            sum(hub_norm_all[idx] for idx in internal)
                        ),
                    }
                )
            scored.sort(
                key=lambda item: (
                    item["cost"],
                    item["hops"],
                    -item["internal_semantic_sum"],
                    item["internal_hub_sum"],
                    item["path_indices"],
                )
            )
            if not scored:
                return None
            path_options.append(scored)
            truncated = len(candidates) >= max_path_results
            any_truncated = any_truncated or truncated
            path_audits.append(
                {
                    "seed_id": meta[seed_idx].node_id,
                    "candidate_path_count": len(candidates),
                    "product_candidate_path_count": len(product_candidates),
                    "truncated": truncated,
                    "alternatives": [
                        {
                            "path": [meta[idx].node_id for idx in item["path_indices"]],
                            "hops": item["hops"],
                            "cost": round(item["cost"], 8),
                            "internal_semantic_sum": round(
                                item["internal_semantic_sum"], 8
                            ),
                            "internal_hub_sum": round(item["internal_hub_sum"], 8),
                        }
                        for item in scored[:5]
                    ],
                }
            )

        def summarize_combination(combination: tuple[dict, ...]) -> dict:
            union_nodes = {root_idx}
            union_edge_ids: set[int] = set()
            path_cost_sum = 0.0
            total_hops = 0
            for item in combination:
                union_nodes.update(item["path_indices"])
                union_edge_ids.update(item["edge_ids"])
                path_cost_sum += item["cost"]
                total_hops += item["hops"]
            connector_nodes = union_nodes - set(seeds)
            union_cost = float(sum(weighted_edges[eid] for eid in union_edge_ids))
            return {
                "combination": combination,
                "union_nodes": union_nodes,
                "union_edge_ids": union_edge_ids,
                "union_weighted_cost": union_cost,
                "path_cost_sum": path_cost_sum,
                "shared_edge_savings": path_cost_sum - union_cost,
                "union_node_count": len(union_nodes),
                "union_edge_count": len(union_edge_ids),
                "connector_node_count": len(connector_nodes),
                "total_hops": total_hops,
                "connector_semantic_sum": float(
                    sum(semantic_all[idx] for idx in connector_nodes)
                ),
                "connector_hub_sum": float(
                    sum(hub_norm_all[idx] for idx in connector_nodes)
                ),
            }

        def combination_key(summary: dict) -> tuple:
            return (
                summary["union_weighted_cost"],
                summary["union_edge_count"],
                summary["union_node_count"],
                summary["total_hops"],
                -summary["connector_semantic_sum"],
                summary["connector_hub_sum"],
                tuple(
                    tuple(item["path_indices"])
                    for item in summary["combination"]
                ),
            )

        def public_combination(summary: dict) -> dict:
            selected_paths = []
            for seed_idx, item in zip(seeds, summary["combination"]):
                selected_paths.append(
                    {
                        "seed_id": meta[seed_idx].node_id,
                        "path": [meta[idx].node_id for idx in item["path_indices"]],
                        "hops": item["hops"],
                        "path_cost": round(item["cost"], 8),
                    }
                )
            union_edges = []
            for edge_id in sorted(summary["union_edge_ids"]):
                source_idx, target_idx = weighted_graph.es[edge_id].tuple
                attributes = weighted_graph.es[edge_id].attributes()
                current_status = attributes.get("current_status")
                if (
                    current_status is None
                    and mode == "all"
                    and "current_status" in directed.es.attributes()
                ):
                    statuses = set()
                    for left, right in (
                        (source_idx, target_idx),
                        (target_idx, source_idx),
                    ):
                        directed_edge_id = directed.get_eid(
                            left, right, directed=True, error=False
                        )
                        if directed_edge_id >= 0:
                            statuses.add(directed.es[directed_edge_id]["current_status"])
                    if len(statuses) == 1:
                        current_status = next(iter(statuses))
                    elif statuses:
                        current_status = sorted(statuses)
                union_edges.append(
                    {
                        "source": meta[source_idx].node_id,
                        "target": meta[target_idx].node_id,
                        "edge_cost": round(weighted_edges[edge_id], 8),
                        "current_status": current_status,
                    }
                )
            return {
                "paths": selected_paths,
                "union_edges": union_edges,
                "union_node_count": summary["union_node_count"],
                "union_edge_count": summary["union_edge_count"],
                "connector_node_count": summary["connector_node_count"],
                "total_hops": summary["total_hops"],
                "union_weighted_cost": round(summary["union_weighted_cost"], 8),
                "path_cost_sum": round(summary["path_cost_sum"], 8),
                "shared_edge_savings": round(summary["shared_edge_savings"], 8),
                "connector_semantic_sum": round(
                    summary["connector_semantic_sum"], 8
                ),
                "connector_hub_sum": round(summary["connector_hub_sum"], 8),
            }

        independent = summarize_combination(tuple(options[0] for options in path_options))
        combination_count_total = math.prod(len(options) for options in path_options)
        evaluated_combinations = 0
        best_summary = None
        best_key = None
        top_summaries: list[tuple[tuple, dict]] = []
        combinations = product(*path_options)
        for combination in islice(combinations, max_union_combinations):
            evaluated_combinations += 1
            summary = summarize_combination(combination)
            key = combination_key(summary)
            if best_key is None or key < best_key:
                best_key = key
                best_summary = summary
            top_summaries.append((key, summary))
            top_summaries.sort(key=lambda item: item[0])
            del top_summaries[5:]
        if best_summary is None:
            return None

        combination_truncated = evaluated_combinations < combination_count_total
        exact = not any_truncated and not combination_truncated
        for audit, independent_item, selected_item in zip(
            path_audits, independent["combination"], best_summary["combination"]
        ):
            audit["independent_selected_path"] = [
                meta[idx].node_id for idx in independent_item["path_indices"]
            ]
            audit["selected_path"] = [
                meta[idx].node_id for idx in selected_item["path_indices"]
            ]
            audit["selection_changed_by_union_optimizer"] = (
                independent_item["path_indices"] != selected_item["path_indices"]
            )
            audit["selected_hops"] = selected_item["hops"]
            audit["selected_cost"] = round(selected_item["cost"], 8)

        selected_public = public_combination(best_summary)
        return {
            "root": meta[root_idx].node_id,
            "root_name": meta[root_idx].name,
            "root_feature_row": public_row(root_idx),
            "paths": path_audits,
            "union_node_count": selected_public["union_node_count"],
            "union_edge_count": selected_public["union_edge_count"],
            "connector_node_count": selected_public["connector_node_count"],
            "total_hops": selected_public["total_hops"],
            "total_cost": selected_public["union_weighted_cost"],
            "any_truncated": any_truncated,
            "independent_path_selection": public_combination(independent),
            "union_optimization": {
                "objective": (
                    "union weighted edge cost, then union edges, union nodes, total hops, "
                    "connector semantic sum, connector hub sum, deterministic path IDs"
                ),
                "combination_count_total": combination_count_total,
                "combination_count_evaluated": evaluated_combinations,
                "combination_truncated": combination_truncated,
                "exact_within_declared_bounds": exact,
                "exactness_scope": (
                    "fixed selected root; simple product-code paths within max_hops and "
                    "the declared path and combination caps"
                ),
                "selected": selected_public,
                "top_combinations": [
                    public_combination(summary) for _, summary in top_summaries
                ],
            },
            "within_declared_hop_budget": all(
                path["selected_hops"] <= case.max_hops for path in path_audits
            ),
        }

    structural_routes = [
        route_for_root(root_idx, weighted=False) for root_idx in route_roots
    ]
    weighted_routes = [
        route_for_root(root_idx, weighted=True) for root_idx in route_roots
    ]
    structural_routes = [route for route in structural_routes if route]
    weighted_routes = [route for route in weighted_routes if route]
    structural_routes.sort(
        key=lambda route: (
            -route["coverage"],
            route["union_edge_count"],
            route["total_hops"],
            route["union_node_count"],
            route["root"],
        )
    )
    weighted_routes.sort(
        key=lambda route: (
            -route["coverage"],
            route["weighted_cost"],
            route["union_edge_count"],
            route["total_hops"],
            route["root"],
        )
    )
    expected_route_ids = expected | {
        id_to_idx[node_id]
        for node_id in case.acceptable_nodes
        if node_id in id_to_idx
    }
    structural_route_rank = next(
        (
            rank
            for rank, route in enumerate(structural_routes, 1)
            if id_to_idx.get(route["root"]) in expected_route_ids
        ),
        None,
    )
    weighted_route_rank = next(
        (
            rank
            for rank, route in enumerate(weighted_routes, 1)
            if id_to_idx.get(route["root"]) in expected_route_ids
        ),
        None,
    )
    selected_root_idx = mode_conditioned_ranked[0] if mode_conditioned_ranked else None
    selected_root_route = (
        bounded_route_for_root(selected_root_idx)
        if selected_root_idx is not None
        else None
    )
    expected_root_routes = [
        route
        for idx in sorted(expected_route_ids)
        if (route := bounded_route_for_root(idx)) is not None
    ]

    expected_rows = [public_row(idx) for idx in expected if idx in by_idx]
    return {
        "case_id": case.case_id,
        "query": case.query,
        "direction_mode": case.direction_mode,
        "igraph_distance_mode": mode,
        "max_hops": case.max_hops,
        "source_evidence": list(case.source_evidence),
        "seeds": [
            {"id": meta[idx].node_id, "name": meta[idx].name, "file": meta[idx].file}
            for idx in seeds
        ],
        "expected_ids": list(case.expected_nodes),
        "acceptable_ids": list(case.acceptable_nodes),
        "frontier_by_seed": frontier_by_seed,
        "candidate_counts": {
            "reached_by_any_seed": len(rows),
            "full_seed_coverage": len(full_coverage),
            "semantic_candidate_pool": len(candidate_pool),
        },
        "route_summary": {
            "max_coverage": max_coverage,
            "structural_route_candidates": len(structural_routes),
            "weighted_route_candidates": len(weighted_routes),
            "structural_route_expected_rank": structural_route_rank,
            "weighted_route_expected_rank": weighted_route_rank,
            "selected_root_id": (
                meta[selected_root_idx].node_id if selected_root_idx is not None else None
            ),
            "selected_root_is_expected": selected_root_idx in expected_route_ids
            if selected_root_idx is not None
            else False,
        },
        "expected_rows": expected_rows,
        "ranks": {
            "distance_lexicographic": _rank(distance_ranked, expected),
            "semantic_only_within_full_coverage": _rank(semantic_ranked, expected),
            **{
                name: _rank(ranking, expected)
                for name, ranking in formula_rankings.items()
            },
            "coverage_semantic_direction": _rank(pareto_ranked, expected),
            "mode_conditioned_lexicographic": _rank(
                mode_conditioned_ranked, expected
            ),
        },
        "top": {
            "distance_lexicographic": [public_row(idx) for idx in distance_ranked[:10]],
            "semantic_only_within_full_coverage": [
                public_row(idx) for idx in semantic_ranked[:10]
            ],
            **{
                name: [public_row(idx) for idx in ranking[:10]]
                for name, ranking in formula_rankings.items()
            },
            "coverage_semantic_direction": [
                public_row(idx) for idx in pareto_ranked[:10]
            ],
            "mode_conditioned_lexicographic": [
                public_row(idx) for idx in mode_conditioned_ranked[:10]
            ],
            "structural_routes": structural_routes[:5],
            "weighted_routes": weighted_routes[:5],
            "selected_root_bounded_route": selected_root_route,
            "expected_root_bounded_routes": expected_root_routes,
        },
    }


def helix_traversal_audit(
    helix_url: str, case: QueryCase, depth_limit: int
) -> dict:
    client = Client(helix_url)
    params = define_params({"fid": param.string()})
    direction = _mode_for_case(case)

    def traversal_step():
        sub = SubTraversal.new()
        if direction == "out":
            return sub.out("CALLS")
        if direction == "in":
            return sub.in_("CALLS")
        return sub.both("CALLS")

    rows = []
    for seed_id in case.seed_ids:
        for depth in range(1, depth_limit + 1):
            query = (
                read_batch()
                .var_as(
                    "nodes",
                    g()
                    .n_with_label("FunctionIdentity")
                    .where(Predicate.eq_param("node_id", "fid"))
                    .repeat(
                        RepeatConfig.new(traversal_step()).times(depth).emit_all()
                    )
                    .dedup()
                    .limit(20_000)
                    .project(
                        [
                            Projection.property("node_id"),
                            Projection.property("code_vector_source_node_id"),
                        ]
                    ),
                )
                .returning(["nodes"])
            )
            t0 = time.perf_counter()
            result = client.query().dynamic(
                query.to_dynamic_request(params, {"fid": seed_id})
            ).send()
            elapsed_ms = (time.perf_counter() - t0) * 1000
            raw = result.get("nodes", {}).get("properties", [])
            canonical = {
                item.get("code_vector_source_node_id") or item.get("node_id")
                for item in raw
                if item.get("code_vector_source_node_id") or item.get("node_id")
            }
            rows.append(
                {
                    "seed_id": seed_id,
                    "depth": depth,
                    "raw_rows": len(raw),
                    "canonical_unique": len(canonical),
                    "elapsed_ms": round(elapsed_ms, 2),
                    "hit_limit": len(raw) >= 20_000,
                }
            )
    return {
        "case_id": case.case_id,
        "direction_mode": case.direction_mode,
        "depth_limit": depth_limit,
        "rows": rows,
    }


def _print_case_summary(case: dict) -> None:
    print("\n" + "=" * 88)
    print(case["case_id"])
    if case.get("error"):
        print(json.dumps(case, indent=2))
        return
    print(case["query"])
    print("direction:", case["direction_mode"], "max_hops:", case["max_hops"])
    print("candidate counts:", case["candidate_counts"])
    print("route summary:", case["route_summary"])
    print("ranks:", json.dumps(case["ranks"], indent=2))
    print("expected feature rows:")
    print(json.dumps(case["expected_rows"], indent=2))
    print("top all_current_features:")
    for row in case["top"]["all_current_features"][:5]:
        print(
            f"  {row['name']:<38} total={row['formula_totals']['all_current_features']:.5f} "
            f"cov={row['coverage']} maxd={row['max_distance']} sum={row['distance_sum']} "
            f"sem={row['semantic_raw']:.5f} degree={row['degree']} "
            f"co={row['cochange_seed_count']} generic={row['is_generic_negative']}"
        )
    print("top weighted routes:")
    for route in case["top"]["weighted_routes"][:3]:
        print(
            f"  {route['root_name']:<38} cost={route['weighted_cost']:.5f} "
            f"edges={route['union_edge_count']} hops={route['total_hops']} "
            f"nodes={route['union_node_count']}"
        )
        for path in route["paths"]:
            print("    ", " -> ".join(path["path"]))
    bounded = case["top"].get("selected_root_bounded_route")
    if bounded:
        union = bounded["union_optimization"]
        selected = union["selected"]
        print(
            "fixed-root union:",
            f"exact={union['exact_within_declared_bounds']}",
            f"combinations={union['combination_count_evaluated']}/"
            f"{union['combination_count_total']}",
            f"cost={selected['union_weighted_cost']:.5f}",
            f"edges={selected['union_edge_count']}",
            f"nodes={selected['union_node_count']}",
        )
        for path in selected["paths"]:
            print("    ", " -> ".join(path["path"]))


def main() -> int:
    run_started = time.perf_counter()
    parser = argparse.ArgumentParser()
    parser.add_argument("--helix-url", default=DEFAULT_HELIX_URL)
    parser.add_argument(
        "--edge-view",
        choices=("raw", "current-overlay"),
        default="current-overlay",
    )
    parser.add_argument("--source-repo", type=Path, default=DEFAULT_SOURCE_REPO)
    parser.add_argument("--case", action="append", help="Run only the named case")
    parser.add_argument("--cases-file", type=Path, default=CASES_PATH)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-path-results", type=int, default=5_000)
    parser.add_argument("--max-union-combinations", type=int, default=100_000)
    parser.add_argument(
        "--helix-audit-case",
        help="Also run actual Helix repeat().emit_all() traversal for this case",
    )
    parser.add_argument("--helix-audit-depth", type=int, default=3)
    args = parser.parse_args()
    if args.max_path_results <= 0 or args.max_union_combinations <= 0:
        raise SystemExit("Path and union enumeration caps must be positive")

    raw_cases = json.loads(args.cases_file.read_text(encoding="utf-8"))
    cases = [
        QueryCase(
            case_id=row["case_id"],
            query=row["query"],
            direction_mode=row["direction_mode"],
            max_hops=int(row["max_hops"]),
            seed_ids=tuple(row["seed_ids"]),
            expected_nodes=tuple(row["expected_nodes"]),
            acceptable_nodes=tuple(row.get("acceptable_nodes", [])),
            generic_negative_names=tuple(row.get("generic_negative_names", [])),
            source_evidence=tuple(row.get("source_evidence", [])),
        )
        for row in raw_cases
    ]
    if args.case:
        selected = set(args.case)
        cases = [case for case in cases if case.case_id in selected]
    if not cases:
        raise SystemExit("No query cases selected")

    raw_directed, raw_undirected, meta, id_to_idx, helix_diagnostics = (
        load_live_helix_calls(args.helix_url)
    )
    overlay_diagnostics = None
    overlay_ms = 0.0
    if args.edge_view == "current-overlay":
        overlay_started = time.perf_counter()
        directed, overlay_diagnostics = build_current_calls_overlay(
            raw_directed, meta, args.source_repo
        )
        overlay_ms = (time.perf_counter() - overlay_started) * 1000
        undirected = directed.as_undirected(mode="collapse")
    else:
        directed, undirected = raw_directed, raw_undirected
    product_indices = {idx for idx, item in enumerate(meta) if _is_product_node(item)}
    if args.edge_view == "current-overlay":
        product_indices = {
            idx
            for idx in product_indices
            if directed.vs[idx]["current_source_available"]
        }
    vector_started = time.perf_counter()
    embeddings, embedding_available, vector_diagnostics = load_embedding_matrix(meta)
    vector_load_ms = (time.perf_counter() - vector_started) * 1000
    embedding_started = time.perf_counter()
    query_vectors = embed_queries(cases)
    query_embedding_ms = (time.perf_counter() - embedding_started) * 1000
    cochange = load_local_cochange()

    evaluation_started = time.perf_counter()
    evaluated = [
        evaluate_case(
            case,
            query_vectors[pos],
            directed,
            undirected,
            meta,
            id_to_idx,
            product_indices,
            embeddings,
            embedding_available,
            cochange,
            max_path_results=args.max_path_results,
            max_union_combinations=args.max_union_combinations,
        )
        for pos, case in enumerate(cases)
    ]
    evaluation_ms = (time.perf_counter() - evaluation_started) * 1000

    traversal_audit = None
    if args.helix_audit_case:
        audit_case = next(
            (case for case in cases if case.case_id == args.helix_audit_case),
            None,
        )
        if audit_case is None:
            raise SystemExit(f"Unknown --helix-audit-case {args.helix_audit_case}")
        traversal_audit = helix_traversal_audit(
            args.helix_url, audit_case, args.helix_audit_depth
        )

    result = {
        "experiment": "query_conditioned_connector_feature_ablation",
        "stage": "fixed_root_bounded_union_optimization",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "graph": "live_helix_calls",
            "edge_view": args.edge_view,
            "current_source_overlay": overlay_diagnostics,
            "semantic_vectors": str(LEGACY_CODE_VECTORS),
            "semantic_vector_note": (
                "Exact legacy vectors are used for experimental introspection only; "
                "runtime retrieval should use TurboVec."
            ),
        },
        "enumeration_limits": {
            "max_path_results_per_seed": args.max_path_results,
            "max_union_combinations": args.max_union_combinations,
        },
        "diagnostics": {
            **helix_diagnostics,
            **vector_diagnostics,
            "active_call_edges": directed.ecount(),
            "product_nodes": len(product_indices),
            "local_cochange_pairs": sum(len(values) for values in cochange.values()) // 2,
            "current_overlay_build_ms": round(overlay_ms, 2),
            "vector_load_ms": round(vector_load_ms, 2),
            "query_embedding_ms": round(query_embedding_ms, 2),
            "case_evaluation_ms": round(evaluation_ms, 2),
            "total_run_ms": round((time.perf_counter() - run_started) * 1000, 2),
        },
        "formula_definitions": {
            "structural": "100*coverage_ratio + 4/(1+max_distance) + 2/(1+distance_sum)",
            "structural_semantic": "structural + 2*raw_query_cosine",
            "structural_semantic_hub": (
                "structural_semantic - 0.75*log_degree_norm - 3*generic_name"
            ),
            "all_current_features": "structural_semantic_hub + 0.5*cochange_ratio",
            "coverage_semantic_direction": (
                "lexicographic: coverage, query cosine, direction-specific seed-edge support, "
                "max distance, distance sum, hub norm"
            ),
            "mode_conditioned_lexicographic": (
                "common_caller: coverage, direct outgoing seed support, query cosine, compactness; "
                "other modes: coverage, query cosine, direction support, compactness"
            ),
            "fixed_root_union_optimization": (
                "after mode-conditioned root selection, enumerate bounded simple paths for each "
                "seed and minimize the cost of their unique union edges"
            ),
        },
        "cases": evaluated,
        "helix_traversal_audit": traversal_audit,
    }

    print("QUERY-CONDITIONED CONNECTOR FEATURE AUDIT")
    print(json.dumps(result["diagnostics"], indent=2))
    for case in evaluated:
        _print_case_summary(case)
    if traversal_audit:
        print("\nACTUAL HELIX TRAVERSAL AUDIT")
        print(json.dumps(traversal_audit, indent=2))

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print("\nWrote", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
