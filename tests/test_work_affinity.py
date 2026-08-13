from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from accepted_work import BackgroundWorker, WorkLedger
from transcript_adapters import NormalizedActivity, NormalizedTranscript
from work_affinity import (
    AcceptedWorkProcessor,
    FunctionCatalog,
    GitRangeAnalyzer,
    TranscriptParser,
    build_affinity_evidence,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=repo, text=True, encoding="utf-8"
    ).strip()


def _commit(repo: Path, message: str) -> str:
    subprocess.check_call(["git", "add", "."], cwd=repo)
    subprocess.check_call(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", message],
        cwd=repo,
        stdout=subprocess.DEVNULL,
    )
    return _git(repo, "rev-parse", "HEAD")


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    subprocess.check_call(["git", "init", "-q"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "Test"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "test@example.com"], cwd=repo)


def _session(path: Path, repo: Path, parts: list[dict]) -> None:
    payload = {
        "info": {"id": "ses_test", "directory": str(repo)},
        "messages": [{"info": {"role": "assistant"}, "parts": parts}],
    }
    path.write_text(
        "opencode.exe : Exporting session: ses_test\nPowerShell warning\n"
        + json.dumps(payload),
        encoding="utf-8-sig",
    )


def _tool(name: str, inputs: dict, output: str = "ok") -> dict:
    return {
        "type": "tool",
        "tool": name,
        "state": {"status": "completed", "input": inputs, "output": output},
    }


