"""Bounded, retryable graph refresh for accepted changed-file scopes."""

from __future__ import annotations

import ast
import hashlib
import textwrap
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Protocol

import git

from work_affinity import FunctionCatalog, FunctionSpan, GitRangeAnalysis


STRUCTURAL_LABELS = ("CALLS",)
MAX_REFRESH_FUNCTIONS = 2_000


class CodeEmbedder(Protocol):
    def encode(self, texts: list[str]) -> list[list[float]]: ...


class GraphRefreshBackend(Protocol):
    def resolve_function_names(self, names: Iterable[str]) -> dict[str, list[str]]: ...

    def upsert_function(self, function: "FunctionRefreshRecord") -> None: ...

    def replace_outgoing_calls(self, source_id: str, target_ids: Iterable[str]) -> None: ...

    def mark_inactive(self, function_id: str) -> None: ...

    def finalize(self) -> None: ...


@dataclass(frozen=True)
class FunctionRefreshRecord:
    node_id: str
    name: str
    file_path: str
    source: str
    code_hash: str
    code_vector: tuple[float, ...]
    head_revision: str


@dataclass(frozen=True)
class GraphRefreshResult:
    changed_python_files: tuple[str, ...]
    refreshed_functions: tuple[str, ...]
    refreshed_edges: tuple[tuple[str, str, str], ...]
    inactive_functions: tuple[str, ...]
    ambiguous_functions: tuple[str, ...]
    unresolved_calls: tuple[tuple[str, str], ...]
    structural_labels: tuple[str, ...] = STRUCTURAL_LABELS

    def to_stats(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.update(
            {
                "refreshed_function_count": len(self.refreshed_functions),
                "refreshed_edge_count": len(self.refreshed_edges),
                "inactive_function_count": len(self.inactive_functions),
                "unresolved_call_count": len(self.unresolved_calls),
            }
        )
        return payload


class SentenceTransformerEmbedder:
    """Lazy local embedding shared with the existing graphdb search path."""

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._model = None

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        vectors = self._model.encode(
            texts,
            show_progress_bar=False,
            batch_size=min(256, max(1, len(texts))),
            normalize_embeddings=True,
        )
        return [vector.tolist() for vector in vectors]


class GitSnapshotReader:
    """Read logical outer-repository paths at an exact accepted revision."""

    def __init__(self, repo_root: str | Path):
        self.repo_root = Path(repo_root).resolve()
        self.repo = git.Repo(self.repo_root)

    def read_text(self, revision: str, logical_path: str) -> str:
        commit = self.repo.commit(revision)
        checkout = self.repo_root
        remaining = list(PurePosixPath(logical_path).parts)
        while remaining:
            relative = "/".join(remaining)
            try:
                entry = commit.tree / relative
            except KeyError:
                entry = None
            if entry is not None and entry.type == "blob":
                return entry.data_stream.read().decode("utf-8", errors="replace")

            submodule_index = None
            submodule_entry = None
            for index in range(1, len(remaining) + 1):
                prefix = "/".join(remaining[:index])
                try:
                    candidate = commit.tree / prefix
                except KeyError:
                    break
                if candidate.type == "submodule":
                    submodule_index = index
                    submodule_entry = candidate
                    break
            if submodule_index is None or submodule_entry is None:
                raise FileNotFoundError(
                    f"{logical_path} is unavailable at accepted revision {revision}"
                )
            checkout = checkout.joinpath(*remaining[:submodule_index])
            try:
                nested_repo = git.Repo(checkout)
                commit = nested_repo.commit(submodule_entry.hexsha)
            except Exception as exc:
                raise FileNotFoundError(
                    f"submodule checkout for {logical_path} lacks {submodule_entry.hexsha}"
                ) from exc
            remaining = remaining[submodule_index:]
        raise FileNotFoundError(logical_path)


def _call_references(source: str) -> tuple[tuple[str, str], ...]:
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return ()
    references: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            references.add((node.func.id, "direct"))
        elif isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id in {
                "self",
                "cls",
            }:
                references.add((node.func.attr, "same_file"))
            else:
                references.add((node.func.attr, "dynamic_attribute"))
    return tuple(sorted(references))


class ChangedScopeGraphRefresher:
    """Refresh all functions in accepted changed Python files, not the full graph."""

    def __init__(
        self,
        repo_root: str | Path,
        repo_id: str,
        backend: GraphRefreshBackend,
        *,
        embedder: CodeEmbedder | None = None,
        max_functions: int = MAX_REFRESH_FUNCTIONS,
    ):
        self.repo_root = Path(repo_root).resolve()
        self.repo_id = repo_id
        self.backend = backend
        self.embedder = embedder or SentenceTransformerEmbedder()
        self.max_functions = max_functions
        self.snapshot = GitSnapshotReader(self.repo_root)

    def refresh(self, git_range: GitRangeAnalysis) -> GraphRefreshResult:
        python_files = tuple(
            sorted(path for path in git_range.changed_files if path.endswith(".py"))
        )
        catalog = FunctionCatalog(self.repo_root, self.repo_id)
        spans: list[FunctionSpan] = []
        ambiguous: list[str] = []
        for file_path in python_files:
            try:
                source = self.snapshot.read_text(git_range.head_revision, file_path)
            except FileNotFoundError:
                try:
                    self.snapshot.read_text(git_range.base_revision, file_path)
                except FileNotFoundError:
                    raise
                continue
            parsed = catalog.parse_source(file_path, source)
            grouped: dict[str, list[FunctionSpan]] = defaultdict(list)
            for span in parsed:
                grouped[span.node_id].append(span)
            for node_id, rows in sorted(grouped.items()):
                if len(rows) == 1:
                    spans.append(rows[0])
                else:
                    ambiguous.append(f"{file_path}::{rows[0].name}")

        if len(spans) > self.max_functions:
            raise RuntimeError(
                f"changed-scope refresh exceeds {self.max_functions} functions: {len(spans)}"
            )

        vectors = self.embedder.encode([span.source[:4_000] for span in spans])
        if len(vectors) != len(spans):
            raise RuntimeError(
                f"embedder returned {len(vectors)} vectors for {len(spans)} functions"
            )
        records = [
            FunctionRefreshRecord(
                node_id=span.node_id,
                name=span.name,
                file_path=span.file_path,
                source=span.source,
                code_hash=hashlib.sha1(span.source.encode("utf-8")).hexdigest()[:12],
                code_vector=tuple(float(value) for value in vector),
                head_revision=git_range.head_revision,
            )
            for span, vector in zip(spans, vectors)
        ]

        by_file_name: dict[tuple[str, str], list[str]] = defaultdict(list)
        by_name: dict[str, list[str]] = defaultdict(list)
        call_names: set[str] = set()
        calls_by_source: dict[str, tuple[tuple[str, str], ...]] = {}
        for record in records:
            by_file_name[(record.file_path, record.name)].append(record.node_id)
            by_name[record.name].append(record.node_id)
            calls = _call_references(record.source)
            calls_by_source[record.node_id] = calls
            call_names.update(
                name for name, resolution in calls if resolution == "direct"
            )
        external = self.backend.resolve_function_names(call_names)

        edges: set[tuple[str, str, str]] = set()
        unresolved: set[tuple[str, str]] = set()
        record_by_id = {record.node_id: record for record in records}
        targets_by_source: dict[str, set[str]] = defaultdict(set)
        for source_id, references in sorted(calls_by_source.items()):
            source = record_by_id[source_id]
            for name, resolution in references:
                if resolution == "dynamic_attribute":
                    unresolved.add((source_id, f"attribute:{name}"))
                    continue
                same_file = by_file_name.get((source.file_path, name), [])
                candidates = same_file if len(same_file) == 1 else []
                if not candidates and resolution == "direct":
                    local = by_name.get(name, [])
                    combined = sorted(set(local) | set(external.get(name, [])))
                    candidates = combined if len(combined) == 1 else []
                if len(candidates) == 1 and candidates[0] != source_id:
                    targets_by_source[source_id].add(candidates[0])
                    edges.add((source_id, candidates[0], "CALLS"))
                elif not candidates:
                    unresolved.add((source_id, name))

        for source in records:
            self.backend.upsert_function(source)
        for source in records:
            self.backend.replace_outgoing_calls(
                source.node_id, sorted(targets_by_source[source.node_id])
            )

        refreshed_ids = {record.node_id for record in records}
        inactive = sorted(set(git_range.removed_functions) - refreshed_ids)
        for function_id in inactive:
            self.backend.replace_outgoing_calls(function_id, ())
            self.backend.mark_inactive(function_id)
        self.backend.finalize()

        return GraphRefreshResult(
            changed_python_files=python_files,
            refreshed_functions=tuple(sorted(refreshed_ids)),
            refreshed_edges=tuple(sorted(edges)),
            inactive_functions=tuple(inactive),
            ambiguous_functions=tuple(sorted(set(ambiguous))),
            unresolved_calls=tuple(sorted(unresolved)),
        )


class HelixGraphRefreshBackend:
    """Absolute FunctionIdentity and outgoing CALLS replacement in HelixDB."""

    def __init__(self, url: str = "http://127.0.0.1:6969", *, vector_index=None):
        from helixdb import Client

        self.url = url
        self.client = Client(url)
        if vector_index is None:
            import turbovec_adapter

            vector_index = turbovec_adapter.code_index
        self.vector_index = vector_index

    @staticmethod
    def _rows(response: dict[str, Any], key: str) -> list[dict[str, Any]]:
        return response.get(key, {}).get("properties", [])

    def _find_node(self, function_id: str) -> list[dict[str, Any]]:
        from helixdb import Predicate, Projection, g, read_batch

        response = self.client.query().dynamic(
            read_batch()
            .var_as(
                "function",
                g()
                .n_with_label("FunctionIdentity")
                .where(Predicate.eq("node_id", function_id))
                .limit(2)
                .project([Projection.property("node_id")]),
            )
            .returning(["function"])
            .to_dynamic_request()
        ).send()
        return self._rows(response, "function")

    def resolve_function_names(self, names: Iterable[str]) -> dict[str, list[str]]:
        from helixdb import Predicate, Projection, g, read_batch

        resolved: dict[str, list[str]] = {}
        unique_names = sorted(set(names))
        for offset in range(0, len(unique_names), 100):
            batch = read_batch()
            returns: list[str] = []
            key_to_name: dict[str, str] = {}
            for index, name in enumerate(unique_names[offset : offset + 100]):
                key = f"functions_{index}"
                key_to_name[key] = name
                returns.append(key)
                batch = batch.var_as(
                    key,
                    g()
                    .n_with_label("FunctionIdentity")
                    .where(Predicate.eq("name", name))
                    .limit(3)
                    .project([Projection.property("node_id")]),
                )
            response = self.client.query().dynamic(
                batch.returning(returns).to_dynamic_request()
            ).send()
            for key, name in key_to_name.items():
                resolved[name] = sorted(
                    row["node_id"]
                    for row in self._rows(response, key)
                    if row.get("node_id")
                )
        return resolved

    @staticmethod
    def _properties(function: FunctionRefreshRecord) -> dict[str, Any]:
        from helixdb import PropertyInput, PropertyValue

        vector_id = str(function.node_id)
        return {
            "node_id": PropertyInput.value(function.node_id),
            "name": PropertyInput.value(function.name),
            "file": PropertyInput.value(function.file_path),
            "active": PropertyInput.value(True),
            "code_hash": PropertyInput.value(function.code_hash),
            "indexed_revision": PropertyInput.value(function.head_revision),
            "code_vector_source_node_id": PropertyInput.value(vector_id),
            "code_vec": PropertyInput.value(
                PropertyValue.f32_array(function.code_vector)
            ),
        }

    def upsert_function(self, function: FunctionRefreshRecord) -> None:
        from helixdb import Predicate, PropertyInput, g, write_batch

        vector_id = self.vector_index.insert(function.node_id, function.code_vector)
        properties = self._properties(function)
        properties["code_vector_id"] = PropertyInput.value(str(vector_id))
        batch = write_batch()
        if self._find_node(function.node_id):
            traversal = g().n_with_label("FunctionIdentity").where(
                Predicate.eq("node_id", function.node_id)
            )
            for name, value in properties.items():
                traversal = traversal.set_property(name, value)
            batch = batch.var_as("function", traversal)
        else:
            batch = batch.var_as(
                "function", g().add_n("FunctionIdentity", properties)
            )
        response = self.client.query().dynamic(
            batch.returning(["function"]).to_dynamic_request()
        ).send()
        if not response.get("function"):
            raise RuntimeError(f"Helix failed to upsert FunctionIdentity {function.node_id}")

    def _outgoing_call_targets(self, source_id: str) -> list[str]:
        from helixdb import Predicate, Projection, g, read_batch

        response = self.client.query().dynamic(
            read_batch()
            .var_as(
                "targets",
                g()
                .n_with_label("FunctionIdentity")
                .where(Predicate.eq("node_id", source_id))
                .out_e("CALLS")
                .other_n()
                .project([Projection.property("node_id")]),
            )
            .returning(["targets"])
            .to_dynamic_request()
        ).send()
        return sorted(
            row["node_id"]
            for row in self._rows(response, "targets")
            if row.get("node_id")
        )

    def replace_outgoing_calls(self, source_id: str, target_ids: Iterable[str]) -> None:
        from helixdb import NodeRef, Predicate, define_params, g, param, write_batch

        desired = sorted(set(target_ids))
        if not self._find_node(source_id):
            if desired:
                raise RuntimeError(f"missing source FunctionIdentity: {source_id}")
            return
        existing = self._outgoing_call_targets(source_id)
        all_targets = sorted(set(existing) | set(desired))
        params = {"source_id": param.string()}
        values = {"source_id": source_id}
        batch = write_batch().var_as(
            "source",
            g().n_with_label("FunctionIdentity").where(
                Predicate.eq_param("node_id", "source_id")
            ),
        )
        returns = ["source"]
        target_vars: dict[str, str] = {}
        for index, target_id in enumerate(all_targets):
            param_name = f"target_id_{index}"
            var_name = f"target_{index}"
            params[param_name] = param.string()
            values[param_name] = target_id
            target_vars[target_id] = var_name
            batch = batch.var_as(
                var_name,
                g().n_with_label("FunctionIdentity").where(
                    Predicate.eq_param("node_id", param_name)
                ),
            )
            returns.append(var_name)
            if target_id in existing:
                removed_name = f"removed_{index}"
                batch = batch.var_as(
                    removed_name,
                    g().n(NodeRef.var("source")).drop_edge_labeled(
                        NodeRef.var(var_name), "CALLS"
                    ),
                )
                returns.append(removed_name)
        for index, target_id in enumerate(desired):
            edge_name = f"edge_{index}"
            batch = batch.var_as(
                edge_name,
                g().n(NodeRef.var("source")).add_e(
                    "CALLS", NodeRef.var(target_vars[target_id]), {}
                ),
            )
            returns.append(edge_name)
        response = self.client.query().dynamic(
            batch.returning(returns).to_dynamic_request(
                define_params(params), values
            )
        ).send()
        if not response.get("source"):
            raise RuntimeError(f"missing source FunctionIdentity: {source_id}")
        for target_id, var_name in target_vars.items():
            if not response.get(var_name):
                raise RuntimeError(f"missing target FunctionIdentity: {target_id}")

    def mark_inactive(self, function_id: str) -> None:
        from helixdb import Predicate, g, write_batch

        if not self._find_node(function_id):
            return
        response = self.client.query().dynamic(
            write_batch()
            .var_as(
                "function",
                g()
                .n_with_label("FunctionIdentity")
                .where(Predicate.eq("node_id", function_id))
                .set_property("active", False),
            )
            .returning(["function"])
            .to_dynamic_request()
        ).send()
        if not response.get("function"):
            raise RuntimeError(f"Helix failed to deactivate {function_id}")
        self.vector_index.remove(function_id)

    def finalize(self) -> None:
        self.vector_index.save()
