from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest

from accepted_work import BackgroundWorker, WorkLedger
from graph_refresh import (
    ChangedScopeGraphRefresher,
    FunctionRefreshRecord,
    HelixGraphRefreshBackend,
)
from work_affinity import AcceptedWorkProcessor, GitRangeAnalyzer


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=repo, text=True, encoding="utf-8"
    ).strip()


def _init(repo: Path) -> None:
    repo.mkdir()
    subprocess.check_call(["git", "init", "-q"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "Test"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "test@example.com"], cwd=repo)


def _commit(repo: Path, message: str) -> str:
    subprocess.check_call(["git", "add", "."], cwd=repo)
    subprocess.check_call(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-q",
            "-m",
            message,
        ],
        cwd=repo,
    )
    return _git(repo, "rev-parse", "HEAD")


class FakeEmbedder:
    def encode(self, texts):
        return [[float(index + 1), 0.5] for index, _ in enumerate(texts)]


class FakeBackend:
    def __init__(self):
        self.functions = {}
        self.calls = {}
        self.inactive = set()
        self.external = {}
        self.fail_upsert = False
        self.finalized = 0

    def resolve_function_names(self, names):
        return {name: list(self.external.get(name, [])) for name in names}

    def upsert_function(self, function):
        if self.fail_upsert:
            raise RuntimeError("helix unavailable")
        self.functions[function.node_id] = function

    def replace_outgoing_calls(self, source_id, target_ids):
        missing = [target for target in target_ids if target not in self.functions]
        if missing:
            raise RuntimeError(f"missing targets: {missing}")
        self.calls[source_id] = tuple(sorted(target_ids))

    def mark_inactive(self, function_id):
        self.inactive.add(function_id)

    def finalize(self):
        self.finalized += 1


def test_changed_file_refresh_replaces_only_bounded_function_scope(tmp_path):
    repo = tmp_path / "repo"
    _init(repo)
    (repo / "app.py").write_text(
        "def helper():\n    return 1\n\n\ndef target():\n    return helper()\n",
        encoding="utf-8",
    )
    base = _commit(repo, "base")
    (repo / "app.py").write_text(
        "def helper():\n    return 2\n\n\ndef target():\n    return helper() + 1\n",
        encoding="utf-8",
    )
    head = _commit(repo, "change")
    backend = FakeBackend()
    analysis = GitRangeAnalyzer(repo, "demo").analyze(base, head)

    result = ChangedScopeGraphRefresher(
        repo, "demo", backend, embedder=FakeEmbedder()
    ).refresh(analysis)

    assert result.changed_python_files == ("app.py",)
    assert set(result.refreshed_functions) == {
        "demo:func_app_helper",
        "demo:func_app_target",
    }
    assert result.refreshed_edges == (
        ("demo:func_app_target", "demo:func_app_helper", "CALLS"),
    )
    assert backend.calls["demo:func_app_helper"] == ()
    assert backend.calls["demo:func_app_target"] == ("demo:func_app_helper",)
    assert backend.finalized == 1


def test_renamed_file_upserts_new_identity_and_deactivates_old(tmp_path):
    repo = tmp_path / "repo"
    _init(repo)
    (repo / "old.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    base = _commit(repo, "base")
    subprocess.check_call(["git", "mv", "old.py", "new.py"], cwd=repo)
    head = _commit(repo, "rename")
    analysis = GitRangeAnalyzer(repo, "demo").analyze(base, head)
    backend = FakeBackend()

    result = ChangedScopeGraphRefresher(
        repo, "demo", backend, embedder=FakeEmbedder()
    ).refresh(analysis)

    assert result.refreshed_functions == ("demo:func_new_target",)
    assert result.inactive_functions == ("demo:func_old_target",)
    assert "demo:func_new_target" in backend.functions
    assert "demo:func_old_target" in backend.inactive


def test_class_qualified_identity_resolves_self_calls_without_method_collisions(tmp_path):
    repo = tmp_path / "repo"
    _init(repo)
    (repo / "service.py").write_text(
        "class Websocket:\n"
        "    def reconnect(self):\n"
        "        return self.run()\n\n"
        "    def run(self):\n"
        "        return 'ws'\n\n"
        "class Http:\n"
        "    def run(self):\n"
        "        return 'http'\n",
        encoding="utf-8",
    )
    base = _commit(repo, "base")
    (repo / "service.py").write_text(
        "class Websocket:\n"
        "    def reconnect(self):\n"
        "        return self.run() + '!'\n\n"
        "    def run(self):\n"
        "        return 'ws'\n\n"
        "class Http:\n"
        "    def run(self):\n"
        "        return 'http'\n",
        encoding="utf-8",
    )
    head = _commit(repo, "change reconnect")
    analysis = GitRangeAnalyzer(repo, "demo").analyze(base, head)
    backend = FakeBackend()

    result = ChangedScopeGraphRefresher(
        repo, "demo", backend, embedder=FakeEmbedder()
    ).refresh(analysis)

    assert set(result.refreshed_functions) == {
        "demo:func_service_Websocket_reconnect",
        "demo:func_service_Websocket_run",
        "demo:func_service_Http_run",
    }
    assert result.refreshed_edges == (
        (
            "demo:func_service_Websocket_reconnect",
            "demo:func_service_Websocket_run",
            "CALLS",
        ),
    )
    assert "demo:func_service_reconnect" in result.migrated_legacy_functions
    assert "demo:func_service_run" not in result.migrated_legacy_functions


def test_submodule_snapshot_uses_accepted_pointer_not_later_checkout(tmp_path):
    inner = tmp_path / "inner"
    _init(inner)
    (inner / "service.py").write_text("def reconnect():\n    return 1\n", encoding="utf-8")
    _commit(inner, "inner base")
    outer = tmp_path / "outer"
    _init(outer)
    subprocess.check_call(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            str(inner),
            "vendor",
        ],
        cwd=outer,
        stdout=subprocess.DEVNULL,
    )
    base = _commit(outer, "add vendor")
    nested = outer / "vendor"
    (nested / "service.py").write_text("def reconnect():\n    return 2\n", encoding="utf-8")
    accepted_inner = _commit(nested, "accepted reconnect")
    accepted_head = _commit(outer, "advance vendor")
    (nested / "service.py").write_text("def reconnect():\n    return 999\n", encoding="utf-8")
    _commit(nested, "later unrelated checkout")
    assert _git(nested, "rev-parse", "HEAD") != accepted_inner

    analysis = GitRangeAnalyzer(outer, "demo").analyze(base, accepted_head)
    backend = FakeBackend()
    result = ChangedScopeGraphRefresher(
        outer, "demo", backend, embedder=FakeEmbedder()
    ).refresh(analysis)

    record = backend.functions["demo:func_vendor_service_reconnect"]
    assert "return 2" in record.source
    assert "return 999" not in record.source
    assert result.refreshed_functions == ("demo:func_vendor_service_reconnect",)


