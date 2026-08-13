"""Provider-specific transcript readers for accepted-work analysis.

The on-disk formats used by coding agents are intentionally not treated as one
universal schema.  Each adapter understands its provider's request/result
envelope and emits the small activity contract consumed by the shared function
resolver in :mod:`work_affinity`.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
JSON_START_RE = re.compile(r"(?m)^\s*([\{\[])")
PATCH_PATH_RE = re.compile(
    r"(?:\*\*\*\s+(?:Update|Add|Delete)\s+File:\s*|^\+\+\+\s+b/|^---\s+a/)([^\r\n]+)",
    re.MULTILINE,
)
NUMBERED_LINE_RE = re.compile(r"(?m)^\s*(\d+):")


@dataclass(frozen=True)
class NormalizedActivity:
    """One provider-neutral operation extracted from a transcript."""

    provider: str
    session_id: str
    kind: str
    tool: str
    call_id: str | None = None
    status: str | None = None
    outcome: str = "unknown"
    path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    command: str | None = None
    old_text: str | None = None
    new_text: str | None = None
    written_content: str | None = None
    result_text: str | None = None
    provider_detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedTranscript:
    provider: str
    session_id: str
    repository_path: str | None
    content_hash: str
    activities: tuple[NormalizedActivity, ...]
    payload: Any = None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "\n".join(part for item in value if (part := _text(item)))
    if isinstance(value, Mapping):
        for key in ("text", "data", "content", "output"):
            if key in value and isinstance(value[key], (str, list, tuple, Mapping)):
                return _text(value[key])
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def classify_outcome(
    status: Any,
    result: Any = None,
    *,
    is_error: bool = False,
    command_output: bool = False,
) -> str:
    """Classify tool completion without assuming provider status is success."""

    normalized_status = str(status or "").lower().replace("-", "_")
    result_text = _text(result)
    if is_error or normalized_status in {"failed", "failure", "error"}:
        return "failed"
    exit_match = re.search(
        r"(?im)^\s*(?:exit\s+code|exit\s+status)\s*:\s*(-?\d+)\s*$",
        result_text,
    )
    if exit_match:
        return "success" if int(exit_match.group(1)) == 0 else "failed"
    if normalized_status in {"timed_out", "timeout", "timedout"}:
        return "timed_out"
    if command_output and re.search(
        r"(?im)(?:^\s*(?:command\s+)?timed\s+out\b|terminated command after exceeding timeout)",
        result_text,
    ):
        return "timed_out"
    if command_output and re.search(
        r"(?im)(?:\bparsererror\b|^\s*traceback \(most recent call last\))",
        result_text,
    ):
        return "failed"
    if normalized_status in {"completed", "success", "succeeded", "ok"}:
        return "success"
    return "unknown"


def _line_range(result: Any) -> tuple[int | None, int | None]:
    numbers = [int(value) for value in NUMBERED_LINE_RE.findall(_text(result))]
    return (min(numbers), max(numbers)) if numbers else (None, None)


def _path_from_input(data: Mapping[str, Any]) -> str | None:
    for key in ("filePath", "file_path", "path", "file", "filename"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _command_path(command: str) -> str | None:
    """Extract only explicit single-file shell reads; otherwise leave audit-only."""

    match = re.search(
        r"(?i)(?:^|[;&|]\s*)(?:get-content|cat|type)\s+(?:-raw\s+)?(?:-literalpath\s+)?(?:\"([^\"]+)\"|'([^']+)'|(\S+))",
        command,
    )
    if not match:
        return None
    return next((value for value in match.groups() if value), None)


def _kind_for_tool(tool: str, data: Mapping[str, Any]) -> str:
    name = tool.lower()
    if name in {"read", "fs_read"}:
        return "read"
    if name in {"edit", "strreplace", "str_replace", "replace"}:
        return "edit"
    if name in {"write", "fs_write", "create_file"}:
        command = str(data.get("command") or "").lower()
        if command in {"strreplace", "str_replace"}:
            return "edit"
        return "write"
    if name in {"grep", "glob", "search", "rg", "find", "code"}:
        return "search"
    if name in {"bash", "shell", "shell_command", "exec_command", "execute", "run"}:
        command = _text(data.get("command") or data.get("cmd"))
        lower = command.lower()
        if re.search(r"(?:^|[;&|]\s*)(?:get-content|cat|type)\b", lower):
            return "read"
        if re.search(r"(?:^|[;&|]\s*)(?:rg|grep|select-string|findstr)\b", lower):
            return "search"
        if re.search(r"\bgit\s+commit\b", lower):
            return "commit"
        if re.search(r"\b(?:pytest|unittest|cargo\s+test|npm\s+test|vitest)\b", lower):
            return "test"
        return "shell"
    if name in {"apply_patch", "patch"}:
        return "patch"
    return "unknown"


def _activity(
    *,
    provider: str,
    session_id: str,
    tool: str,
    data: Mapping[str, Any],
    status: Any,
    result: Any = None,
    call_id: str | None = None,
    is_error: bool = False,
    provider_detail: Mapping[str, Any] | None = None,
    kind: str | None = None,
) -> NormalizedActivity:
    selected_kind = kind or _kind_for_tool(tool, data)
    command = data.get("command") or data.get("cmd")
    old_text = data.get("oldString") or data.get("old_string") or data.get("oldStr")
    new_text = data.get("newString") or data.get("new_string") or data.get("newStr")
    written = data.get("content") if selected_kind == "write" else None
    line_start, line_end = _line_range(result)
    if line_start is None and data.get("start_line") is not None:
        try:
            line_start = int(data["start_line"])
        except (TypeError, ValueError):
            pass
    if line_end is None and data.get("end_line") is not None:
        try:
            line_end = int(data["end_line"])
        except (TypeError, ValueError):
            pass
    if line_start is None and data.get("offset") is not None:
        try:
            line_start = int(data["offset"]) + 1
        except (TypeError, ValueError):
            pass
    if line_start is not None and line_end is None and data.get("limit") is not None:
        try:
            line_end = line_start + int(data["limit"]) - 1
        except (TypeError, ValueError):
            pass
    path = _path_from_input(data)
    if path is None and selected_kind == "read" and command is not None:
        path = _command_path(_text(command))
    return NormalizedActivity(
        provider=provider,
        session_id=session_id,
        kind=selected_kind,
        tool=tool,
        call_id=call_id,
        status=str(status) if status is not None else None,
        outcome=classify_outcome(
            status,
            result,
            is_error=is_error,
            command_output=selected_kind in {"shell", "test", "commit"},
        ),
        path=path,
        line_start=line_start,
        line_end=line_end,
        command=_text(command) if command is not None else None,
        old_text=_text(old_text) if old_text is not None else None,
        new_text=_text(new_text) if new_text is not None else None,
        written_content=_text(written) if written is not None else None,
        result_text=_text(result) if result is not None else None,
        provider_detail=dict(provider_detail or {}),
    )


class TranscriptAdapter:
    provider: str

    def parse(self, path: Path, raw: bytes) -> NormalizedTranscript:
        raise NotImplementedError


class OpenCodeAdapter(TranscriptAdapter):
    provider = "opencode"

    def parse(self, path: Path, raw: bytes) -> NormalizedTranscript:
        text = ANSI_RE.sub("", raw.decode("utf-8-sig", errors="replace"))
        match = JSON_START_RE.search(text)
        if match is None:
            raise ValueError("transcript has no JSON payload")
        try:
            payload = json.loads(text[match.start() :])
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed transcript JSON: {exc}") from exc
        if not isinstance(payload, Mapping) or not isinstance(payload.get("info"), Mapping):
            raise ValueError("OpenCode transcript must contain an info object")
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError("OpenCode transcript must contain non-empty messages")
        info = payload["info"]
        session_id = str(info.get("id") or "")
        if not session_id:
            raise ValueError("OpenCode transcript info.id is missing")
        activities: list[NormalizedActivity] = []
        for message in messages:
            if not isinstance(message, Mapping):
                continue
            for part in message.get("parts", []):
                if not isinstance(part, Mapping):
                    continue
                if part.get("type") == "patch":
                    for file_path in part.get("files", []):
                        activities.append(
                            NormalizedActivity(
                                provider=self.provider,
                                session_id=session_id,
                                kind="patch",
                                tool="patch",
                                status="completed",
                                outcome="success",
                                path=str(file_path),
                            )
                        )
                    continue
                if part.get("type") != "tool":
                    continue
                tool = str(part.get("tool") or "unknown")
                state = _mapping(part.get("state"))
                data = _mapping(state.get("input"))
                activities.append(
                    _activity(
                        provider=self.provider,
                        session_id=session_id,
                        tool=tool,
                        data=data,
                        status=state.get("status"),
                        result=state.get("output"),
                        provider_detail={"workdir": data.get("workdir")},
                    )
                )
        return NormalizedTranscript(
            provider=self.provider,
            session_id=session_id,
            repository_path=info.get("directory") if isinstance(info.get("directory"), str) else None,
            content_hash=hashlib.sha256(raw).hexdigest(),
            activities=tuple(activities),
            payload=dict(payload),
        )


def _jsonl(raw: bytes) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in raw.decode("utf-8-sig", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, Mapping):
            rows.append(dict(value))
    return rows


def _patch_paths(patch: str) -> list[str]:
    paths: list[str] = []
    for match in PATCH_PATH_RE.finditer(patch):
        value = match.group(1).strip()
        if value and value not in paths and value != "/dev/null":
            paths.append(value)
    return paths


def _patch_changes(patch: str) -> list[tuple[str, str, str]]:
    """Extract per-file old/new hunk text from Codex apply_patch payloads."""

    changes: list[tuple[str, str, str]] = []
    current_path: str | None = None
    old_lines: list[str] = []
    new_lines: list[str] = []

    def flush() -> None:
        nonlocal current_path, old_lines, new_lines
        if current_path is not None:
            changes.append(
                (current_path, "\n".join(old_lines), "\n".join(new_lines))
            )
        current_path = None
        old_lines = []
        new_lines = []

    for line in patch.splitlines():
        header = re.match(r"\*\*\*\s+(?:Update|Add|Delete)\s+File:\s*(.+)", line)
        if header:
            flush()
            current_path = header.group(1).strip()
            continue
        if current_path is None:
            continue
        if line.startswith("*** "):
            flush()
            continue
        if line.startswith("@@"):
            continue
        if line.startswith("+") and not line.startswith("+++"):
            new_lines.append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            old_lines.append(line[1:])
        elif line.startswith(" "):
            old_lines.append(line[1:])
            new_lines.append(line[1:])
    flush()
    return changes


class CodexAdapter(TranscriptAdapter):
    provider = "codex"

    def parse(self, path: Path, raw: bytes) -> NormalizedTranscript:
        rows = _jsonl(raw)
        session_id = path.stem
        repository_path: str | None = None
        calls: list[tuple[dict[str, Any], str | None]] = []
        outputs: dict[str, tuple[Any, bool]] = {}
        for row in rows:
            payload = _mapping(row.get("payload"))
            payload_type = payload.get("type")
            if row.get("type") == "session_meta":
                session_id = str(payload.get("session_id") or payload.get("id") or session_id)
                repository_path = payload.get("cwd") if isinstance(payload.get("cwd"), str) else repository_path
            elif row.get("type") == "turn_context":
                repository_path = payload.get("cwd") if isinstance(payload.get("cwd"), str) else repository_path
            elif row.get("type") == "response_item" and payload_type in {"function_call", "custom_tool_call"}:
                calls.append((payload, payload.get("call_id")))
            elif row.get("type") == "response_item" and payload_type in {"function_call_output", "custom_tool_call_output"}:
                call_id = payload.get("call_id")
                if call_id:
                    result = payload.get("output")
                    if result is None:
                        result = payload.get("result")
                    outputs[str(call_id)] = (result, bool(payload.get("is_error")))
        activities: list[NormalizedActivity] = []
        for payload, call_id_value in calls:
            tool = str(payload.get("name") or "shell_command")
            data = _mapping(payload.get("arguments"))
            if not data:
                data = _mapping(payload.get("input"))
            result, is_error = outputs.get(str(call_id_value), (None, False))
            if tool == "apply_patch":
                patch = _text(payload.get("input") or payload.get("arguments"))
                changes = _patch_changes(patch)
                if changes:
                    patch_rows: list[tuple[str | None, str | None, str | None]] = [
                        (patch_path, old_text, new_text)
                        for patch_path, old_text, new_text in changes
                    ]
                else:
                    patch_rows = [
                        (patch_path, None, None)
                        for patch_path in (_patch_paths(patch) or [None])
                    ]
                for patch_path, old_text, new_text in patch_rows:
                    activities.append(
                        _activity(
                            provider=self.provider,
                            session_id=session_id,
                            tool=tool,
                            data={
                                "path": patch_path,
                                "oldString": old_text,
                                "newString": new_text,
                                "content": patch,
                            },
                            status="completed" if str(call_id_value) in outputs else "unknown",
                            result=result,
                            call_id=str(call_id_value) if call_id_value else None,
                            is_error=is_error,
                            kind="patch",
                        )
                    )
            else:
                activities.append(
                    _activity(
                        provider=self.provider,
                        session_id=session_id,
                        tool=tool,
                        data=data,
                        status="completed" if str(call_id_value) in outputs else "unknown",
                        result=result,
                        call_id=str(call_id_value) if call_id_value else None,
                        is_error=is_error,
                        provider_detail={"cwd": data.get("workdir")},
                    )
                )
        return NormalizedTranscript(
            provider=self.provider,
            session_id=session_id,
            repository_path=repository_path,
            content_hash=hashlib.sha256(raw).hexdigest(),
            activities=tuple(activities),
        )


class ClaudeAdapter(TranscriptAdapter):
    provider = "claude"

    def parse(self, path: Path, raw: bytes) -> NormalizedTranscript:
        rows = _jsonl(raw)
        session_id = path.stem
        repository_path: str | None = None
        uses: list[tuple[dict[str, Any], str]] = []
        results: dict[str, tuple[Any, bool]] = {}
        for row in rows:
            if isinstance(row.get("session_id"), str):
                session_id = row["session_id"]
            if isinstance(row.get("cwd"), str):
                repository_path = row["cwd"]
            message = _mapping(row.get("message"))
            if row.get("type") == "assistant":
                for content in message.get("content", []):
                    if not isinstance(content, Mapping) or content.get("type") != "tool_use":
                        continue
                    tool_id = str(content.get("id") or "")
                    if tool_id:
                        uses.append((dict(content), tool_id))
            elif row.get("type") == "user":
                for content in message.get("content", []):
                    if not isinstance(content, Mapping) or content.get("type") != "tool_result":
                        continue
                    tool_id = content.get("tool_use_id")
                    if tool_id:
                        results[str(tool_id)] = (content.get("content"), bool(content.get("is_error")))
        activities: list[NormalizedActivity] = []
        for content, tool_id in uses:
            tool = str(content.get("name") or "unknown")
            data = _mapping(content.get("input"))
            result, is_error = results.get(tool_id, (None, False))
            activities.append(
                _activity(
                    provider=self.provider,
                    session_id=session_id,
                    tool=tool,
                    data=data,
                    status="completed" if tool_id in results else "unknown",
                    result=result,
                    call_id=tool_id,
                    is_error=is_error,
                )
            )
        return NormalizedTranscript(
            provider=self.provider,
            session_id=session_id,
            repository_path=repository_path,
            content_hash=hashlib.sha256(raw).hexdigest(),
            activities=tuple(activities),
        )


class KiroAdapter(TranscriptAdapter):
    provider = "kiro"

    def parse(self, path: Path, raw: bytes) -> NormalizedTranscript:
        rows = _jsonl(raw)
        session_id = path.stem
        repository_path: str | None = None
        metadata_path = path.with_suffix(".json")
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                metadata = {}
            if isinstance(metadata, Mapping):
                session_id = str(metadata.get("session_id") or session_id)
                repository_path = (
                    metadata.get("cwd")
                    if isinstance(metadata.get("cwd"), str)
                    else repository_path
                )
        uses: list[tuple[dict[str, Any], str]] = []
        results: dict[str, tuple[Any, bool, Any]] = {}
        for row in rows:
            data = _mapping(row.get("data"))
            meta = _mapping(data.get("meta"))
            env = _mapping(meta.get("env_context"))
            env_state = _mapping(env.get("env_state"))
            if isinstance(env_state.get("current_working_directory"), str):
                repository_path = env_state["current_working_directory"]
            if row.get("kind") == "AssistantMessage":
                for content in data.get("content", []):
                    if not isinstance(content, Mapping) or content.get("kind") != "toolUse":
                        continue
                    tool = _mapping(content.get("data"))
                    tool_id = str(tool.get("toolUseId") or "")
                    if tool_id:
                        uses.append((tool, tool_id))
            elif row.get("kind") == "ToolResults":
                for content in data.get("content", []):
                    if not isinstance(content, Mapping) or content.get("kind") != "toolResult":
                        continue
                    result = _mapping(content.get("data"))
                    tool_id = result.get("toolUseId")
                    if tool_id:
                        results[str(tool_id)] = (
                            result.get("content"),
                            str(result.get("status") or "").lower() in {"error", "failed"},
                            result.get("status"),
                        )
        activities: list[NormalizedActivity] = []
        for tool, tool_id in uses:
            name = str(tool.get("name") or "unknown")
            input_data = _mapping(tool.get("input"))
            result, is_error, result_status = results.get(tool_id, (None, False, None))
            if name in {"read", "fs_read"} and isinstance(input_data.get("operations"), list):
                operations = [
                    operation
                    for operation in input_data["operations"]
                    if isinstance(operation, Mapping)
                ]
                for operation in operations:
                    if str(operation.get("mode") or "").lower() == "directory":
                        kind = "search"
                    else:
                        kind = "read"
                    activities.append(
                        _activity(
                            provider=self.provider,
                            session_id=session_id,
                            tool=name,
                            data=operation,
                            status=result_status or ("completed" if tool_id in results else "unknown"),
                            result=result,
                            call_id=tool_id,
                            is_error=is_error,
                            kind=kind,
                            provider_detail={
                                "operation_mode": operation.get("mode"),
                                "purpose": input_data.get("__tool_use_purpose"),
                            },
                        )
                    )
                if operations:
                    continue
            # Kiro's fs_write tool uses a command/path envelope rather than the
            # OpenCode/Claude filePath envelope.
            if name in {"write", "fs_write"} and input_data.get("command") in {"create", "strReplace", "str_replace"}:
                write_command = input_data.get("command")
                if write_command in {"strReplace", "str_replace"}:
                    input_data = {
                        "path": input_data.get("path"),
                        "oldStr": input_data.get("oldStr"),
                        "newStr": input_data.get("newStr"),
                    }
                else:
                    input_data = {"path": input_data.get("path"), "content": input_data.get("content")}
            activities.append(
                _activity(
                    provider=self.provider,
                    session_id=session_id,
                    tool=name,
                    data=input_data,
                    status=result_status or ("completed" if tool_id in results else "unknown"),
                    result=result,
                    call_id=tool_id,
                    is_error=is_error,
                    kind=(
                        "edit"
                        if name in {"write", "fs_write"}
                        and "oldStr" in input_data
                        else None
                    ),
                )
            )
        return NormalizedTranscript(
            provider=self.provider,
            session_id=session_id,
            repository_path=repository_path,
            content_hash=hashlib.sha256(raw).hexdigest(),
            activities=tuple(activities),
        )


ADAPTERS: tuple[TranscriptAdapter, ...] = (
    OpenCodeAdapter(),
    CodexAdapter(),
    ClaudeAdapter(),
    KiroAdapter(),
)


def detect_adapter(path: Path, raw: bytes) -> TranscriptAdapter:
    """Detect by envelope shape; filename hints are only a last resort."""

    text = raw.decode("utf-8-sig", errors="replace")
    if "opencode" in text[:500].lower():
        return OpenCodeAdapter()
    match = JSON_START_RE.search(ANSI_RE.sub("", text))
    if match:
        try:
            value = json.loads(ANSI_RE.sub("", text)[match.start() :])
        except json.JSONDecodeError:
            value = None
        if isinstance(value, Mapping) and isinstance(value.get("info"), Mapping) and isinstance(value.get("messages"), list):
            return OpenCodeAdapter()
    rows = _jsonl(raw)
    if any(row.get("type") == "response_item" or row.get("type") == "session_meta" for row in rows):
        return CodexAdapter()
    if any(row.get("type") == "assistant" and isinstance(row.get("message"), Mapping) for row in rows):
        return ClaudeAdapter()
    if any(row.get("kind") in {"AssistantMessage", "ToolResults", "Prompt"} for row in rows):
        return KiroAdapter()
    raise ValueError(f"unable to detect transcript provider: {path}")


def parse_transcript_file(path: str | Path, *, provider: str | None = None) -> NormalizedTranscript:
    source_path = Path(path)
    try:
        raw = source_path.read_bytes()
    except OSError as exc:
        raise ValueError(f"transcript unavailable: {source_path}: {exc}") from exc
    if provider:
        adapter = next((item for item in ADAPTERS if item.provider == provider), None)
        if adapter is None:
            raise ValueError(f"unsupported transcript provider: {provider}")
    else:
        adapter = detect_adapter(source_path, raw)
    return adapter.parse(source_path, raw)
