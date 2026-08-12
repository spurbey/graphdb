"""Materialize compact WORK_AFFINITY pair state into HelixDB."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from accepted_work import ANALYZER_VERSION, WorkLedger


EDGE_LABEL = "WORK_AFFINITY"


class AffinityBackend(Protocol):
    def replace_pair(
        self, source_function_id: str, target_function_id: str, payload: dict[str, Any] | None
    ) -> None: ...


@dataclass(frozen=True)
class MaterializationResult:
    applied: int
    deleted: int
    failed: int
    remaining: int


class AffinityMaterializer:
    def __init__(self, ledger: WorkLedger, backend: AffinityBackend):
        self.ledger = ledger
        self.backend = backend

    def flush_pending(
        self,
        *,
        analyzer_version: str = ANALYZER_VERSION,
        limit: int = 100,
        stop_on_error: bool = True,
    ) -> MaterializationResult:
        applied = deleted = failed = 0
        for row in self.ledger.pending_materializations(
            analyzer_version=analyzer_version, limit=limit
        ):
            try:
                self.backend.replace_pair(
                    row["source_function_id"],
                    row["target_function_id"],
                    row["desired_payload"],
                )
                self.ledger.mark_materialized(
                    analyzer_version,
                    row["source_function_id"],
                    row["target_function_id"],
                    row["desired_hash"],
                )
                if row["desired_payload"] is None:
                    deleted += 1
                else:
                    applied += 1
            except Exception as exc:
                failed += 1
                self.ledger.mark_materialization_failed(
                    analyzer_version,
                    row["source_function_id"],
                    row["target_function_id"],
                    repr(exc),
                )
                if stop_on_error:
                    raise
        remaining = len(
            self.ledger.pending_materializations(
                analyzer_version=analyzer_version, limit=1_000_000
            )
        )
        return MaterializationResult(applied, deleted, failed, remaining)


class HelixAffinityBackend:
    """Absolute pair replacement using label-specific delete then add.

    SQLite is canonical, so a process failure between delete and add is repaired
    by replaying the same pending row.  This avoids depending on unproven
    in-place edge-property mutation behavior.
    """

    def __init__(self, url: str = "http://127.0.0.1:6969"):
        from helixdb import Client

        self.url = url
        self.client = Client(url)

    @staticmethod
    def _properties(payload: dict[str, Any]) -> dict[str, Any]:
        from helixdb import PropertyInput, PropertyValue

        compact = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return {
            "schema": PropertyInput.value(PropertyValue.i64(int(payload["schema"]))),
            "analyzer_version": PropertyInput.value(str(payload["analyzer_version"])),
            "payload_json": PropertyInput.value(compact),
            "max_score": PropertyInput.value(PropertyValue.f64(float(payload["max_score"]))),
            "episode_count": PropertyInput.value(
                PropertyValue.i64(int(payload["episode_count"]))
            ),
            "evidence_total": PropertyInput.value(
                PropertyValue.f64(float(payload["evidence_total"]))
            ),
        }

    def _pair_batch(
        self,
        source_function_id: str,
        target_function_id: str,
        *,
        add_payload: dict[str, Any] | None,
    ):
        from helixdb import NodeRef, Predicate, define_params, g, param, write_batch

        params = define_params(
            {"source_id": param.string(), "target_id": param.string()}
        )
        values = {
            "source_id": source_function_id,
            "target_id": target_function_id,
        }
        batch = (
            write_batch()
            .var_as(
                "source",
                g().n_with_label("FunctionIdentity").where(
                    Predicate.eq_param("node_id", "source_id")
                ),
            )
            .var_as(
                "target",
                g().n_with_label("FunctionIdentity").where(
                    Predicate.eq_param("node_id", "target_id")
                ),
            )
            .var_as(
                "removed",
                g().n(NodeRef.var("source")).drop_edge_labeled(
                    NodeRef.var("target"), EDGE_LABEL
                ),
            )
        )
        returns = ["source", "target", "removed"]
        if add_payload is not None:
            batch = batch.var_as(
                "edge",
                g().n(NodeRef.var("source")).add_e(
                    EDGE_LABEL,
                    NodeRef.var("target"),
                    self._properties(add_payload),
                ),
            )
            returns.append("edge")
        return batch.returning(returns).to_dynamic_request(params, values)

    def replace_pair(
        self, source_function_id: str, target_function_id: str, payload: dict[str, Any] | None
    ) -> None:
        request = self._pair_batch(
            source_function_id, target_function_id, add_payload=payload
        )
        response = self.client.query().dynamic(request).send()
        if not response.get("source"):
            raise RuntimeError(f"missing source FunctionIdentity: {source_function_id}")
        if not response.get("target"):
            raise RuntimeError(f"missing target FunctionIdentity: {target_function_id}")

    def offline_capability_probe(self) -> dict[str, bool]:
        payload = {
            "schema": 1,
            "analyzer_version": ANALYZER_VERSION,
            "global": 0.2,
            "domains": {"probe": 0.3},
            "change_kinds": {"bug_fix": 0.4},
            "max_score": 0.4,
            "episode_count": 2,
            "evidence_total": 1.0,
        }
        encoded = self._pair_batch("probe-source", "probe-target", add_payload=payload).to_json()
        text = repr(encoded)
        return {
            "drop_edge_labeled": "DropEdgeLabeled" in text,
            "add_edge": "AddE" in text,
            "edge_properties": "payload_json" in text and "max_score" in text,
        }
