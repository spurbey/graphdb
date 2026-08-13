"""Deterministic post-accepted-work signal extraction and affinity learning."""

from __future__ import annotations

import ast
import re
import shlex
import textwrap
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping

import git

from accepted_work import Job, PermanentWorkError, WorkLedger
from transcript_adapters import NormalizedActivity, NormalizedTranscript, parse_transcript_file


MAX_CHANGED_FUNCTIONS = 20
MAX_NON_CHANGED_SOURCES = 25
ROLE_STRENGTH = {
    "changed": 1.0,
    "targeted_operation": 0.6,
    "repeated_read": 0.25,
    "single_read": 0.1,
}
_PYTHON_REF_RE = re.compile(
    r"(?P<path>(?:[A-Za-z]:[\\/])?[^\s\"']+?\.py)(?:::(?P<symbol>[A-Za-z_]\w*))?"
)


def normalize_repo_path(path: str | Path, repo_root: Path) -> str | None:
    raw = str(path).strip().strip('"\'').replace("\\", "/")
    if not raw:
        return None
    try:
        candidate = Path(raw)
        if candidate.is_absolute():
            resolved = candidate.resolve(strict=False)
            root = repo_root.resolve(strict=False)
            try:
                raw = resolved.relative_to(root).as_posix()
            except ValueError:
                return None
        else:
            raw = PurePosixPath(raw).as_posix()
    except (OSError, ValueError):
        return None
    while raw.startswith("./"):
        raw = raw[2:]
    if raw.startswith("../") or raw == "..":
        return None
    return raw


def function_id(
    repo_id: str,
    file_path: str,
    name: str,
    *,
    qualified_name: str | None = None,
) -> str:
    safe = file_path.replace("/", "_").replace("\\", "_").replace(".py", "")
    safe_symbol = (qualified_name or name).replace(".", "_")
    return f"{repo_id}:func_{safe}_{safe_symbol}"


@dataclass(frozen=True)
class FunctionSpan:
    file_path: str
    name: str
    qualified_name: str
    owner_qualified_name: str | None
    start_line: int
    end_line: int
    source: str
    node_id: str


