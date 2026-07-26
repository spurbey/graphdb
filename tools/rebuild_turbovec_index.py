from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import turbovec_adapter


def _node_id_candidates(node_id: str) -> list[str]:
    """Support old Dograh Helix rows that were inserted without repo prefix."""
    candidates = [node_id]
    if ":" in node_id:
        candidates.append(node_id.split(":", 1)[1])
    return list(dict.fromkeys(candidates))


def _ensure_helix_vector_id_indexes(kind: str) -> None:
    from helixdb import Client, IndexSpec, g, write_batch

    client = Client("http://127.0.0.1:6969")
    batch = write_batch()
    if kind == "code":
        batch = batch.var_as(
            "idx_code_vector_id",
            g().create_index_if_not_exists(IndexSpec.node_equality("FunctionIdentity", "code_vector_id")),
        )
    elif kind == "memory":
        batch = batch.var_as(
            "idx_memory_vector_id",
            g().create_index_if_not_exists(IndexSpec.node_equality("FunctionState", "memory_vector_id")),
        )
    else:
        return
    client.query().dynamic(batch.returning(["idx_code_vector_id" if kind == "code" else "idx_memory_vector_id"]).to_dynamic_request()).send()


def _stamp_helix_vector_ids(kind: str, vector_ids: dict[str, int], batch_size: int = 100) -> dict:
    from helixdb import Client, Predicate, PropertyInput, g, write_batch

    client = Client("http://127.0.0.1:6969")
    label = "FunctionIdentity" if kind == "code" else "FunctionState"
    property_name = "code_vector_id" if kind == "code" else "memory_vector_id"
    source_property_name = "code_vector_source_node_id" if kind == "code" else "memory_vector_source_node_id"

    attempts = 0
    batches = 0
    errors = 0
    items = list(vector_ids.items())
    for offset in range(0, len(items), batch_size):
        batch = write_batch()
        names = []
        for i, (source_node_id, vector_id) in enumerate(items[offset:offset + batch_size]):
            for j, candidate in enumerate(_node_id_candidates(source_node_id)):
                name = f"n{i}_{j}"
                batch = batch.var_as(
                    name,
                    g()
                    .n_with_label(label)
                    .where(Predicate.eq("node_id", candidate))
                    .set_property(property_name, PropertyInput.value(str(vector_id)))
                    .set_property(source_property_name, PropertyInput.value(source_node_id)),
                )
                names.append(name)
                attempts += 1
        try:
            client.query().dynamic(batch.returning(names).to_dynamic_request()).send()
            batches += 1
        except Exception:
            errors += 1
    return {"helix_update_attempts": attempts, "helix_update_batches": batches, "helix_update_errors": errors}


def rebuild(kind: str, source: Path, stamp_helix: bool = True) -> dict:
    if kind == "code":
        index = turbovec_adapter.code_index
    elif kind == "memory":
        index = turbovec_adapter.memory_index
    else:
        raise ValueError(f"unsupported kind: {kind}")

    raw = json.loads(source.read_text(encoding="utf-8"))
    inserted = 0
    vector_ids: dict[str, int] = {}
    for node_id, vector in raw.items():
        if isinstance(vector, list) and len(vector) == index.dim:
            vector_ids[node_id] = index.insert(node_id, vector)
            inserted += 1
    index.save()
    helix_result = {}
    if stamp_helix:
        _ensure_helix_vector_id_indexes(kind)
        helix_result = _stamp_helix_vector_ids(kind, vector_ids)
    return {
        "kind": kind,
        "source": str(source),
        "inserted": inserted,
        "index_path": str(index.index_path),
        "manifest_path": str(index.manifest_path),
        "bit_width": index.bit_width,
        "dim": index.dim,
        **helix_result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild a TurboVec 4-bit index from a legacy JSON vector cache.")
    parser.add_argument("--kind", choices=["code", "memory"], default="code")
    parser.add_argument("--source", type=Path, default=None)
    parser.add_argument("--no-helix-stamp", action="store_true", help="Only rebuild TurboVec; do not update Helix vector_id properties.")
    args = parser.parse_args()

    source = args.source or (ROOT / f".turbovec_{args.kind}.json")
    result = rebuild(args.kind, source, stamp_helix=not args.no_helix_stamp)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
