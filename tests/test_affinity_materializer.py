from __future__ import annotations

import os

import pytest

from accepted_work import WorkLedger
from affinity_materializer import AffinityMaterializer, HelixAffinityBackend


class FakeBackend:
    def __init__(self):
        self.pairs = {}
        self.calls = []
        self.fail_once = False

    def replace_pair(self, source, target, payload):
        self.calls.append((source, target, payload))
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("helix unavailable")
        if payload is None:
            self.pairs.pop((source, target), None)
        else:
            self.pairs[(source, target)] = payload


def _seed_analysis(ledger: WorkLedger, suffix: str, targets: list[str]) -> str:
    payload = {
        "repo_id": "demo",
        "base_revision": f"base-{suffix}",
        "head_revision": f"head-{suffix}",
        "summary": "accepted work",
        "change_kind": "bug_fix",
        "domains": ["core"],
        "transcript_ref": f"session-{suffix}",
    }
    job_id, _ = ledger.enqueue(payload)
    job = ledger.claim("worker")
    assert job is not None and job.job_id == job_id
    evidence = []
    for target in targets:
        for dimension_type, dimension_value in (
            ("global", "global"),
            ("domain", "core"),
            ("change_kind", "bug_fix"),
        ):
            evidence.append(
                {
                    "source_function_id": "source",
                    "target_function_id": target,
                    "dimension_type": dimension_type,
                    "dimension_value": dimension_value,
                    "role": "changed",
                    "evidence": 1.0,
                }
            )
    ledger.record_analysis(
        job,
        "worker",
        transcript_hash=f"hash-{suffix}",
        changed_files=[],
        changed_functions=targets,
        stats={},
        events=[],
        evidence=evidence,
    )
    ledger.mark_applied(job_id, "worker", {})
    return job_id


def test_materializer_writes_absolute_sparse_pair_payload(tmp_path):
    ledger = WorkLedger(tmp_path / "work.sqlite3")
    _seed_analysis(ledger, "one", ["target"])
    backend = FakeBackend()

    result = AffinityMaterializer(ledger, backend).flush_pending()

    assert result.applied == 1
    assert result.remaining == 0
    payload = backend.pairs[("source", "target")]
    assert payload["global"] == pytest.approx(0.25)
    assert payload["domains"] == {"core": pytest.approx(0.25)}
    assert payload["change_kinds"] == {"bug_fix": pytest.approx(0.25)}
    assert payload["episode_count"] == 1


def test_failed_write_remains_pending_and_retry_replaces_same_pair(tmp_path):
    ledger = WorkLedger(tmp_path / "work.sqlite3")
    _seed_analysis(ledger, "one", ["target"])
    backend = FakeBackend()
    backend.fail_once = True
    materializer = AffinityMaterializer(ledger, backend)

    with pytest.raises(RuntimeError, match="helix unavailable"):
        materializer.flush_pending()

    pending = ledger.pending_materializations()
    assert len(pending) == 1
    assert pending[0]["attempts"] == 1
    result = materializer.flush_pending()
    assert result.applied == 1
    assert len(backend.calls) == 2
    assert backend.calls[0][2] == backend.calls[1][2]


def test_force_reconcile_replays_applied_absolute_state(tmp_path):
    ledger = WorkLedger(tmp_path / "work.sqlite3")
    _seed_analysis(ledger, "one", ["target"])
    backend = FakeBackend()
    materializer = AffinityMaterializer(ledger, backend)
    materializer.flush_pending()
    backend.pairs.clear()

    assert ledger.force_reconcile_materializations() == 1
    materializer.flush_pending()

    assert ("source", "target") in backend.pairs


def test_per_dimension_and_distinct_neighbor_caps(tmp_path):
    ledger = WorkLedger(tmp_path / "work.sqlite3")
    _seed_analysis(ledger, "many", [f"target-{i:03d}" for i in range(150)])

    pending = ledger.pending_materializations(limit=1000)

    assert len(pending) == 32


def test_installed_helix_sdk_can_encode_replace_request():
    probe = HelixAffinityBackend().offline_capability_probe()
    assert probe == {
        "drop_edge_labeled": True,
        "add_edge": True,
        "edge_properties": True,
    }


@pytest.mark.skipif(
    os.environ.get("HELIX_LIVE_TEST") != "1",
    reason="set HELIX_LIVE_TEST=1 with a disposable Helix graph",
)
def test_live_helix_pair_create_replace_delete():
    from helixdb import Client, Projection, g, read_batch

    url = os.environ.get("HELIX_URL", "http://127.0.0.1:6969")
    client = Client(url)
    functions = client.query().dynamic(
        read_batch()
        .var_as(
            "functions",
            g().n_with_label("FunctionIdentity").limit(2).project(
                [Projection.property("node_id")]
            ),
        )
        .returning(["functions"])
        .to_dynamic_request()
    ).send()["functions"]["properties"]
    if len(functions) < 2:
        pytest.skip("live graph needs two FunctionIdentity nodes")
    source, target = (row["node_id"] for row in functions)
    backend = HelixAffinityBackend(url)
    payload = {
        "schema": 1,
        "analyzer_version": "live-test",
        "global": 0.2,
        "domains": {},
        "change_kinds": {"bug_fix": 0.4},
        "max_score": 0.4,
        "episode_count": 2,
        "evidence_total": 1.0,
    }
    updated = {**payload, "global": 0.7, "max_score": 0.7}
    try:
        backend.replace_pair(source, target, payload)
        backend.replace_pair(source, target, updated)
        edges = client.query().dynamic(
            read_batch()
            .var_as("edges", g().e_with_label("WORK_AFFINITY").edge_properties())
            .returning(["edges"])
            .to_dynamic_request()
        ).send()["edges"]["properties"]
        matching = [
            row for row in edges if row.get("analyzer_version") == "live-test"
        ]
        assert len(matching) == 1
        assert matching[0]["max_score"] == pytest.approx(0.7)
    finally:
        backend.replace_pair(source, target, None)