def test_transcript_parser_handles_preamble_and_rejects_error_only_export(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    valid = tmp_path / "valid.json"
    _session(valid, repo, [_tool("read", {"filePath": str(repo / "a.py")})])

    parsed = TranscriptParser().parse_file(valid)
    assert parsed.session_id == "ses_test"

    invalid = tmp_path / "invalid.json"
    invalid.write_text("opencode.exe : Exporting session\nError: Session not found", encoding="utf-8")
    with pytest.raises(Exception, match="no JSON payload"):
        TranscriptParser().parse_file(invalid)


def test_catalog_resolves_only_exact_function_scope(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text(
        "def one():\n    return 1\n\n\ndef two():\n    return 2\n", encoding="utf-8"
    )
    catalog = FunctionCatalog(repo, "demo")

    one, resolution = catalog.resolve("a.py", line_start=1, line_end=2)
    assert one is not None and one.name == "one"
    assert resolution == "exact_line_range"
    whole, resolution = catalog.resolve("a.py", line_start=1, line_end=6)
    assert whole is None
    assert resolution == "ambiguous_line_range"


def test_catalog_qualifies_same_named_methods_by_class(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "service.py").write_text(
        "class Websocket:\n"
        "    def run(self):\n"
        "        return 'ws'\n\n"
        "class Http:\n"
        "    def run(self):\n"
        "        return 'http'\n",
        encoding="utf-8",
    )
    catalog = FunctionCatalog(repo, "demo")

    spans = catalog.functions("service.py")

    assert [span.qualified_name for span in spans] == ["Websocket.run", "Http.run"]
    assert [span.node_id for span in spans] == [
        "demo:func_service_Websocket_run",
        "demo:func_service_Http_run",
    ]


def test_edit_resolves_unique_symbol_when_final_source_differs_from_edit_snippet(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "service.py").write_text(
        "class Service:\n"
        "    async def reconnect(self):\n"
        "        self.task = None\n"
        "        return await self.open()\n",
        encoding="utf-8",
    )
    transcript = tmp_path / "session.json"
    _session(
        transcript,
        repo,
        [
            _tool(
                "edit",
                {
                    "filePath": str(repo / "service.py"),
                    "oldString": "async def reconnect(self):\n    return await self.open()",
                    "newString": "async def reconnect(self):\n    if self.task.done():\n        self.task = None\n    return await self.open()",
                },
            )
        ],
    )
    parsed = TranscriptParser().parse_file(transcript)

    events = TranscriptParser().events(parsed, FunctionCatalog(repo, "demo"))

    assert events[0]["function_id"] == "demo:func_service_Service_reconnect"
    assert events[0]["resolution"] == "exact_symbol"


def test_numbered_read_disambiguates_same_named_methods_by_line_range(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "service.py").write_text(
        "class Websocket:\n"
        "    def run(self):\n"
        "        return 'ws'\n\n"
        "class Http:\n"
        "    def run(self):\n"
        "        return 'http'\n",
        encoding="utf-8",
    )
    transcript = tmp_path / "session.json"
    _session(
        transcript,
        repo,
        [
            _tool(
                "read",
                {"filePath": str(repo / "service.py")},
                "<content>\n2:     def run(self):\n3:         return 'ws'\n</content>",
            )
        ],
    )
    parsed = TranscriptParser().parse_file(transcript)

    events = TranscriptParser().events(parsed, FunctionCatalog(repo, "demo"))

    assert events[0]["function_id"] == "demo:func_service_Websocket_run"
    assert events[0]["resolution"] == "exact_symbol_line_range"


def test_codex_patch_hunk_resolves_changed_function(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "service.py").write_text(
        "class Service:\n"
        "    def reconnect(self):\n"
        "        return 'old'\n",
        encoding="utf-8",
    )
    transcript = NormalizedTranscript(
        provider="codex",
        session_id="codex-test",
        repository_path=str(repo),
        content_hash="hash",
        activities=(
            NormalizedActivity(
                provider="codex",
                session_id="codex-test",
                kind="patch",
                tool="apply_patch",
                outcome="success",
                path="service.py",
                old_text="    def reconnect(self):\n        return 'old'",
                new_text="    def reconnect(self):\n        return 'new'",
            ),
        ),
    )

    events = TranscriptParser().events(transcript, FunctionCatalog(repo, "demo"))

    assert events[0]["event_kind"] == "changed_patch"
    assert events[0]["function_id"] == "demo:func_service_Service_reconnect"
    assert events[0]["resolution"] == "exact_symbol"


def test_positive_evidence_uses_strongest_role_and_known_dimensions():
    events = [
        {"event_kind": "read", "function_id": "source"},
        {"event_kind": "read", "function_id": "source"},
        {"event_kind": "test", "function_id": "tester"},
        {"event_kind": "search", "function_id": None},
    ]
    evidence, unknown = build_affinity_evidence(
        ["target_a", "target_b"],
        events,
        change_kind="bug_fix",
        domains=["auth.login", "unknown"],
        known_domains={"auth.login"},
    )

    assert unknown == ("unknown",)
    assert {row["dimension_type"] for row in evidence} == {"global", "change_kind", "domain"}
    assert any(
        row["source_function_id"] == "source"
        and row["target_function_id"] == "target_a"
        and row["role"] == "repeated_read"
        and row["evidence"] == 0.25
        for row in evidence
    )
    assert any(row["role"] == "targeted_operation" for row in evidence)
    assert any(row["role"] == "changed" for row in evidence)


def test_end_to_end_git_transcript_analysis_and_local_normalization(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-q"], cwd=repo)
    (repo / "app.py").write_text(
        "def helper():\n    return 1\n\n\ndef target():\n    return helper()\n",
        encoding="utf-8",
    )
    base = _commit(repo, "base")
    (repo / "app.py").write_text(
        "def helper():\n    return 1\n\n\ndef target():\n    return helper() + 1\n",
        encoding="utf-8",
    )
    head = _commit(repo, "change target")

    transcript = tmp_path / "session.json"
    read_output = "<content>\n1: def helper():\n2:     return 1\n</content>"
    _session(
        transcript,
        repo,
        [
            _tool("grep", {"pattern": "helper", "path": str(repo)}),
            _tool("read", {"filePath": str(repo / "app.py")}, read_output),
            _tool("read", {"filePath": str(repo / "app.py")}, read_output),
        ],
    )

    ledger = WorkLedger(tmp_path / "work.sqlite3")
    job_id, _ = ledger.enqueue(
        {
            "repo_id": "demo",
            "base_revision": base,
            "head_revision": head,
            "summary": "Adjust target behavior",
            "change_kind": "bug_fix",
            "domains": ["core", "not.registered"],
            "transcript_ref": str(transcript),
        }
    )
    worker = BackgroundWorker(ledger, owner="worker")
    processor = AcceptedWorkProcessor(
        ledger, repo, "demo", domain_registry={"core"}
    )

    completed = worker.run_once(lambda job: processor.process(job, worker.owner))

    assert completed is not None and completed.status == "applied"
    details = ledger.analysis_details(job_id)
    assert details is not None
    assert details["changed_functions"] == ["demo:func_app_target"]
    assert details["unknown_domains"] == ["not.registered"]
    assert sum(1 for event in details["events"] if event["function_id"]) == 2
    neighbor = ledger.affinity_neighbors(
        "demo:func_app_helper", "global", "global"
    )
    assert neighbor == [
        {
            "target_function_id": "demo:func_app_target",
            "evidence_total": 0.25,
            "episode_count": 1,
            "weight": pytest.approx(0.25 / 3.25),
        }
    ]


def test_submodule_range_uses_outer_paths_and_exact_edit_targets(tmp_path):
    origin = tmp_path / "pipecat-origin"
    _init_repo(origin)
    source_root = origin / "src" / "pipecat"
    source_root.mkdir(parents=True)
    (source_root / "tts.py").write_text(
        "def _connect():\n    return 'old'\n\n\ndef _keepalive_task_handler():\n    return True\n",
        encoding="utf-8",
    )
    (source_root / "voicemail.py").write_text(
        "def detect_voicemail():\n    return False\n", encoding="utf-8"
    )
    _commit(origin, "inner base")

    repo = tmp_path / "outer"
    _init_repo(repo)
    subprocess.check_call(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            str(origin),
            "pipecat",
        ],
        cwd=repo,
        stdout=subprocess.DEVNULL,
    )
    base = _commit(repo, "add pipecat")

    nested = repo / "pipecat"
    nested_source = nested / "src" / "pipecat"
    (nested_source / "tts.py").write_text(
        "def _connect():\n    return 'reconnected'\n\n\ndef _keepalive_task_handler():\n    return True\n",
        encoding="utf-8",
    )
    _commit(nested, "fix reconnect")
    (nested_source / "voicemail.py").write_text(
        "def detect_voicemail():\n    return True\n", encoding="utf-8"
    )
    _commit(nested, "fix voicemail")
    head = _commit(repo, "advance pipecat")

    (nested_source / "tts.py").write_text(
        "def later_checkout_only():\n    return 'not accepted'\n", encoding="utf-8"
    )
    _commit(nested, "later checkout")

    analysis = GitRangeAnalyzer(repo, "demo").analyze(base, head)
    changed_ids = {row.node_id for row in analysis.changed_functions}
    assert "pipecat/src/pipecat/tts.py" in analysis.changed_files
    assert "pipecat/src/pipecat/voicemail.py" in analysis.changed_files
    assert "demo:func_pipecat_src_pipecat_tts__connect" in changed_ids
    assert "demo:func_pipecat_src_pipecat_voicemail_detect_voicemail" in changed_ids
    assert all(not path.startswith("pipecat/pipecat/") for path in analysis.changed_files)

    transcript = tmp_path / "submodule-session.json"
    _session(
        transcript,
        repo,
        [
            _tool(
                "edit",
                {
                    "filePath": str(nested_source / "tts.py"),
                    "oldString": "return 'old'",
                    "newString": "return 'reconnected'",
                },
            ),
            _tool(
                "read",
                {"filePath": str(nested_source / "tts.py")},
                "<content>\n5: def _keepalive_task_handler():\n6:     return True\n</content>",
            ),
        ],
    )
    ledger = WorkLedger(tmp_path / "submodule-work.sqlite3")
    job_id, _ = ledger.enqueue(
        {
            "repo_id": "demo",
            "base_revision": base,
            "head_revision": head,
            "summary": "Fix reconnect while advancing a multi-commit submodule range",
            "change_kind": "bug_fix",
            "domains": ["voice.elevenlabs"],
            "transcript_ref": str(transcript),
        }
    )
    worker = BackgroundWorker(ledger, owner="worker")
    processor = AcceptedWorkProcessor(
        ledger, repo, "demo", domain_registry={"voice.elevenlabs"}
    )

    completed = worker.run_once(lambda job: processor.process(job, worker.owner))

    assert completed is not None and completed.status == "applied"
    details = ledger.analysis_details(job_id)
    assert details is not None
    assert set(details["changed_functions"]) == changed_ids
    assert details["stats"]["affinity_target_policy"] == (
        "transcript_exact_change_intersection"
    )
    assert details["stats"]["affinity_target_functions"] == [
        "demo:func_pipecat_src_pipecat_tts__connect"
    ]
    assert details["evidence"]
    assert {
        row["target_function_id"] for row in details["evidence"]
    } == {"demo:func_pipecat_src_pipecat_tts__connect"}


def test_large_change_is_audited_but_skips_affinity(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-q"], cwd=repo)
    (repo / "many.py").write_text(
        "\n\n".join(f"def f{i}():\n    return {i}" for i in range(21)) + "\n",
        encoding="utf-8",
    )
    base = _commit(repo, "base")
    (repo / "many.py").write_text(
        "\n\n".join(f"def f{i}():\n    return {i + 1}" for i in range(21)) + "\n",
        encoding="utf-8",
    )
    head = _commit(repo, "many changes")
    transcript = tmp_path / "session.json"
    _session(transcript, repo, [_tool("read", {"filePath": str(repo / "many.py")})])
    ledger = WorkLedger(tmp_path / "work.sqlite3")
    job_id, _ = ledger.enqueue(
        {
            "repo_id": "demo",
            "base_revision": base,
            "head_revision": head,
            "summary": "Bulk change",
            "change_kind": "architecture",
            "domains": [],
            "transcript_ref": str(transcript),
        }
    )
    worker = BackgroundWorker(ledger, owner="worker")
    processor = AcceptedWorkProcessor(ledger, repo, "demo")

    worker.run_once(lambda job: processor.process(job, worker.owner))

    details = ledger.analysis_details(job_id)
    assert details is not None
    assert len(details["changed_functions"]) == 21
    assert details["evidence"] == []
    assert details["affinity_skipped_reason"] == "affinity_target_limit:21>20"