class FunctionCatalog:
    """Lazy current-source function lookup for only the files an episode touches."""

    def __init__(
        self,
        repo_root: str | Path,
        repo_id: str,
        *,
        source_loader: Callable[[str], str] | None = None,
    ):
        self.repo_root = Path(repo_root).resolve()
        self.repo_id = repo_id
        self.source_loader = source_loader
        self._cache: dict[str, list[FunctionSpan]] = {}

    def parse_source(self, file_path: str, source: str) -> list[FunctionSpan]:
        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError:
            return []
        lines = source.splitlines(keepends=True)
        spans: list[FunctionSpan] = []

        class Visitor(ast.NodeVisitor):
            def __init__(self):
                self.scope: list[str] = []

            def _visit_function(
                self, node: ast.FunctionDef | ast.AsyncFunctionDef
            ) -> None:
                owner = ".".join(self.scope) or None
                qualified_name = ".".join((*self.scope, node.name))
                end_line = int(getattr(node, "end_lineno", node.lineno))
                snippet = "".join(lines[node.lineno - 1 : end_line])
                spans.append(
                    FunctionSpan(
                        file_path=file_path,
                        name=node.name,
                        qualified_name=qualified_name,
                        owner_qualified_name=owner,
                        start_line=int(node.lineno),
                        end_line=end_line,
                        source=snippet,
                        node_id=function_id(
                            self_outer.repo_id,
                            file_path,
                            node.name,
                            qualified_name=qualified_name,
                        ),
                    )
                )
                self.scope.append(node.name)
                self.generic_visit(node)
                self.scope.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self._visit_function(node)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                self._visit_function(node)

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                self.scope.append(node.name)
                self.generic_visit(node)
                self.scope.pop()

        self_outer = self
        Visitor().visit(tree)
        return sorted(spans, key=lambda row: (row.start_line, row.end_line, row.name))

    def functions(self, file_path: str) -> list[FunctionSpan]:
        normalized = normalize_repo_path(file_path, self.repo_root)
        if normalized is None or not normalized.endswith(".py"):
            return []
        if normalized not in self._cache:
            try:
                if self.source_loader is not None:
                    source = self.source_loader(normalized)
                else:
                    source = (self.repo_root / Path(normalized)).read_text(
                        encoding="utf-8"
                    )
            except (OSError, UnicodeError):
                self._cache[normalized] = []
            else:
                self._cache[normalized] = self.parse_source(normalized, source)
        return self._cache[normalized]

    def resolve(
        self,
        file_path: str,
        *,
        line_start: int | None = None,
        line_end: int | None = None,
        symbol: str | None = None,
        snippets: Iterable[str] = (),
    ) -> tuple[FunctionSpan | None, str]:
        functions = self.functions(file_path)
        candidates = functions
        ambiguous_symbol = False
        if symbol:
            named = [row for row in functions if row.name == symbol]
            if len(named) == 1:
                return named[0], "exact_symbol"
            if len(named) > 1:
                candidates = named
                ambiguous_symbol = True

        if line_start is not None:
            end = line_end if line_end is not None else line_start
            overlaps = [
                row
                for row in candidates
                if row.start_line <= end and row.end_line >= line_start
            ]
            if len(overlaps) == 1:
                return overlaps[0], (
                    "exact_symbol_line_range"
                    if ambiguous_symbol
                    else "exact_line_range"
                )
            if len(overlaps) > 1:
                containing = [
                    row
                    for row in overlaps
                    if row.start_line <= line_start and row.end_line >= end
                ]
                if len(containing) == 1:
                    return containing[0], "exact_line_range"
                return None, "ambiguous_line_range"

        for snippet in snippets:
            snippet = snippet.strip()
            if not snippet:
                continue
            matches = [row for row in candidates if snippet in row.source]
            if len(matches) == 1:
                return matches[0], "exact_snippet"
            if len(matches) > 1:
                return None, "ambiguous_snippet"
        if ambiguous_symbol:
            return None, "ambiguous_symbol"
        return None, "file_only"


@dataclass(frozen=True)
class GitRangeAnalysis:
    base_revision: str
    head_revision: str
    changed_files: tuple[str, ...]
    changed_functions: tuple[FunctionSpan, ...]
    removed_functions: tuple[str, ...]
    ambiguous_functions: tuple[str, ...]


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


