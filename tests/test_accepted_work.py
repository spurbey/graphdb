from __future__ import annotations

import sqlite3

import pytest

from accepted_work import AcceptedWork, BackgroundWorker, WorkLedger


def _payload() -> dict:
    return {
        "repo_id": "dograh",
        "base_revision": "abc123",
        "head_revision": "def456",
        "accepted": True,
        "summary": "Fix login reconnect after WebSocket inactivity",
        "change_kind": "bug_fix",
        "domains": ["auth.login", "session.websocket"],
        "transcript_ref": "opencode://session/ses_123",
        "decisions": ["Preserve the existing authenticated session"],
    }


def test_contract_normalizes_and_ignores_unknown_agent_fields():
    raw = _payload()
    raw["domains"] = ["AUTH.LOGIN", "auth.login"]
    raw["agent_notes"] = {"not": "a scoring field"}

    work = AcceptedWork.from_mapping(raw)

    assert work.domains == ("auth.login",)
    assert work.change_kind == "bug_fix"
    assert "agent_notes" not in work.to_dict()


@pytest.mark.parametrize(
    "field,value",
    [
        ("accepted", False),
        ("change_kind", "unknown"),
        ("summary", ""),
        ("domains", ["bad domain"]),
    ],
)
def test_contract_rejects_invalid_values(field, value):
    raw = _payload()
    raw[field] = value
    with pytest.raises(ValueError):
        AcceptedWork.from_mapping(raw)


def test_job_id_is_stable_and_changes_with_analyzer_version():
    work = AcceptedWork.from_mapping(_payload())
    assert work.job_id() == work.job_id()
    assert work.job_id("v1") != work.job_id("v2")


def test_enqueue_is_idempotent_and_persists_episode(tmp_path):
    ledger = WorkLedger(tmp_path / "work.sqlite3")
    work = AcceptedWork.from_mapping(_payload())

    first_id, inserted = ledger.enqueue(work)
    second_id, inserted_again = ledger.enqueue(work)

    assert first_id == second_id
    assert inserted is True
    assert inserted_again is False
    job = ledger.get(first_id)
    assert job is not None
    assert job.status == "queued"
    with sqlite3.connect(tmp_path / "work.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM work_episodes").fetchone()[0] == 1


def test_worker_claims_and_marks_applied(tmp_path):
    ledger = WorkLedger(tmp_path / "work.sqlite3")
    job_id, _ = ledger.enqueue(_payload())
    worker = BackgroundWorker(ledger, owner="test-worker")

    result = worker.run_once(lambda job: {"repo": job.work.repo_id, "changed": 2})

    assert result is not None
    assert result.job_id == job_id
    assert result.status == "applied"
    assert result.result == {"repo": "dograh", "changed": 2}


def test_worker_failure_is_retryable_and_owner_is_enforced(tmp_path):
    ledger = WorkLedger(tmp_path / "work.sqlite3")
    job_id, _ = ledger.enqueue(_payload())
    worker = BackgroundWorker(ledger, owner="test-worker")

    with pytest.raises(RuntimeError, match="boom"):
        worker.run_once(lambda _: (_ for _ in ()).throw(RuntimeError("boom")))

    job = ledger.get(job_id)
    assert job is not None
    assert job.status == "retry_wait"
    assert job.attempts == 1

    with pytest.raises(RuntimeError, match="not leased"):
        ledger.mark_applied(job_id, "wrong-owner")
