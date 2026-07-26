from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import turbovec_adapter


def rebuild(kind: str, source: Path) -> dict:
    if kind == "code":
        index = turbovec_adapter.code_index
    elif kind == "memory":
        index = turbovec_adapter.memory_index
    else:
        raise ValueError(f"unsupported kind: {kind}")

    raw = json.loads(source.read_text(encoding="utf-8"))
    inserted = 0
    for node_id, vector in raw.items():
        if isinstance(vector, list) and len(vector) == index.dim:
            index.insert(node_id, vector)
            inserted += 1
    index.save()
    return {
        "kind": kind,
        "source": str(source),
        "inserted": inserted,
        "index_path": str(index.index_path),
        "registry_path": str(index.registry_path),
        "bit_width": index.bit_width,
        "dim": index.dim,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild a TurboVec 4-bit index from a legacy JSON vector cache.")
    parser.add_argument("--kind", choices=["code", "memory"], default="code")
    parser.add_argument("--source", type=Path, default=None)
    args = parser.parse_args()

    source = args.source or (ROOT / f".turbovec_{args.kind}.json")
    result = rebuild(args.kind, source)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