class GitRangeAnalyzer:
    def __init__(
        self,
        repo_root: str | Path,
        repo_id: str,
        *,
        path_prefix: str = "",
    ):
        self.repo_root = Path(repo_root).resolve()
        self.repo_id = repo_id
        self.path_prefix = path_prefix.strip("/")
        try:
            self.repo = git.Repo(self.repo_root)
        except Exception as exc:
            raise PermanentWorkError(f"invalid Git repository: {self.repo_root}: {exc}") from exc

    @staticmethod
    def _blob_text(commit: git.objects.Commit, file_path: str) -> str:
        try:
            blob = commit.tree / file_path
        except KeyError:
            return ""
        return blob.data_stream.read().decode("utf-8", errors="replace")

    @staticmethod
    def _unique_by_name(spans: Iterable[FunctionSpan]) -> tuple[dict[str, FunctionSpan], set[str]]:
        grouped: dict[str, list[FunctionSpan]] = {}
        for span in spans:
            grouped.setdefault(span.qualified_name, []).append(span)
        unique = {name: rows[0] for name, rows in grouped.items() if len(rows) == 1}
        ambiguous = {name for name, rows in grouped.items() if len(rows) > 1}
        return unique, ambiguous

    def analyze(self, base_revision: str, head_revision: str) -> GitRangeAnalysis:
        try:
            base = self.repo.commit(base_revision)
            head = self.repo.commit(head_revision)
            self.repo.git.merge_base("--is-ancestor", base.hexsha, head.hexsha)
        except Exception as exc:
            raise PermanentWorkError(
                f"invalid accepted revision range {base_revision}..{head_revision}: {exc}"
            ) from exc

        try:
            name_status = self.repo.git.diff(
                "--name-status", "--find-renames", base.hexsha, head.hexsha
            )
        except Exception as exc:
            raise PermanentWorkError(f"cannot read accepted Git range: {exc}") from exc

        changed_files: list[str] = []
        path_pairs: list[tuple[str | None, str | None]] = []
        submodule_pairs: list[tuple[str, str, str]] = []
        for line in name_status.splitlines():
            fields = line.split("\t")
            if len(fields) < 2:
                continue
            status = fields[0]
            if status.startswith("R") and len(fields) >= 3:
                old_path, new_path = fields[1], fields[2]
            elif status.startswith("D"):
                old_path, new_path = fields[1], None
            elif status.startswith("A"):
                old_path, new_path = None, fields[1]
            else:
                old_path = new_path = fields[1]
            display_path = new_path or old_path
            if display_path:
                changed_files.append(self._join_path(display_path))
            if (old_path and old_path.endswith(".py")) or (new_path and new_path.endswith(".py")):
                path_pairs.append((old_path, new_path))
            elif display_path:
                try:
                    old_entry = (base.tree / display_path) if old_path else None
                    new_entry = (head.tree / display_path) if new_path else None
                    if (old_entry and old_entry.type == "submodule") or (
                        new_entry and new_entry.type == "submodule"
                    ):
                        old_sha = old_entry.hexsha if old_entry else ""
                        new_sha = new_entry.hexsha if new_entry else ""
                        submodule_pairs.append((display_path, old_sha, new_sha))
                except (KeyError, AttributeError):
                    pass

        parser = FunctionCatalog(self.repo_root, self.repo_id)
        changed: list[FunctionSpan] = []
        removed: list[str] = []
        ambiguous: list[str] = []
        for old_path, new_path in path_pairs:
            old_source = self._blob_text(base, old_path) if old_path else ""
            new_source = self._blob_text(head, new_path) if new_path else ""
            old_logical_path = self._join_path(old_path or new_path or "")
            new_logical_path = self._join_path(new_path or old_path or "")
            old_spans = parser.parse_source(old_logical_path, old_source)
            new_spans = parser.parse_source(new_logical_path, new_source)
            old_unique, old_ambiguous = self._unique_by_name(old_spans)
            new_unique, new_ambiguous = self._unique_by_name(new_spans)
            for name in sorted(old_ambiguous | new_ambiguous):
                ambiguous.append(f"{new_logical_path}::{name}")
            for name, new_span in sorted(new_unique.items()):
                if name in new_ambiguous or name in old_ambiguous:
                    continue
                old_span = old_unique.get(name)
                if (
                    old_span is None
                    or old_span.node_id != new_span.node_id
                    or old_span.source != new_span.source
                ):
                    changed.append(new_span)
            for name, old_span in sorted(old_unique.items()):
                new_span = new_unique.get(name)
                if (
                    name not in old_ambiguous
                    and name not in new_ambiguous
                    and (new_span is None or new_span.node_id != old_span.node_id)
                ):
                    removed.append(old_span.node_id)

        for submodule_path, old_sha, new_sha in submodule_pairs:
            nested_path = self.repo_root / submodule_path
            if not old_sha or not new_sha or not (nested_path / ".git").exists():
                continue
            nested = GitRangeAnalyzer(
                nested_path,
                self.repo_id,
                path_prefix=self._join_path(submodule_path),
            ).analyze(old_sha, new_sha)
            changed_files.extend(nested.changed_files)
            changed.extend(nested.changed_functions)
            removed.extend(nested.removed_functions)
            ambiguous.extend(nested.ambiguous_functions)

        deduped = {row.node_id: row for row in changed}
        return GitRangeAnalysis(
            base_revision=base.hexsha,
            head_revision=head.hexsha,
            changed_files=tuple(sorted(set(changed_files))),
            changed_functions=tuple(deduped[key] for key in sorted(deduped)),
            removed_functions=tuple(sorted(set(removed))),
            ambiguous_functions=tuple(sorted(set(ambiguous))),
        )

    def _join_path(self, path: str) -> str:
        prefix = self.path_prefix.strip("/")
        return f"{prefix}/{path}" if prefix else path


