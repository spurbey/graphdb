from __future__ import annotations

import json
from pathlib import Path

from transcript_adapters import (
    ClaudeAdapter,
    CodexAdapter,
    OpenCodeAdapter,
    parse_transcript_file,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )


def test_opencode_adapter_normalizes_read_edit_and_failed_shell(tmp_path):
    path = tmp_path / "opencode.json"
    payload = {
        "info": {"id": "oc-1", "directory": str(tmp_path)},
        "messages": [
            {
                "parts": [
                    {
                        "type": "tool",
                        "tool": "read",
                        "state": {
                            "status": "completed",
                            "input": {"filePath": "src/a.py"},
                            "output": "10: def run():\n11:     return 1",
                        },
                    },
                    {
                        "type": "tool",
                        "tool": "edit",
                        "state": {
                            "status": "completed",
                            "input": {
                                "filePath": "src/a.py",
                                "oldString": "old",
                                "newString": "new",
                            },
                            "output": "ok",
                        },
                    },
                    {
                        "type": "tool",
                        "tool": "bash",
                        "state": {
                            "status": "completed",
                            "input": {"command": "python -c 'raise SystemExit(1)'"},
                            "output": "ParserError: invalid command",
                        },
                    },
                ]
            }
        ],
    }
    path.write_text("warning\n" + json.dumps(payload), encoding="utf-8")

    transcript = OpenCodeAdapter().parse(path, path.read_bytes())

    assert transcript.provider == "opencode"
    assert [activity.kind for activity in transcript.activities] == [
        "read",
        "edit",
        "shell",
    ]
    assert transcript.activities[0].line_start == 10
    assert transcript.activities[0].line_end == 11
    assert transcript.activities[2].outcome == "failed"


def test_codex_adapter_pairs_function_calls_and_outputs(tmp_path):
    path = tmp_path / "rollout.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "session_meta", "payload": {"session_id": "cx-1", "cwd": str(tmp_path)}},
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "shell_command",
                    "call_id": "call-1",
                    "arguments": json.dumps({"command": "git commit -m fix", "workdir": str(tmp_path)}),
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call-1",
                    "output": "[main abc] fix",
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "apply_patch",
                    "call_id": "call-2",
                    "input": "*** Update File: src/a.py\n@@\n-old\n+new\n",
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "call-2",
                    "output": "ok",
                },
            },
        ],
    )

    transcript = CodexAdapter().parse(path, path.read_bytes())

    assert transcript.session_id == "cx-1"
    assert [activity.kind for activity in transcript.activities] == ["commit", "patch"]
    assert transcript.activities[0].outcome == "success"
    assert transcript.activities[1].path == "src/a.py"
    assert transcript.activities[1].old_text == "old"
    assert transcript.activities[1].new_text == "new"


def test_codex_shell_file_read_is_normalized_as_read(tmp_path):
    path = tmp_path / "rollout.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "session_meta", "payload": {"session_id": "cx-2", "cwd": str(tmp_path)}},
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "shell_command",
                    "call_id": "call-read",
                    "arguments": json.dumps({"command": "Get-Content -Raw src/a.py"}),
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call-read",
                    "output": "Exit code: 0\nOutput:\n1: def run():\n2:     return 1",
                },
            },
        ],
    )

    transcript = parse_transcript_file(path)

    activity = transcript.activities[0]
    assert activity.kind == "read"
    assert activity.path == "src/a.py"
    assert activity.outcome == "success"
    assert activity.line_start == 1
    assert activity.line_end == 2


def test_claude_adapter_pairs_tool_use_and_tool_result(tmp_path):
    path = tmp_path / "claude.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "system", "cwd": str(tmp_path)},
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "Read",
                            "input": {"file_path": "src/a.py", "offset": 4, "limit": 3},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tool-1", "content": "ok"}
                    ]
                },
            },
        ],
    )

    transcript = ClaudeAdapter().parse(path, path.read_bytes())

    assert transcript.provider == "claude"
    assert transcript.repository_path == str(tmp_path)
    assert transcript.activities[0].kind == "read"
    assert transcript.activities[0].line_start == 5
    assert transcript.activities[0].line_end == 7


def test_kiro_adapter_expands_read_operations_and_write_envelopes(tmp_path):
    path = tmp_path / "kiro.jsonl"
    path.with_suffix(".json").write_text(
        json.dumps({"session_id": "kiro-1", "cwd": str(tmp_path)}),
        encoding="utf-8",
    )
    _write_jsonl(
        path,
        [
            {
                "kind": "AssistantMessage",
                "data": {
                    "content": [
                        {
                            "kind": "toolUse",
                            "data": {
                                "toolUseId": "read-1",
                                "name": "read",
                                "input": {
                                    "operations": [
                                        {"mode": "Line", "path": "src/a.py", "offset": 2, "limit": 4},
                                        {"mode": "Directory", "path": "src"},
                                    ]
                                },
                            },
                        },
                        {
                            "kind": "toolUse",
                            "data": {
                                "toolUseId": "write-1",
                                "name": "fs_write",
                                "input": {
                                    "command": "strReplace",
                                    "path": "src/a.py",
                                    "oldStr": "old",
                                    "newStr": "new",
                                },
                            },
                        },
                    ]
                },
            },
            {
                "kind": "ToolResults",
                "data": {
                    "content": [
                        {
                            "kind": "toolResult",
                            "data": {
                                "toolUseId": "read-1",
                                "status": "success",
                                "content": [
                                    {"kind": "text", "data": "3: def run():\n4:     return 1"}
                                ],
                            },
                        },
                        {"kind": "toolResult", "data": {"toolUseId": "write-1", "status": "success", "content": "ok"}},
                    ]
                },
            },
        ],
    )

    transcript = parse_transcript_file(path)

    assert transcript.provider == "kiro"
    assert transcript.session_id == "kiro-1"
    assert transcript.repository_path == str(tmp_path)
    assert [activity.kind for activity in transcript.activities] == ["read", "search", "edit"]
    assert transcript.activities[0].path == "src/a.py"
    assert transcript.activities[0].line_start == 3
    assert transcript.activities[0].line_end == 4
    assert transcript.activities[2].old_text == "old"
