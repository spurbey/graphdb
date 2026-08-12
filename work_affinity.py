"""Deterministic post-accepted-work signal extraction and affinity learning."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import shlex
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

import git

from accepted_work import ANALYZER_VERSION, Job, PermanentWorkError, WorkLedger


MAX_CHANGED_FUNCTIONS = 20
MAX_NON_CHANGED_SOURCES = 25
ROLE_STRENGTH = {
    "changed": 1.0,
    "targeted_operation": 0.6,
    "repeated_read": 0.25,
    "single_read": 0.1,
}
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_JSON_START_RE = re.compile(r"(?m)^\s*([\{\[])")
_OUTPUT_LINE_RE = re.compile(r"(?m)^\s*(\d+):")
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


def function_id(repo_id: str, file_path: str, name: str) -> str:
    safe = file_path.replace("/", "_").replace("\\", "_").replace(".py", "")
    return f"{repo_id}:func_{safe}_{name}"


@dataclass(frozen=True)
class FunctionSpan:
    file_path: str
    name: str
    start_line: int
    end_line: int
    source: str
    node_id: str


class FunctionCatalog:
    """Lazy current-source function lookup for only the files an episode touches."""

    def __init__(self, repo_root: str | Path, repo_id: str):
        self.repo_root = Path(repo_root).resolve()
        self.repo_id = repo_id
        self._cache: dict[str, list[FunctionSpan]] = {}

    def parse_source(self, file_path: str, source: str) -> list[FunctionSpan]:
        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError:
            return []
        lines = source.splitlines(keepends=True)
        spans: list[FunctionSpan] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            end_line = int(getattr(node, "end_lineno", node.lineno))
            snippet = "".join(lines[node.lineno - 1 : end_line])
            spans.append(
                FunctionSpan(
                    file_path=file_path,
                    name=node.name,
                    start_line=int(node.lineno),
                    end_line=end_line,
                    source=snippet,
                    node_id=function_id(self.repo_id, file_path, node.name),
                )
            )
        return sorted(spans, key=lambda row: (row.start_line, row.end_line, row.name))

    def functions(self, file_path: str) -> list[FunctionSpan]:
        normalized = normalize_repo_path(file_path, self.repo_root)
        if normalized is None or not normalized.endswith(".py"):
            return []
        if normalized not in self._cache:
            path = self.repo_root / Path(normalized)
            try:
                source = path.read_text(encoding="utf-8")
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
        if symbol:
            named = [row for row in functions if row.name == symbol]
            if len(named) == 1:
                return named[0], "exact_symbol"
            if len(named) > 1:
                return None, "ambiguous_symbol"

        if line_start is not None:
            end = line_end if line_end is not None else line_start
            overlaps = [
                row
                for row in functions
                if row.start_line <= end and row.end_line >= line_start
            ]
            if len(overlaps) == 1:
                return overlaps[0], "exact_line_range"
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
            matches = [row for row in functions if snippet in row.source]
            if len(matches) == 1:
                return matches[0], "exact_snippet"
            if len(matches) > 1:
                return None, "ambiguous_snippet"
        return None, "file_only"


@dataclass(frozen=True)
class GitRangeAnalysis:
    base_revision: str
    head_revision: str
    changed_files: tuple[str, ...]
    changed_functions: tuple[FunctionSpan, ...]
    removed_functions: tuple[str, ...]
    ambiguous_functions: tuple[str, ...]


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
            grouped.setdefault(span.name, []).append(span)
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


@dataclass(frozen=True)
class Transcript:
    session_id: str
    repository_path: str | None
    content_hash: str
    payload: dict[str, Any]


class TranscriptParser:
    def parse_file(self, path: str | Path) -> Transcript:
        source_path = Path(path)
        try:
            raw = source_path.read_bytes()
        except OSError as exc:
            raise PermanentWorkError(f"transcript unavailable: {source_path}: {exc}") from exc
        text = raw.decode("utf-8-sig", errors="replace")
        clean = _ANSI_RE.sub("", text)
        match = _JSON_START_RE.search(clean)
        if match is None:
            raise PermanentWorkError(f"transcript has no JSON payload: {source_path}")
        try:
            payload = json.loads(clean[match.start() :])
        except json.JSONDecodeError as exc:
            raise PermanentWorkError(f"malformed transcript JSON: {source_path}: {exc}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("info"), dict):
            raise PermanentWorkError("transcript must contain an info object")
        if not isinstance(payload.get("messages"), list) or not payload["messages"]:
            raise PermanentWorkError("transcript must contain non-empty messages")
        session_id = payload["info"].get("id")
        if not isinstance(session_id, str) or not session_id:
            raise PermanentWorkError("transcript info.id is missing")
        repository_path = payload["info"].get("directory")
        return Transcript(
            session_id=session_id,
            repository_path=repository_path if isinstance(repository_path, str) else None,
            content_hash=hashlib.sha256(raw).hexdigest(),
            payload=payload,
        )

    def events(self, transcript: Transcript, catalog: FunctionCatalog) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for message in transcript.payload.get("messages", []):
            if not isinstance(message, dict):
                continue
            for part in message.get("parts", []):
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "patch":
                    for path in part.get("files", []):
                        normalized = normalize_repo_path(path, catalog.repo_root)
                        events.append(
                            self._event("changed_patch", "patch", normalized, resolution="audit_only")
                        )
                    continue
                if part.get("type") != "tool":
                    continue
                tool = str(part.get("tool") or "")
                state = part.get("state") or {}
                if state.get("status") != "completed":
                    continue
                inputs = state.get("input") or {}
                if not isinstance(inputs, dict):
                    inputs = {}
                output = state.get("output")
                if tool == "read":
                    events.append(self._read_event(tool, inputs, output, catalog))
                elif tool == "edit":
                    events.append(self._edit_event(tool, inputs, catalog))
                elif tool == "write":
                    events.append(self._write_event(tool, inputs, catalog))
                elif tool == "bash":
                    events.extend(self._bash_events(tool, inputs, catalog))
                elif tool in {"grep", "glob", "graphdb_search_code_semantics"}:
                    events.append(
                        self._event(
                            "search",
                            tool,
                            normalize_repo_path(inputs.get("path", ""), catalog.repo_root),
                            resolution="audit_only",
                        )
                    )
        return events

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
        self, tool: str, inputs: Mapping[str, Any], output: Any, catalog: FunctionCatalog
    ) -> dict[str, Any]:
        path = normalize_repo_path(inputs.get("filePath", ""), catalog.repo_root)
        numbered = [int(value) for value in _OUTPUT_LINE_RE.findall(str(output or ""))]
        line_start = min(numbered) if numbered else None
        line_end = max(numbered) if numbered else None
        function, resolution = catalog.resolve(
            path or "", line_start=line_start, line_end=line_end
        )
        return self._event(
            "read",
            tool,
            path,
            line_start=line_start,
            line_end=line_end,
            function=function,
            resolution=resolution,
        )

    def _edit_event(
        self, tool: str, inputs: Mapping[str, Any], catalog: FunctionCatalog
    ) -> dict[str, Any]:
        path = normalize_repo_path(inputs.get("filePath", ""), catalog.repo_root)
        snippets = [str(inputs.get("newString") or ""), str(inputs.get("oldString") or "")]
        function, resolution = catalog.resolve(path or "", snippets=snippets)
        return self._event(
            "edit", tool, path, function=function, resolution=resolution
        )

    def _write_event(
        self, tool: str, inputs: Mapping[str, Any], catalog: FunctionCatalog
    ) -> dict[str, Any]:
        path = normalize_repo_path(inputs.get("filePath", ""), catalog.repo_root)
        return self._event("write", tool, path, resolution="file_only")

    def _bash_events(
        self, tool: str, inputs: Mapping[str, Any], catalog: FunctionCatalog
    ) -> list[dict[str, Any]]:
        command = str(inputs.get("command") or "")
        lower = command.lower()
        is_test = any(marker in lower for marker in ("pytest", "unittest", " test"))
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
                    "test" if is_test else "operation",
                    tool,
                    path,
                    function=function,
                    resolution=resolution if symbol else "file_only",
                    detail={"command": command[:1000]},
                )
            )
        if not results:
            try:
                shlex.split(command, posix=False)
            except ValueError:
                pass
            results.append(
                self._event(
                    "test" if is_test else "operation",
                    tool,
                    None,
                    resolution="audit_only",
                    detail={"command": command[:1000]},
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

        catalog = FunctionCatalog(self.repo_root, self.repo_id)
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
