"""Lean accepted-work contract and durable background-job ledger.

This module is intentionally independent from HelixDB and the existing
experimental ingest/query code.  It provides the durable boundary used by the
post-accepted-work worker; later blocks can plug Git/transcript processing and
Helix materialization into ``BackgroundWorker`` without changing the queue
contract.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


ANALYZER_VERSION = "work-affinity-v1"
CHANGE_KINDS = frozenset(
    {"bug_fix", "feature", "refactor", "optimization", "architecture"}
)
_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class AcceptedWork:
    """The only semantic input required from a coding-agent completion."""

    repo_id: str
    base_revision: str
    head_revision: str
    summary: str
    change_kind: str
    domains: tuple[str, ...] = ()
    transcript_ref: str | None = None
    decisions: tuple[str, ...] = ()
    accepted: bool = True

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "AcceptedWork":
        if not isinstance(raw, Mapping):
            raise ValueError("accepted work must be a JSON object")

        def required_text(name: str, max_len: int = 512) -> str:
            value = raw.get(name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            value = value.strip()
            if len(value) > max_len:
                raise ValueError(f"{name} exceeds {max_len} characters")
            return value

        accepted = raw.get("accepted", True)
        if accepted is not True:
            raise ValueError("only accepted work can be enqueued")

        change_kind = required_text("change_kind", 32).lower()
        if change_kind not in CHANGE_KINDS:
            allowed = ", ".join(sorted(CHANGE_KINDS))
            raise ValueError(f"change_kind must be one of: {allowed}")

        domains_raw = raw.get("domains", [])
        if domains_raw is None:
            domains_raw = []
        if not isinstance(domains_raw, (list, tuple)):
            raise ValueError("domains must be an array of strings")
        domains: list[str] = []
        for domain in domains_raw:
            if not isinstance(domain, str):
                raise ValueError("domains must contain only strings")
            domain = domain.strip().lower()
            if not _DOMAIN_RE.fullmatch(domain):
                raise ValueError(f"invalid domain: {domain!r}")
            if domain not in domains:
                domains.append(domain)
        if len(domains) > 8:
            raise ValueError("at most 8 domains may be attached to one episode")

        decisions_raw = raw.get("decisions", [])
        if decisions_raw is None:
            decisions_raw = []
        if not isinstance(decisions_raw, (list, tuple)):
            raise ValueError("decisions must be an array of strings")
        decisions: list[str] = []
        for decision in decisions_raw:
            if not isinstance(decision, str) or not decision.strip():
                raise ValueError("decisions must contain non-empty strings")
            decision = decision.strip()
            if len(decision) > 2000:
                raise ValueError("decision exceeds 2000 characters")
            decisions.append(decision)
        if len(decisions) > 16:
            raise ValueError("at most 16 decisions may be attached to one episode")

        transcript_ref = raw.get("transcript_ref")
        if transcript_ref is not None:
            if not isinstance(transcript_ref, str) or not transcript_ref.strip():
                raise ValueError("transcript_ref must be a non-empty string when supplied")
            transcript_ref = transcript_ref.strip()

        return cls(
            repo_id=required_text("repo_id"),
            base_revision=required_text("base_revision"),
            head_revision=required_text("head_revision"),
            summary=required_text("summary", 4000),
            change_kind=change_kind,
            domains=tuple(domains),
            transcript_ref=transcript_ref,
            decisions=tuple(decisions),
            accepted=True,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "base_revision": self.base_revision,
            "head_revision": self.head_revision,
            "accepted": self.accepted,
            "summary": self.summary,
            "change_kind": self.change_kind,
            "domains": list(self.domains),
            "transcript_ref": self.transcript_ref,
            "decisions": list(self.decisions),
        }

    def digest(self) -> str:
        return hashlib.sha256(_canonical_json(self.to_dict()).encode("utf-8")).hexdigest()

    def job_id(self, analyzer_version: str = ANALYZER_VERSION) -> str:
        identity = {
            "analyzer_version": analyzer_version,
            "input_digest": self.digest(),
            "repo_id": self.repo_id,
            "base_revision": self.base_revision,
            "head_revision": self.head_revision,
            "transcript_ref": self.transcript_ref,
        }
        return hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class Job:
    job_id: str
    work: AcceptedWork
    analyzer_version: str
    status: str
    attempts: int
    available_at: float
    lease_owner: str | None
    lease_until: float | None
    last_error: str | None
    result: dict[str, Any] | None


class WorkLedger:
    """SQLite queue and episode ledger.

    A new connection is used for each operation so the persistent worker and
    diagnostic commands can safely share the same SQLite file.  WAL mode keeps
    reads from blocking the worker's short write transactions.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS work_jobs (
                    job_id TEXT PRIMARY KEY,
                    repo_id TEXT NOT NULL,
                    base_revision TEXT NOT NULL,
                    head_revision TEXT NOT NULL,
                    analyzer_version TEXT NOT NULL,
                    input_digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN
                        ('queued','running','retry_wait','applied','quarantined')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    available_at REAL NOT NULL,
                    lease_owner TEXT,
                    lease_until REAL,
                    last_error TEXT,
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_work_jobs_ready
                    ON work_jobs(status, available_at, created_at);

                CREATE TABLE IF NOT EXISTS work_episodes (
                    job_id TEXT PRIMARY KEY REFERENCES work_jobs(job_id),
                    repo_id TEXT NOT NULL,
                    base_revision TEXT NOT NULL,
                    head_revision TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    change_kind TEXT NOT NULL,
                    domains_json TEXT NOT NULL,
                    transcript_ref TEXT,
                    decisions_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def enqueue(
        self,
        work: AcceptedWork | Mapping[str, Any],
        *,
        analyzer_version: str = ANALYZER_VERSION,
    ) -> tuple[str, bool]:
        if not isinstance(work, AcceptedWork):
            work = AcceptedWork.from_mapping(work)
        job_id = work.job_id(analyzer_version)
        now = utc_now()
        payload = _canonical_json(work.to_dict())
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT job_id FROM work_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if existing:
                return job_id, False
            conn.execute(
                """
                INSERT INTO work_jobs (
                    job_id, repo_id, base_revision, head_revision, analyzer_version,
                    input_digest, payload_json, status, available_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (
                    job_id,
                    work.repo_id,
                    work.base_revision,
                    work.head_revision,
                    analyzer_version,
                    work.digest(),
                    payload,
                    time.time(),
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO work_episodes (
                    job_id, repo_id, base_revision, head_revision, summary,
                    change_kind, domains_json, transcript_ref, decisions_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    work.repo_id,
                    work.base_revision,
                    work.head_revision,
                    work.summary,
                    work.change_kind,
                    _canonical_json(list(work.domains)),
                    work.transcript_ref,
                    _canonical_json(list(work.decisions)),
                    now,
                ),
            )
        return job_id, True

    def _row_to_job(self, row: sqlite3.Row) -> Job:
        payload = json.loads(row["payload_json"])
        return Job(
            job_id=row["job_id"],
            work=AcceptedWork.from_mapping(payload),
            analyzer_version=row["analyzer_version"],
            status=row["status"],
            attempts=int(row["attempts"]),
            available_at=float(row["available_at"]),
            lease_owner=row["lease_owner"],
            lease_until=row["lease_until"],
            last_error=row["last_error"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
        )

    def get(self, job_id: str) -> Job | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM work_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._row_to_job(row) if row else None

    def claim(
        self,
        owner: str | None = None,
        *,
        lease_seconds: int = 300,
    ) -> Job | None:
        owner = owner or f"worker-{uuid.uuid4().hex[:12]}"
        now = time.time()
        lease_until = now + lease_seconds
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM work_jobs
                WHERE (status IN ('queued','retry_wait') AND available_at <= ?)
                   OR (status = 'running' AND lease_until < ?)
                ORDER BY created_at
                LIMIT 1
                """,
                (now, now),
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            conn.execute(
                """
                UPDATE work_jobs
                SET status='running', attempts=attempts+1, lease_owner=?,
                    lease_until=?, updated_at=?
                WHERE job_id=?
                """,
                (owner, lease_until, utc_now(), row["job_id"]),
            )
            conn.commit()
            claimed = conn.execute(
                "SELECT * FROM work_jobs WHERE job_id = ?", (row["job_id"],)
            ).fetchone()
        return self._row_to_job(claimed)

    def mark_applied(self, job_id: str, owner: str, result: Mapping[str, Any] | None = None) -> None:
        self._finish(job_id, owner, "applied", result=result)

    def mark_retry(
        self,
        job_id: str,
        owner: str,
        error: str,
        *,
        delay_seconds: float = 30.0,
        max_attempts: int = 5,
    ) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT attempts, status, lease_owner FROM work_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            self._check_owner(row, job_id, owner)
            status = "quarantined" if int(row["attempts"]) >= max_attempts else "retry_wait"
            conn.execute(
                """
                UPDATE work_jobs
                SET status=?, available_at=?, lease_owner=NULL, lease_until=NULL,
                    last_error=?, updated_at=?
                WHERE job_id=?
                """,
                (
                    status,
                    time.time() + max(0.0, delay_seconds),
                    str(error)[:4000],
                    utc_now(),
                    job_id,
                ),
            )

    def quarantine(self, job_id: str, owner: str, error: str) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status, lease_owner FROM work_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            self._check_owner(row, job_id, owner)
            conn.execute(
                """
                UPDATE work_jobs
                SET status='quarantined', lease_owner=NULL, lease_until=NULL,
                    last_error=?, updated_at=?
                WHERE job_id=?
                """,
                (str(error)[:4000], utc_now(), job_id),
            )

    def _finish(
        self,
        job_id: str,
        owner: str,
        status: str,
        *,
        result: Mapping[str, Any] | None,
    ) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status, lease_owner FROM work_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            self._check_owner(row, job_id, owner)
            conn.execute(
                """
                UPDATE work_jobs
                SET status=?, lease_owner=NULL, lease_until=NULL, result_json=?,
                    last_error=NULL, updated_at=?
                WHERE job_id=?
                """,
                (status, _canonical_json(dict(result)) if result is not None else None, utc_now(), job_id),
            )

    @staticmethod
    def _check_owner(row: sqlite3.Row | None, job_id: str, owner: str) -> None:
        if row is None:
            raise KeyError(f"unknown job: {job_id}")
        if row["status"] != "running" or row["lease_owner"] != owner:
            raise RuntimeError(f"job {job_id} is not leased by {owner}")

    def list_jobs(self, *, status: str | None = None, limit: int = 50) -> list[Job]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self._connect() as conn:
            if status is None:
                rows = conn.execute(
                    "SELECT * FROM work_jobs ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM work_jobs WHERE status=? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
        return [self._row_to_job(row) for row in rows]


class BackgroundWorker:
    """One-job worker boundary; processing is injected by later blocks."""

    def __init__(self, ledger: WorkLedger, *, owner: str | None = None):
        self.ledger = ledger
        self.owner = owner or f"worker-{uuid.uuid4().hex[:12]}"

    def run_once(
        self,
        processor: Callable[[AcceptedWork], Mapping[str, Any] | None],
        *,
        lease_seconds: int = 300,
    ) -> Job | None:
        job = self.ledger.claim(self.owner, lease_seconds=lease_seconds)
        if job is None:
            return None
        try:
            result = processor(job.work)
            self.ledger.mark_applied(job.job_id, self.owner, result)
        except Exception as exc:
            self.ledger.mark_retry(job.job_id, self.owner, repr(exc))
            raise
        return self.ledger.get(job.job_id)

