import sys
import json
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pipeline_api

# 1. Load the fully embedded mock memories
memories_path = ROOT / "sandbox" / "mock_memories_embedded.json"
with open(memories_path, "r", encoding="utf-8") as f:
    mock_data = json.load(f)

# Convert to a fast lookup dictionary by function_id
mock_memory_store = {}
all_states = [] # for vector search

for entry in mock_data:
    func_id = entry["function_id"]
    history = []
    for h in entry["history"]:
        state = {
            "state_id": f"state_{func_id}_{h['commit_sha']}",
            "commit_sha": h['commit_sha'],
            "memory": h['memory'],
            "edge_type": h['edge_type'],
            "memory_vec": h.get("memory_vec", [])
        }
        history.append(state)
        # Store flat list for vector search test
        flat_state = dict(state)
        flat_state["function_id"] = func_id
        all_states.append(flat_state)
    mock_memory_store[func_id] = history

# 2. Patch pipeline_api to use our JSON instead of HelixDB
def patched_read_function_memory_history(func_id):
    return mock_memory_store.get(func_id, [])

pipeline_api.read_function_memory_history = patched_read_function_memory_history

# 3. Initialize the igraph pipeline
print("Initializing in-memory igraph...")
pipeline_api.initialize()

print("\n" + "="*80)
print("TEST 1: commit_review via MCP")
print("Target: _noise_penalty (Tests chronologcial sorting and days_ago)")
print("="*80)

from tools.graph_tools import commit_review
review_result = commit_review(["src/agent_memory_orchestrator/memory/retrieval.py::_noise_penalty"])
print(json.dumps(review_result, indent=2))


print("\n" + "="*80)
print("TEST 2: explain_coupling via MCP")
print("Target: MemoryRetrievalMixin.search_memories <-> MemoryRetrievalMixin._vector_candidates")
print("="*80)

from tools.graph_tools import explain_coupling
coupling_result = explain_coupling(
    "src/agent_memory_orchestrator/memory/retrieval.py::MemoryRetrievalMixin.search_memories",
    "src/agent_memory_orchestrator/memory/retrieval.py::MemoryRetrievalMixin._vector_candidates"
)
print(json.dumps(coupling_result, indent=2) if coupling_result else "No co-change edge found in igraph.")

print("\n" + "="*80)
print("TEST 3: Semantic Vector Search (/query-history prototype)")
print("Query: 'Why was the IDE context stripping penalty increased?'")
print("="*80)

def cosine_sim(a, b):
    a, b = np.array(a), np.array(b)
    norm_a, norm_b = np.linalg.norm(a), np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0: return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))

query_vec = pipeline_api._embed("Why was the IDE context stripping penalty increased?")
scored_states = []
for s in all_states:
    sim = cosine_sim(query_vec, s["memory_vec"])
    scored_states.append((sim, s))

scored_states.sort(key=lambda x: x[0], reverse=True)
top_3 = scored_states[:3]

print("Top 3 Semantic Matches:")
for i, (sim, s) in enumerate(top_3):
    print(f"{i+1}. [Score: {sim:.3f}] {s['function_id']} ({s['edge_type']})")
    print(f"   {s['memory']}\n")
