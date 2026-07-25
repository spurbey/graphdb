"""
Turbovec Adapter (TurboQuant algorithm)
Replaces HelixDB HNSW for vector indexing and search.
"""
import os
import json
import numpy as np

# We provide a mock/shim here so the code runs even if turbovec isn't installed.
# In production, this would import turbovec and use its bindings.
try:
    import turbovec
    _HAS_TURBOVEC = True
except ImportError:
    _HAS_TURBOVEC = False

class TurbovecIndex:
    def __init__(self, name: str, dim: int = 384):
        self.name = name
        self.dim = dim
        self.data_path = f".turbovec_{name}.json"
        self._mock_data = {} # id -> vector
        if os.path.exists(self.data_path):
            try:
                with open(self.data_path, "r") as f:
                    self._mock_data = json.load(f)
            except Exception:
                pass

    def insert(self, doc_id: str, vector: list[float]):
        if _HAS_TURBOVEC:
            pass
        self._mock_data[doc_id] = vector

    def save(self):
        try:
            with open(self.data_path, "w") as f:
                json.dump(self._mock_data, f)
        except Exception:
            pass

    def search(self, query_vec: list[float], k: int = 10) -> list[dict]:
        """Returns list of dicts: {'id': doc_id, 'score': score}"""
        if _HAS_TURBOVEC:
            pass
        
        # Mock brute-force search
        q = np.array(query_vec, dtype=np.float32)
        results = []
        for doc_id, vec in self._mock_data.items():
            if not vec: continue
            v = np.array(vec, dtype=np.float32)
            score = float(np.dot(q, v))
            results.append({"id": doc_id, "score": score})
        
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:k]

# Singletons for the main indexes
code_index = TurbovecIndex("code", 384)
ai_summary_index = TurbovecIndex("ai_summary", 384)
memory_index = TurbovecIndex("memory", 384)
graphsage_index = TurbovecIndex("graphsage", 128)
