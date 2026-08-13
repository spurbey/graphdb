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
from typing import Any, Callable, Iterable, Mapping


ANALYZER_VERSION = "work-affinity-v2"
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


class PermanentWorkError(RuntimeError):
    """Invalid input that must be quarantined rather than retried."""


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

                CREATE TABLE IF NOT EXISTS work_analysis (
                    job_id TEXT PRIMARY KEY REFERENCES work_jobs(job_id),
                    analyzer_version TEXT NOT NULL,
                    transcript_hash TEXT NOT NULL,
                    changed_files_json TEXT NOT NULL,
                    changed_functions_json TEXT NOT NULL,
                    stats_json TEXT NOT NULL,
                    affinity_skipped_reason TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS work_events (
                    job_id TEXT NOT NULL REFERENCES work_jobs(job_id),
                    event_index INTEGER NOT NULL,
                    event_kind TEXT NOT NULL,
                    tool TEXT,
                    file_path TEXT,
                    line_start INTEGER,
                    line_end INTEGER,
                    function_id TEXT,
                    resolution TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    PRIMARY KEY (job_id, event_index)
                );

                CREATE TABLE IF NOT EXISTS affinity_episode_evidence (
                    job_id TEXT NOT NULL REFERENCES work_jobs(job_id),
                    analyzer_version TEXT NOT NULL,
                    source_function_id TEXT NOT NULL,
                    target_function_id TEXT NOT NULL,
                    dimension_type TEXT NOT NULL,
                    dimension_value TEXT NOT NULL,
                    role TEXT NOT NULL,
                    evidence REAL NOT NULL CHECK (evidence >= 0.0),
                    PRIMARY KEY (
                        job_id, source_function_id, target_function_id,
                        dimension_type, dimension_value
                    )
                );
                CREATE INDEX IF NOT EXISTS idx_affinity_episode_source_dimension
                    ON affinity_episode_evidence(
                        analyzer_version, source_function_id,
                        dimension_type, dimension_value
                    );

                CREATE TABLE IF NOT EXISTS affinity_aggregates (
                    analyzer_version TEXT NOT NULL,
                    source_function_id TEXT NOT NULL,
                    target_function_id TEXT NOT NULL,
                    dimension_type TEXT NOT NULL,
                    dimension_value TEXT NOT NULL,
                    evidence_total REAL NOT NULL,
                    episode_count INTEGER NOT NULL,
                    weight REAL NOT NULL CHECK (weight >= 0.0 AND weight <= 1.0),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (
                        analyzer_version, source_function_id, target_function_id,
                        dimension_type, dimension_value
                    )
                );
                CREATE INDEX IF NOT EXISTS idx_affinity_aggregate_source_dimension
                    ON affinity_aggregates(
                        analyzer_version, source_function_id,
                        dimension_type, dimension_value, weight DESC
                    );

                CREATE TABLE IF NOT EXISTS unknown_work_domains (
                    job_id TEXT NOT NULL REFERENCES work_jobs(job_id),
                    domain TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (job_id, domain)
                );

                CREATE TABLE IF NOT EXISTS affinity_materializations (
                    analyzer_version TEXT NOT NULL,
                    source_function_id TEXT NOT NULL,
                    target_function_id TEXT NOT NULL,
                    desired_payload_json TEXT,
                    desired_hash TEXT,
                    applied_hash TEXT,
                    status TEXT NOT NULL CHECK (status IN ('pending','applied')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    updated_at TEXT NOT NULL,
                    applied_at TEXT,
                    PRIMARY KEY (
                        analyzer_version, source_function_id, target_function_id
                    )
                );
                CREATE INDEX IF NOT EXISTS idx_affinity_materialization_pending
                    ON affinity_materializations(analyzer_version, status, updated_at);
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

    def record_analysis(
        self,
        job: Job,
        owner: str,
        *,
        transcript_hash: str,
        changed_files: Iterable[str],
        changed_functions: Iterable[str],
        stats: Mapping[str, Any],
        events: Iterable[Mapping[str, Any]],
        evidence: Iterable[Mapping[str, Any]],
        unknown_domains: Iterable[str] = (),
        affinity_skipped_reason: str | None = None,
        normalization_prior: float = 3.0,
    ) -> bool:
        """Persist one deterministic analysis and rebuild affected aggregates.

        Returns ``False`` when this exact job was already analyzed.  That makes
        retries safe: later Helix materialization can resume from the existing
        absolute SQLite state without adding the episode twice.
        """

        if normalization_prior <= 0:
            raise ValueError("normalization_prior must be positive")
        event_rows = [dict(row) for row in events]
        evidence_rows = [dict(row) for row in evidence]
        now = utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            lease = conn.execute(
                "SELECT status, lease_owner FROM work_jobs WHERE job_id=?", (job.job_id,)
            ).fetchone()
            self._check_owner(lease, job.job_id, owner)
            if conn.execute(
                "SELECT 1 FROM work_analysis WHERE job_id=?", (job.job_id,)
            ).fetchone():
                conn.commit()
                return False

            conn.execute(
                """
                INSERT INTO work_analysis (
                    job_id, analyzer_version, transcript_hash, changed_files_json,
                    changed_functions_json, stats_json, affinity_skipped_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.analyzer_version,
                    transcript_hash,
                    _canonical_json(sorted(set(changed_files))),
                    _canonical_json(sorted(set(changed_functions))),
                    _canonical_json(dict(stats)),
                    affinity_skipped_reason,
                    now,
                ),
            )

            for index, event in enumerate(event_rows):
                detail = dict(event.get("detail") or {})
                conn.execute(
                    """
                    INSERT INTO work_events (
                        job_id, event_index, event_kind, tool, file_path,
                        line_start, line_end, function_id, resolution, detail_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job.job_id,
                        index,
                        event["event_kind"],
                        event.get("tool"),
                        event.get("file_path"),
                        event.get("line_start"),
                        event.get("line_end"),
                        event.get("function_id"),
                        event.get("resolution", "unresolved"),
                        _canonical_json(detail),
                    ),
                )

            affected: set[tuple[str, str, str]] = set()
            for row in evidence_rows:
                source = str(row["source_function_id"])
                dimension_type = str(row["dimension_type"])
                dimension_value = str(row["dimension_value"])
                affected.add((source, dimension_type, dimension_value))
                conn.execute(
                    """
                    INSERT INTO affinity_episode_evidence (
                        job_id, analyzer_version, source_function_id,
                        target_function_id, dimension_type, dimension_value,
                        role, evidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job.job_id,
                        job.analyzer_version,
                        source,
                        str(row["target_function_id"]),
                        dimension_type,
                        dimension_value,
                        str(row["role"]),
                        float(row["evidence"]),
                    ),
                )

            for domain in sorted(set(unknown_domains)):
                conn.execute(
                    "INSERT INTO unknown_work_domains(job_id, domain, created_at) VALUES (?, ?, ?)",
                    (job.job_id, domain, now),
                )

            for source, dimension_type, dimension_value in sorted(affected):
                grouped = conn.execute(
                    """
                    SELECT target_function_id, SUM(evidence) AS evidence_total,
                           COUNT(DISTINCT job_id) AS episode_count
                    FROM affinity_episode_evidence
                    WHERE analyzer_version=? AND source_function_id=?
                      AND dimension_type=? AND dimension_value=?
                    GROUP BY target_function_id
                    """,
                    (job.analyzer_version, source, dimension_type, dimension_value),
                ).fetchall()
                outgoing_total = sum(float(row["evidence_total"]) for row in grouped)
                conn.execute(
                    """
                    DELETE FROM affinity_aggregates
                    WHERE analyzer_version=? AND source_function_id=?
                      AND dimension_type=? AND dimension_value=?
                    """,
                    (job.analyzer_version, source, dimension_type, dimension_value),
                )
                denominator = normalization_prior + outgoing_total
                for row in grouped:
                    total = float(row["evidence_total"])
                    conn.execute(
                        """
                        INSERT INTO affinity_aggregates (
                            analyzer_version, source_function_id, target_function_id,
                            dimension_type, dimension_value, evidence_total,
                            episode_count, weight, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            job.analyzer_version,
                            source,
                            row["target_function_id"],
                            dimension_type,
                            dimension_value,
                            total,
                            int(row["episode_count"]),
                            total / denominator,
                            now,
                        ),
                    )
            self._stage_materializations(
                conn,
                job.analyzer_version,
                {source for source, _, _ in affected},
                now=now,
            )
            conn.commit()
        return True

    @staticmethod
    def _stage_materializations(
        conn: sqlite3.Connection,
        analyzer_version: str,
        sources: set[str],
        *,
        now: str,
        max_per_dimension: int = 32,
        max_distinct_neighbors: int = 128,
    ) -> None:
        """Build compact desired pair payloads from canonical aggregates."""

        for source in sorted(sources):
            rows = conn.execute(
                """
                SELECT target_function_id, dimension_type, dimension_value,
                       evidence_total, episode_count, weight
                FROM affinity_aggregates
                WHERE analyzer_version=? AND source_function_id=?
                ORDER BY dimension_type, dimension_value, weight DESC,
                         target_function_id
                """,
                (analyzer_version, source),
            ).fetchall()
            per_dimension: dict[tuple[str, str], list[sqlite3.Row]] = {}
            for row in rows:
                per_dimension.setdefault(
                    (row["dimension_type"], row["dimension_value"]), []
                ).append(row)

            selected: dict[str, list[sqlite3.Row]] = {}
            for dimension_rows in per_dimension.values():
                for row in dimension_rows[:max_per_dimension]:
                    selected.setdefault(row["target_function_id"], []).append(row)

            if len(selected) > max_distinct_neighbors:
                ordered_targets = sorted(
                    selected,
                    key=lambda target: (
                        -max(float(row["weight"]) for row in selected[target]),
                        target,
                    ),
                )[:max_distinct_neighbors]
                selected = {target: selected[target] for target in ordered_targets}

            desired: dict[str, tuple[str, str]] = {}
            for target, target_rows in selected.items():
                payload: dict[str, Any] = {
                    "schema": 1,
                    "analyzer_version": analyzer_version,
                    "global": None,
                    "domains": {},
                    "change_kinds": {},
                    "max_score": 0.0,
                    "episode_count": 0,
                    "evidence_total": 0.0,
                }
                for row in target_rows:
                    weight = float(row["weight"])
                    payload["max_score"] = max(payload["max_score"], weight)
                    payload["episode_count"] = max(
                        payload["episode_count"], int(row["episode_count"])
                    )
                    payload["evidence_total"] += float(row["evidence_total"])
                    if row["dimension_type"] == "global":
                        payload["global"] = weight
                    elif row["dimension_type"] == "domain":
                        payload["domains"][row["dimension_value"]] = weight
                    elif row["dimension_type"] == "change_kind":
                        payload["change_kinds"][row["dimension_value"]] = weight
                payload_json = _canonical_json(payload)
                payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
                desired[target] = (payload_json, payload_hash)

            existing = {
                row["target_function_id"]: row
                for row in conn.execute(
                    """
                    SELECT target_function_id, desired_hash, applied_hash
                    FROM affinity_materializations
                    WHERE analyzer_version=? AND source_function_id=?
                    """,
                    (analyzer_version, source),
                ).fetchall()
            }
            all_targets = sorted(set(existing) | set(desired))
            for target in all_targets:
                payload_json, desired_hash = desired.get(target, (None, None))
                old = existing.get(target)
                applied_hash = old["applied_hash"] if old else None
                status = "applied" if desired_hash == applied_hash else "pending"
                if desired_hash is None and applied_hash is None:
                    if old:
                        conn.execute(
                            """
                            DELETE FROM affinity_materializations
                            WHERE analyzer_version=? AND source_function_id=?
                              AND target_function_id=?
                            """,
                            (analyzer_version, source, target),
                        )
                    continue
                conn.execute(
                    """
                    INSERT INTO affinity_materializations (
                        analyzer_version, source_function_id, target_function_id,
                        desired_payload_json, desired_hash, applied_hash, status,
                        attempts, last_error, updated_at, applied_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, NULL)
                    ON CONFLICT(analyzer_version, source_function_id, target_function_id)
                    DO UPDATE SET
                        desired_payload_json=excluded.desired_payload_json,
                        desired_hash=excluded.desired_hash,
                        status=excluded.status,
                        attempts=CASE
                            WHEN affinity_materializations.desired_hash IS excluded.desired_hash
                            THEN affinity_materializations.attempts ELSE 0 END,
                        last_error=CASE
                            WHEN affinity_materializations.desired_hash IS excluded.desired_hash
                            THEN affinity_materializations.last_error ELSE NULL END,
                        updated_at=excluded.updated_at
                    """,
                    (
                        analyzer_version,
                        source,
                        target,
                        payload_json,
                        desired_hash,
                        applied_hash,
                        status,
                        now,
                    ),
                )

    def affinity_neighbors(
        self,
        source_function_id: str,
        dimension_type: str,
        dimension_value: str,
        *,
        analyzer_version: str = ANALYZER_VERSION,
        limit: int = 32,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT target_function_id, evidence_total, episode_count, weight
                FROM affinity_aggregates
                WHERE analyzer_version=? AND source_function_id=?
                  AND dimension_type=? AND dimension_value=?
                ORDER BY weight DESC, target_function_id
                LIMIT ?
                """,
                (
                    analyzer_version,
                    source_function_id,
                    dimension_type,
                    dimension_value,
                    limit,
                ),
            ).fetchall()
        return [dict(row) for row in rows]

    def pending_materializations(
        self,
        *,
        analyzer_version: str = ANALYZER_VERSION,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT analyzer_version, source_function_id, target_function_id,
                       desired_payload_json, desired_hash, applied_hash,
                       attempts, last_error
                FROM affinity_materializations
                WHERE analyzer_version=? AND status='pending'
                ORDER BY updated_at, source_function_id, target_function_id
                LIMIT ?
                """,
                (analyzer_version, limit),
            ).fetchall()
        return [
            {
                **dict(row),
                "desired_payload": json.loads(row["desired_payload_json"])
                if row["desired_payload_json"]
                else None,
            }
            for row in rows
        ]

    def mark_materialized(
        self,
        analyzer_version: str,
        source_function_id: str,
        target_function_id: str,
        desired_hash: str | None,
    ) -> None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT desired_hash FROM affinity_materializations
                WHERE analyzer_version=? AND source_function_id=?
                  AND target_function_id=?
                """,
                (analyzer_version, source_function_id, target_function_id),
            ).fetchone()
            if row is None:
                raise KeyError("unknown materialization row")
            if row["desired_hash"] != desired_hash:
                raise RuntimeError("desired affinity changed during materialization")
            if desired_hash is None:
                conn.execute(
                    """
                    DELETE FROM affinity_materializations
                    WHERE analyzer_version=? AND source_function_id=?
                      AND target_function_id=?
                    """,
                    (analyzer_version, source_function_id, target_function_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE affinity_materializations
                    SET applied_hash=desired_hash, status='applied', attempts=attempts+1,
                        last_error=NULL, updated_at=?, applied_at=?
                    WHERE analyzer_version=? AND source_function_id=?
                      AND target_function_id=?
                    """,
                    (
                        utc_now(),
                        utc_now(),
                        analyzer_version,
                        source_function_id,
                        target_function_id,
                    ),
                )

    def mark_materialization_failed(
        self,
        analyzer_version: str,
        source_function_id: str,
        target_function_id: str,
        error: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE affinity_materializations
                SET status='pending', attempts=attempts+1, last_error=?, updated_at=?
                WHERE analyzer_version=? AND source_function_id=?
                  AND target_function_id=?
                """,
                (
                    str(error)[:4000],
                    utc_now(),
                    analyzer_version,
                    source_function_id,
                    target_function_id,
                ),
            )

    def force_reconcile_materializations(
        self, *, analyzer_version: str = ANALYZER_VERSION
    ) -> int:
        """Requeue all desired pair states for absolute Helix repair."""

        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE affinity_materializations
                SET status='pending', applied_hash=NULL, last_error=NULL, updated_at=?
                WHERE analyzer_version=?
                """,
                (utc_now(), analyzer_version),
            )
            return int(cursor.rowcount)

    def analysis_details(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            analysis = conn.execute(
                "SELECT * FROM work_analysis WHERE job_id=?", (job_id,)
            ).fetchone()
            if analysis is None:
                return None
            events = conn.execute(
                "SELECT * FROM work_events WHERE job_id=? ORDER BY event_index", (job_id,)
            ).fetchall()
            evidence = conn.execute(
                """
                SELECT source_function_id, target_function_id, dimension_type,
                       dimension_value, role, evidence
                FROM affinity_episode_evidence
                WHERE job_id=?
                ORDER BY source_function_id, target_function_id,
                         dimension_type, dimension_value
                """,
                (job_id,),
            ).fetchall()
            unknown = conn.execute(
                "SELECT domain FROM unknown_work_domains WHERE job_id=? ORDER BY domain",
                (job_id,),
            ).fetchall()
        return {
            "transcript_hash": analysis["transcript_hash"],
            "changed_files": json.loads(analysis["changed_files_json"]),
            "changed_functions": json.loads(analysis["changed_functions_json"]),
            "stats": json.loads(analysis["stats_json"]),
            "affinity_skipped_reason": analysis["affinity_skipped_reason"],
            "events": [
                {
                    **dict(row),
                    "detail": json.loads(row["detail_json"]),
                }
                for row in events
            ],
            "evidence": [dict(row) for row in evidence],
            "unknown_domains": [row["domain"] for row in unknown],
        }


class BackgroundWorker:
    """One-job worker boundary; processing is injected by later blocks."""

    def __init__(self, ledger: WorkLedger, *, owner: str | None = None):
        self.ledger = ledger
        self.owner = owner or f"worker-{uuid.uuid4().hex[:12]}"

    def run_once(
        self,
        processor: Callable[[Job], Mapping[str, Any] | None],
        *,
        lease_seconds: int = 300,
    ) -> Job | None:
        job = self.ledger.claim(self.owner, lease_seconds=lease_seconds)
        if job is None:
            return None
        try:
            result = processor(job)
            self.ledger.mark_applied(job.job_id, self.owner, result)
        except PermanentWorkError as exc:
            self.ledger.quarantine(job.job_id, self.owner, str(exc))
            return self.ledger.get(job.job_id)
        except Exception as exc:
            self.ledger.mark_retry(job.job_id, self.owner, repr(exc))
            raise
        return self.ledger.get(job.job_id)