def test_refresh_failure_keeps_worker_retryable_and_analysis_unwritten(tmp_path):
    repo = tmp_path / "repo"
    _init(repo)
    (repo / "app.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    base = _commit(repo, "base")
    (repo / "app.py").write_text("def target():\n    return 2\n", encoding="utf-8")
    head = _commit(repo, "change")
    transcript = tmp_path / "session.json"
    transcript.write_text("not reached", encoding="utf-8")
    ledger = WorkLedger(tmp_path / "work.sqlite3")
    job_id, _ = ledger.enqueue(
        {
            "repo_id": "demo",
            "base_revision": base,
            "head_revision": head,
            "summary": "change target",
            "change_kind": "bug_fix",
            "domains": [],
            "transcript_ref": str(transcript),
        }
    )
    backend = FakeBackend()
    backend.fail_upsert = True
    refresher = ChangedScopeGraphRefresher(
        repo, "demo", backend, embedder=FakeEmbedder()
    )
    processor = AcceptedWorkProcessor(
        ledger, repo, "demo", graph_refresher=refresher
    )
    worker = BackgroundWorker(ledger, owner="worker")

    with pytest.raises(RuntimeError, match="helix unavailable"):
        worker.run_once(lambda job: processor.process(job, worker.owner))

    assert ledger.get(job_id).status == "retry_wait"
    assert ledger.analysis_details(job_id) is None


class FakeVectorIndex:
    def __init__(self):
        self.nodes = {}

    def insert(self, node_id, vector):
        self.nodes[node_id] = tuple(vector)
        return len(self.nodes)

    def remove(self, node_id):
        self.nodes.pop(node_id, None)
        return 0

    def save(self):
        return None


@pytest.mark.skipif(
    os.environ.get("HELIX_LIVE_TEST") != "1",
    reason="set HELIX_LIVE_TEST=1 with a disposable Helix graph",
)
def test_live_helix_function_and_calls_lifecycle():
    from helixdb import Client, Predicate, Projection, g, read_batch, write_batch

    url = os.environ.get("HELIX_URL", "http://127.0.0.1:6969")
    suffix = uuid.uuid4().hex[:12]
    source_id = f"live-test:func_source_{suffix}"
    target_id = f"live-test:func_target_{suffix}"
    vector_index = FakeVectorIndex()
    backend = HelixGraphRefreshBackend(url, vector_index=vector_index)
    client = Client(url)
    source = FunctionRefreshRecord(
        source_id,
        f"source_{suffix}",
        f"source_{suffix}",
        None,
        "live/source.py",
        "def source():\n    return target()\n",
        "source-hash",
        (1.0, 0.0),
        "live-head",
    )
    target = FunctionRefreshRecord(
        target_id,
        f"target_{suffix}",
        f"target_{suffix}",
        None,
        "live/target.py",
        "def target():\n    return 1\n",
        "target-hash",
        (0.0, 1.0),
        "live-head",
    )
    try:
        backend.upsert_function(source)
        backend.upsert_function(target)
        backend.replace_outgoing_calls(source_id, [target_id])
        response = client.query().dynamic(
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
        assert response["targets"]["properties"] == [{"node_id": target_id}]

        backend.replace_outgoing_calls(source_id, [])
        backend.mark_inactive(source_id)
        response = client.query().dynamic(
            read_batch()
            .var_as(
                "source",
                g()
                .n_with_label("FunctionIdentity")
                .where(Predicate.eq("node_id", source_id))
                .project(
                    [
                        Projection.property("node_id"),
                        Projection.property("active"),
                    ]
                ),
            )
            .returning(["source"])
            .to_dynamic_request()
        ).send()
        assert response["source"]["properties"] == [
            {"node_id": source_id, "active": False}
        ]
    finally:
        client.query().dynamic(
            write_batch()
            .var_as(
                "source",
                g()
                .n_with_label("FunctionIdentity")
                .where(Predicate.eq("node_id", source_id))
                .drop(),
            )
            .var_as(
                "target",
                g()
                .n_with_label("FunctionIdentity")
                .where(Predicate.eq("node_id", target_id))
                .drop(),
            )
            .returning(["source", "target"])
            .to_dynamic_request()
        ).send()
