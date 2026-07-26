"""TurboVec-backed vector indexes for graphdb.

The serving path uses TurboVec's 4-bit IdMapIndex and stable uint64 vector IDs.
Legacy JSON vector caches are still accepted as a bootstrap source so existing
Dograh experiments can migrate without reingesting.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

import numpy as np

try:
    import turbovec

    _HAS_TURBOVEC = True
except ImportError:
    turbovec = None
    _HAS_TURBOVEC = False


ROOT = Path(__file__).resolve().parent
VECTOR_DIR = ROOT / "data" / "vectors"
BIT_WIDTH = 4


def stable_vector_id(node_id: str, kind: str = "code") -> int:
    """Return a deterministic non-zero uint64 ID for TurboVec IdMapIndex."""
    digest = hashlib.blake2b(f"{kind}:{node_id}".encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "little", signed=False)
    return value or 1


class TurboVecIndex:
    def __init__(self, name: str, dim: int = 384, bit_width: int = BIT_WIDTH):
        self.name = name
        self.dim = dim
        self.bit_width = bit_width
        self.index_path = VECTOR_DIR / f"{name}_{dim}_b{bit_width}.turbovec"
        self.registry_path = VECTOR_DIR / f"{name}_{dim}_b{bit_width}.registry.json"
        self.legacy_json_path = ROOT / f".turbovec_{name}.json"
        self._vectors: dict[str, list[float]] = {}
        self._vector_id_to_node_id: dict[int, str] = {}
        self._index = None
        self._dirty = False
        self._load()

    def count(self) -> int:
        return len(self._vector_id_to_node_id)

    def insert(self, node_id: str, vector: list[float] | np.ndarray) -> int:
        vec = np.asarray(vector, dtype=np.float32)
        if vec.shape != (self.dim,):
            raise ValueError(f"{self.name} vector for {node_id} must be shape ({self.dim},), got {vec.shape}")
        norm = float(np.linalg.norm(vec))
        if norm > 1e-8:
            vec = vec / norm
        vector_id = stable_vector_id(node_id, self.name)
        self._vectors[node_id] = vec.astype(np.float32).tolist()
        self._vector_id_to_node_id[vector_id] = node_id
        self._dirty = True
        return vector_id

    def remove(self, node_id: str) -> int:
        vector_id = stable_vector_id(node_id, self.name)
        self._vectors.pop(node_id, None)
        self._vector_id_to_node_id.pop(vector_id, None)
        if self._index is not None and _HAS_TURBOVEC:
            try:
                self._index.remove(vector_id)
            except Exception:
                # Rebuild on next search/save if direct removal is not supported
                # for the current internal state.
                self._dirty = True
        else:
            self._dirty = True
        return vector_id

    def bulk_insert(self, items: Iterable[tuple[str, list[float] | np.ndarray]]) -> None:
        for node_id, vector in items:
            self.insert(node_id, vector)

    def search(self, query_vec: list[float] | np.ndarray, k: int = 10) -> list[dict]:
        """Return [{'id': node_id, 'vector_id': uint64, 'score': float}, ...]."""
        q = np.asarray(query_vec, dtype=np.float32)
        if q.shape != (self.dim,):
            raise ValueError(f"query vector must be shape ({self.dim},), got {q.shape}")
        norm = float(np.linalg.norm(q))
        if norm > 1e-8:
            q = q / norm
        if _HAS_TURBOVEC:
            self._ensure_index()
            if self._index is None:
                return []
            scores, vector_ids = self._index.search(q.reshape(1, -1), k)
            results = []
            for score, vector_id in zip(scores[0], vector_ids[0]):
                node_id = self._vector_id_to_node_id.get(int(vector_id))
                if node_id:
                    results.append({"id": node_id, "vector_id": int(vector_id), "score": float(score)})
            return results
        return self._search_numpy(q, k)

    def save(self) -> None:
        VECTOR_DIR.mkdir(parents=True, exist_ok=True)
        self._write_registry()
        if _HAS_TURBOVEC:
            self._ensure_index()
            if self._index is not None:
                self._index.write(str(self.index_path))
        else:
            self.legacy_json_path.write_text(json.dumps(self._vectors), encoding="utf-8")
        self._dirty = False

    def _load(self) -> None:
        self._load_registry()
        if _HAS_TURBOVEC and self.index_path.exists() and self.registry_path.exists():
            self._index = turbovec.IdMapIndex.load(str(self.index_path))
        elif self.legacy_json_path.exists():
            self._load_legacy_vectors()
            if _HAS_TURBOVEC and self._vectors:
                self._dirty = True
        elif _HAS_TURBOVEC and self._vectors:
            self._dirty = True

    def _load_registry(self) -> None:
        if not self.registry_path.exists():
            return
        try:
            raw = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except Exception:
            return
        entries = raw.get("entries", raw)
        for key, entry in entries.items():
            try:
                vector_id = int(key)
            except ValueError:
                continue
            if isinstance(entry, str):
                node_id = entry
            else:
                node_id = entry.get("node_id", "")
            if node_id:
                self._vector_id_to_node_id[vector_id] = node_id

    def _load_legacy_vectors(self) -> None:
        try:
            raw = json.loads(self.legacy_json_path.read_text(encoding="utf-8"))
        except Exception:
            return
        for node_id, vector in raw.items():
            if isinstance(vector, list) and len(vector) == self.dim:
                vector_id = stable_vector_id(node_id, self.name)
                self._vectors[node_id] = vector
                self._vector_id_to_node_id[vector_id] = node_id

    def _write_registry(self) -> None:
        entries = {
            str(vector_id): {
                "node_id": node_id,
                "kind": self.name,
                "dim": self.dim,
                "bit_width": self.bit_width,
                "active": True,
            }
            for vector_id, node_id in sorted(self._vector_id_to_node_id.items())
        }
        self.registry_path.write_text(
            json.dumps({"kind": self.name, "dim": self.dim, "bit_width": self.bit_width, "entries": entries}, indent=2),
            encoding="utf-8",
        )

    def _ensure_index(self) -> None:
        if not _HAS_TURBOVEC:
            return
        if self._index is not None and not self._dirty:
            return
        if not self._vectors:
            self._index = None
            self._dirty = False
            return
        node_ids = list(self._vectors)
        vectors = np.asarray([self._vectors[node_id] for node_id in node_ids], dtype=np.float32)
        vector_ids = np.asarray([stable_vector_id(node_id, self.name) for node_id in node_ids], dtype=np.uint64)
        self._index = turbovec.IdMapIndex(dim=self.dim, bit_width=self.bit_width)
        self._index.add_with_ids(vectors, vector_ids)
        self._index.prepare()
        self._vector_id_to_node_id = {int(vector_id): node_id for vector_id, node_id in zip(vector_ids, node_ids)}
        self._dirty = False

    def _search_numpy(self, q: np.ndarray, k: int) -> list[dict]:
        results = []
        for node_id, vec in self._vectors.items():
            v = np.asarray(vec, dtype=np.float32)
            score = float(np.dot(q, v))
            results.append({"id": node_id, "vector_id": stable_vector_id(node_id, self.name), "score": score})
        results.sort(key=lambda item: item["score"], reverse=True)
        return results[:k]


code_index = TurboVecIndex("code", 384)
memory_index = TurboVecIndex("memory", 384)
graphsage_index = TurboVecIndex("graphsage", 128)
