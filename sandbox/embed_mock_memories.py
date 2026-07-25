import sys
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pipeline_api

memories_path = ROOT / "sandbox" / "mock_memories.json"
output_path = ROOT / "sandbox" / "mock_memories_embedded.json"

with open(memories_path, "r", encoding="utf-8") as f:
    mock_data = json.load(f)

print("Embedding memory text to generate 2048-D memory_vec...")
for entry in mock_data:
    for h in entry["history"]:
        if "memory_vec" not in h:
            print(f"Embedding: {h['memory'][:50]}...")
            vec = pipeline_api._embed(h["memory"])
            h["memory_vec"] = vec.tolist()
            time.sleep(0.5) # respect rate limit

with open(output_path, "w", encoding="utf-8") as f:
    json.dump(mock_data, f, indent=2)

print(f"Successfully wrote fully embedded JSON to {output_path}")
