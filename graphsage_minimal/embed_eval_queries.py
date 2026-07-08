from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "graphsage_minimal" / "data"
MODEL = "nvidia/llama-nemotron-embed-vl-1b-v2:free"
DIM = 2048


def _load_api_key() -> str:
    for name in ("OPENROUTER_API_KEY", "LLM_API_KEY", "llm_api_key"):
        value = os.environ.get(name)
        if value:
            return value.strip()

    env_path = ROOT / ".env"
    if not env_path.exists():
        return ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lower()
        if key in {"openrouter_api_key", "llm_api_key", "api_key"} or "api" in key:
            return value.strip()
    return ""


def _embed(text: str, api_key: str) -> np.ndarray:
    payload = json.dumps({"model": MODEL, "input": text[:2000]}).encode("utf-8")
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/embeddings",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    response = json.loads(urllib.request.urlopen(request, timeout=30).read())
    return np.asarray(response["data"][0]["embedding"], dtype=np.float32)


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    api_key = _load_api_key()
    if not api_key:
        raise SystemExit("No OpenRouter API key found in environment or .env")

    queries = json.loads((DATA / "eval_queries.json").read_text(encoding="utf-8"))
    vectors = []
    for row in queries:
        print(f"Embedding query: {row['id']}")
        vec = _embed(row["query"], api_key)
        vectors.append(vec)

    arr = np.vstack(vectors).astype(np.float32)
    arr = arr - arr.mean(axis=0, keepdims=True)
    arr = arr / np.maximum(np.linalg.norm(arr, axis=1, keepdims=True), 1e-8)
    np.save(DATA / "query_embeddings.npy", arr)
    print(f"Wrote {DATA / 'query_embeddings.npy'}")


if __name__ == "__main__":
    main()