class TranscriptParser:
    """Shared function resolver over provider-normalized activities."""

    def parse_file(self, path: str | Path) -> NormalizedTranscript:
        try:
            return parse_transcript_file(path)
        except ValueError as exc:
            raise PermanentWorkError(str(exc)) from exc

    def events(
        self, transcript: NormalizedTranscript, catalog: FunctionCatalog
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for activity in transcript.activities:
            if activity.outcome != "success":
                events.append(self._audit_activity(activity, catalog))
                continue
            if activity.kind == "read":
                events.append(self._read_event(activity, catalog))
            elif activity.kind == "edit":
                events.append(self._edit_event(activity, catalog))
            elif activity.kind == "write":
                events.append(self._write_event(activity, catalog))
            elif activity.kind in {"shell", "test", "commit"}:
                events.extend(self._shell_events(activity, catalog))
            elif activity.kind == "patch":
                events.append(self._patch_event(activity, catalog))
            else:
                events.append(self._audit_activity(activity, catalog))
        return events

    @staticmethod
    def _detail(activity: NormalizedActivity) -> dict[str, Any]:
        detail = {
            "provider": activity.provider,
            "call_id": activity.call_id,
            "status": activity.status,
            "outcome": activity.outcome,
        }
        detail.update(activity.provider_detail)
        return {key: value for key, value in detail.items() if value is not None}

    def _audit_activity(
        self, activity: NormalizedActivity, catalog: FunctionCatalog
    ) -> dict[str, Any]:
        detail = self._detail(activity)
        if activity.command:
            detail["command"] = activity.command[:1000]
        return self._event(
            activity.kind if activity.kind != "unknown" else "unknown",
            activity.tool,
            normalize_repo_path(activity.path or "", catalog.repo_root),
            resolution="audit_only",
            detail=detail,
        )

    @staticmethod
    def _event(
        event_kind: str,
        tool: str,
        file_path: str | None,
        *,
        line_start: int | None = None,
        line_end: int | None = None,
        function: FunctionSpan | None = None,
        resolution: str,
        detail: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "event_kind": event_kind,
            "tool": tool,
            "file_path": file_path,
            "line_start": line_start,
            "line_end": line_end,
            "function_id": function.node_id if function else None,
            "resolution": resolution,
            "detail": dict(detail or {}),
        }

    def _read_event(
        self, activity: NormalizedActivity, catalog: FunctionCatalog
    ) -> dict[str, Any]:
        path = normalize_repo_path(activity.path or "", catalog.repo_root)
        line_start = activity.line_start
        line_end = activity.line_end
        symbols = {
            match.group(1)
            for match in re.finditer(
                r"^\s*(?:\d+:\s*)?(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(",
                activity.result_text or "",
                flags=re.MULTILINE,
            )
        }
        if len(symbols) == 1:
            function, resolution = catalog.resolve(
                path or "",
                symbol=next(iter(symbols)),
                line_start=line_start,
                line_end=line_end,
            )
            return self._event(
                "read",
                activity.tool,
                path,
                line_start=line_start,
                line_end=line_end,
                function=function,
                resolution=resolution,
                detail=self._detail(activity),
            )
        function, resolution = catalog.resolve(
            path or "", line_start=line_start, line_end=line_end
        )
        return self._event(
            "read",
            activity.tool,
            path,
            line_start=line_start,
            line_end=line_end,
            function=function,
            resolution=resolution,
            detail=self._detail(activity),
        )

    def _edit_event(
        self, activity: NormalizedActivity, catalog: FunctionCatalog
    ) -> dict[str, Any]:
        path = normalize_repo_path(activity.path or "", catalog.repo_root)
        snippets = [activity.new_text or "", activity.old_text or ""]
        symbols: set[str] = set()
        for snippet in snippets:
            try:
                tree = ast.parse(textwrap.dedent(snippet))
            except SyntaxError:
                continue
            symbols.update(
                node.name
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
        function = None
        resolution = "file_only"
        if len(symbols) == 1:
            function, resolution = catalog.resolve(
                path or "", symbol=next(iter(symbols))
            )
        if function is None and resolution != "ambiguous_symbol":
            function, resolution = catalog.resolve(path or "", snippets=snippets)
        return self._event(
            "edit",
            activity.tool,
            path,
            function=function,
            resolution=resolution,
            detail=self._detail(activity),
        )

    def _write_event(
        self, activity: NormalizedActivity, catalog: FunctionCatalog
    ) -> dict[str, Any]:
        path = normalize_repo_path(activity.path or "", catalog.repo_root)
        function, resolution = catalog.resolve(
            path or "", snippets=(activity.written_content or "",)
        )
        return self._event(
            "write",
            activity.tool,
            path,
            function=function,
            resolution=resolution,
            detail=self._detail(activity),
        )

    def _patch_event(
        self, activity: NormalizedActivity, catalog: FunctionCatalog
    ) -> dict[str, Any]:
        if activity.old_text or activity.new_text:
            resolved = self._edit_event(activity, catalog)
            resolved["event_kind"] = "changed_patch"
            return resolved
        return self._event(
            "changed_patch",
            activity.tool,
            normalize_repo_path(activity.path or "", catalog.repo_root),
            resolution="audit_only",
            detail=self._detail(activity),
        )

    def _shell_events(
        self, activity: NormalizedActivity, catalog: FunctionCatalog
    ) -> list[dict[str, Any]]:
        command = activity.command or ""
        is_test = activity.kind == "test"
        event_kind = "commit" if activity.kind == "commit" else (
            "test" if is_test else "operation"
        )
        results: list[dict[str, Any]] = []
        seen: set[tuple[str | None, str | None]] = set()
        for match in _PYTHON_REF_RE.finditer(command):
            path = normalize_repo_path(match.group("path"), catalog.repo_root)
            symbol = match.group("symbol")
            key = (path, symbol)
            if key in seen:
                continue
            seen.add(key)
            function, resolution = catalog.resolve(path or "", symbol=symbol)
            results.append(
                self._event(
                    event_kind,
                    activity.tool,
                    path,
                    function=function,
                    resolution=resolution if symbol else "file_only",
                    detail={**self._detail(activity), "command": command[:1000]},
                )
            )
        if not results:
            try:
                shlex.split(command, posix=False)
            except ValueError:
                pass
            results.append(
                self._event(
                    event_kind,
                    activity.tool,
                    None,
                    resolution="audit_only",
                    detail={**self._detail(activity), "command": command[:1000]},
                )
            )
        return results


def build_affinity_evidence(
    changed_function_ids: Iterable[str],
    events: Iterable[Mapping[str, Any]],
    *,
    change_kind: str,
    domains: Iterable[str],
    known_domains: set[str],
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    targets = sorted(set(changed_function_ids))
    if len(targets) > MAX_CHANGED_FUNCTIONS:
        return [], tuple(sorted(set(domains) - known_domains))

    read_counts: Counter[str] = Counter()
    operation_sources: set[str] = set()
    for event in events:
        source = event.get("function_id")
        if not source:
            continue
        if event.get("event_kind") == "read":
            read_counts[str(source)] += 1
        elif event.get("event_kind") in {"test", "operation"}:
            operation_sources.add(str(source))

    source_roles: dict[str, str] = {}
    for source, count in read_counts.items():
        source_roles[source] = "repeated_read" if count >= 2 else "single_read"
    for source in operation_sources:
        source_roles[source] = "targeted_operation"

    non_changed = [
        (ROLE_STRENGTH[role], read_counts.get(source, 0), source, role)
        for source, role in source_roles.items()
        if source not in targets
    ]
    non_changed.sort(key=lambda row: (-row[0], -row[1], row[2]))
    source_roles = {
        source: role for _, _, source, role in non_changed[:MAX_NON_CHANGED_SOURCES]
    }

    pair_roles: dict[tuple[str, str], str] = {}
    for source in targets:
        for target in targets:
            if source != target:
                pair_roles[(source, target)] = "changed"
    for source, role in source_roles.items():
        for target in targets:
            if source != target:
                pair_roles[(source, target)] = role

    requested_domains = tuple(dict.fromkeys(str(domain) for domain in domains))
    valid_domains = [domain for domain in requested_domains if domain in known_domains]
    unknown_domains = tuple(sorted(set(requested_domains) - known_domains))
    dimensions = [("global", "global"), ("change_kind", change_kind)]
    dimensions.extend(("domain", domain) for domain in valid_domains)

    evidence: list[dict[str, Any]] = []
    for (source, target), role in sorted(pair_roles.items()):
        for dimension_type, dimension_value in dimensions:
            evidence.append(
                {
                    "source_function_id": source,
                    "target_function_id": target,
                    "dimension_type": dimension_type,
                    "dimension_value": dimension_value,
                    "role": role,
                    "evidence": ROLE_STRENGTH[role],
                }
            )
    return evidence, unknown_domains


def select_affinity_targets(
    git_changed_function_ids: Iterable[str],
    events: Iterable[Mapping[str, Any]],
) -> tuple[list[str], str]:
    """Prefer exact transcript edits without losing the accepted Git scope.

    A merged range can contain unrelated work, especially when an outer
    repository advances a submodule across multiple commits.  Exact edit-like
    transcript events identify the task-local target.  Broad file evidence is
    deliberately excluded; when no exact intersection exists, the accepted
    Git-changed functions remain the conservative fallback.
    """

    git_changed = sorted(set(str(value) for value in git_changed_function_ids))
    changed_set = set(git_changed)
    exact_edits = sorted(
        {
            str(event["function_id"])
            for event in events
            if event.get("event_kind") in {"edit", "write", "changed_patch"}
            and str(event.get("resolution") or "").startswith("exact_")
            and event.get("function_id") in changed_set
        }
    )
    if exact_edits:
        return exact_edits, "transcript_exact_change_intersection"
    return git_changed, "git_changed_fallback"


class AcceptedWorkProcessor:
    """Git + transcript deterministic analysis; Helix materialization plugs in later."""

    def __init__(
        self,
        ledger: WorkLedger,
        repo_root: str | Path,
        repo_id: str,
        *,
        domain_registry: Iterable[str] = (),
        transcript_root: str | Path | None = None,
        graph_refresher: Any | None = None,
    ):
        self.ledger = ledger
        self.repo_root = Path(repo_root).resolve()
        self.repo_id = repo_id
        self.domain_registry = {str(value) for value in domain_registry}
        self.transcript_root = Path(transcript_root).resolve() if transcript_root else None
        self.graph_refresher = graph_refresher
        self.git = GitRangeAnalyzer(self.repo_root, repo_id)
        self.snapshot = GitSnapshotReader(self.repo_root)
        self.transcripts = TranscriptParser()

    def _transcript_path(self, reference: str | None) -> Path:
        if not reference:
            raise PermanentWorkError("transcript_ref is required for affinity learning")
        if reference.startswith("opencode://session/"):
            if self.transcript_root is None:
                raise PermanentWorkError("opencode transcript_root is not configured")
            session_id = reference.rsplit("/", 1)[-1]
            return self.transcript_root / f"{session_id}.json"
        if reference.startswith("file://"):
            return Path(reference[7:])
        return Path(reference)

    def process(self, job: Job, owner: str) -> dict[str, Any]:
        if job.work.repo_id != self.repo_id:
            raise PermanentWorkError(
                f"job repository {job.work.repo_id!r} does not match worker repository {self.repo_id!r}"
            )
        git_range = self.git.analyze(job.work.base_revision, job.work.head_revision)
        graph_refresh_stats = None
        if self.graph_refresher is not None:
            graph_refresh_stats = self.graph_refresher.refresh(git_range).to_stats()
        existing = self.ledger.analysis_details(job.job_id)
        if existing is not None:
            return self._result(job, existing, replayed=True)

        transcript = self.transcripts.parse_file(self._transcript_path(job.work.transcript_ref))
        if transcript.repository_path:
            transcript_repo = Path(transcript.repository_path).resolve(strict=False)
            if transcript_repo != self.repo_root:
                raise PermanentWorkError(
                    f"transcript repository {transcript_repo} does not match {self.repo_root}"
                )

        catalog = FunctionCatalog(
            self.repo_root,
            self.repo_id,
            source_loader=lambda path: self.snapshot.read_text(
                git_range.head_revision, path
            ),
        )
        events = self.transcripts.events(transcript, catalog)
        changed_ids = [row.node_id for row in git_range.changed_functions]
        affinity_targets, target_policy = select_affinity_targets(changed_ids, events)
        skip_reason = None
        if len(affinity_targets) > MAX_CHANGED_FUNCTIONS:
            skip_reason = (
                f"affinity_target_limit:{len(affinity_targets)}>{MAX_CHANGED_FUNCTIONS}"
            )
            evidence: list[dict[str, Any]] = []
            unknown_domains = tuple(sorted(set(job.work.domains) - self.domain_registry))
        else:
            evidence, unknown_domains = build_affinity_evidence(
                affinity_targets,
                events,
                change_kind=job.work.change_kind,
                domains=job.work.domains,
                known_domains=self.domain_registry,
            )
        stats = {
            "session_id": transcript.session_id,
            "transcript_provider": transcript.provider,
            "changed_file_count": len(git_range.changed_files),
            "changed_function_count": len(changed_ids),
            "git_changed_functions": changed_ids,
            "affinity_target_count": len(affinity_targets),
            "affinity_target_functions": affinity_targets,
            "affinity_target_policy": target_policy,
            "removed_function_count": len(git_range.removed_functions),
            "ambiguous_function_count": len(git_range.ambiguous_functions),
            "event_count": len(events),
            "resolved_event_count": sum(1 for row in events if row.get("function_id")),
            "audit_only_event_count": sum(1 for row in events if not row.get("function_id")),
            "evidence_row_count": len(evidence),
            "removed_functions": list(git_range.removed_functions),
            "ambiguous_functions": list(git_range.ambiguous_functions),
            "graph_refresh": graph_refresh_stats,
        }
        self.ledger.record_analysis(
            job,
            owner,
            transcript_hash=transcript.content_hash,
            changed_files=git_range.changed_files,
            changed_functions=changed_ids,
            stats=stats,
            events=events,
            evidence=evidence,
            unknown_domains=unknown_domains,
            affinity_skipped_reason=skip_reason,
        )
        details = self.ledger.analysis_details(job.job_id)
        assert details is not None
        return self._result(job, details, replayed=False)

    @staticmethod
    def _result(job: Job, details: Mapping[str, Any], *, replayed: bool) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "replayed": replayed,
            "changed_functions": len(details["changed_functions"]),
            "affinity_targets": len(
                details.get("stats", {}).get(
                    "affinity_target_functions", details["changed_functions"]
                )
            ),
            "events": len(details["events"]),
            "evidence_rows": len(details["evidence"]),
            "affinity_skipped_reason": details["affinity_skipped_reason"],
            "unknown_domains": details["unknown_domains"],
        }
