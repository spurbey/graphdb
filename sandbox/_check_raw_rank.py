"""
Check export_snapshot rank using RAW API embeddings — same space as the
running pipeline (no mean-centering). Embed the query fresh, score all
candidates, report where export_snapshot lands.
"""
import json
import random as _random
import urllib.request
from pathlib import Path
import numpy as np
import igraph as ig

ROOT = Path(__file__).resolve().parents[1]

# ── Load API key ──────────────────────────────────────────────────────────────
preferred = ("llm_api_key_2", "llm_api_key2", "llm_api_key")
key = ""
for p in [ROOT / ".env", Path.cwd() / ".env"]:
    if not p.exists():
        continue
    vals = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        vals[k.strip().lower()] = v.strip()
    for name in preferred:
        if vals.get(name):
            key = vals[name]
            break
    if key:
        break

def embed_raw(text: str) -> np.ndarray:
    payload = json.dumps({"model": "nvidia/llama-nemotron-embed-vl-1b-v2:free",
                          "input": text}).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/embeddings",
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=20).read())
    return np.array(resp["data"][0]["embedding"], dtype=np.float32)

def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    return float(np.dot(a, b) / (na * nb)) if na > 0 and nb > 0 else 0.0

# ── Load graph ────────────────────────────────────────────────────────────────
with open("sandbox/amo_nodes.json", encoding="utf-8") as f:
    nodes_data = json.load(f)

has_nonzero = lambda v: bool(v) and any(abs(float(x)) > 1e-12 for x in v)

def _is_candidate(name: str, file: str, status: str, emb) -> bool:
    file_base = file.replace("\\", "/").split("/")[-1]
    return (
        status == "active"
        and emb is not None
        and not name.startswith("test_")
        and not file_base.startswith("test_")
    )

# ── Embed the query with raw API ──────────────────────────────────────────────
query = "store and save session memory snapshot"
print(f"Embedding query (raw API): '{query}'")
query_vec = embed_raw(query)
print("  Done.")

# ── Score all candidates ──────────────────────────────────────────────────────
scores = []
for n in nodes_data:
    emb_raw = n.get("embedding", [])
    emb = np.array(emb_raw, dtype=np.float32) if has_nonzero(emb_raw) else None
    if not _is_candidate(n["name"], n["file"], n.get("status", "superseded"), emb):
        continue
    sim = cosine(query_vec, emb)
    scores.append((sim, n["name"], n["file"].split("/")[-1], n["file"]))

scores.sort(reverse=True)

print(f"\nTotal candidates: {len(scores)}")
print()
print("Top-20 by raw cosine similarity:")
for rank, (sim, name, fname, fpath) in enumerate(scores[:20], 1):
    marker = " <-- TARGET" if name == "export_snapshot" and "snapshots" in fpath else ""
    print(f"  {rank:3d}. [{sim:.4f}] {name} [{fname}]{marker}")

print()
export_rank = None
export_score = None
for rank, (sim, name, fname, fpath) in enumerate(scores, 1):
    if name == "export_snapshot" and "snapshots" in fpath:
        export_rank = rank
        export_score = sim
        break

if export_rank:
    print(f"export_snapshot [snapshots.py]:")
    print(f"  raw cosine score : {export_score:.4f}")
    print(f"  raw rank         : {export_rank} / {len(scores)}")
    print()
    if export_rank <= 15:
        verdict = "NEAR-MISS in raw space — would be a seed. Miss is purely PPR scoping."
    elif export_rank <= 50:
        verdict = "MODERATE MISS — better summary likely brings into top-15 seeds."
    elif export_rank <= 200:
        verdict = "SIGNIFICANT MISS — better summary helps but not guaranteed."
    else:
        verdict = "TOTAL MISS — near-zero similarity in raw space. LLM summaries alone insufficient."
    print(f"Verdict: {verdict}")
else:
    print("export_snapshot not found in candidate pool")

# ── Also confirm ablation top-10 alignment ───────────────────────────────────
print()
print("Cross-check: do our top-10 match the ablation stored top-10?")
ab = json.loads((ROOT / "sandbox" / "ablation_results.json").read_text(encoding="utf-8"))
sq = next(q for q in ab["per_query"] if q["id"] == "old_snapshot")
stored = [r.split(" [")[0] for r in sq["top10_vector"]]
ours   = [name for _, name, _, _ in scores[:10]]
print(f"  Stored : {stored}")
print(f"  Current: {ours}")
matches = sum(1 for n in ours if n in stored)
print(f"  Overlap: {matches}/10  ({'consistent' if matches >= 7 else 'MISMATCH — different embedding'})")
