"""
Experiment 6: MMR vs File-Dedup vs No-Diversity

General question: Does any diversity mechanism improve HIT@13 over
just taking the top-k by score directly?

Modes:
  A) Top-k by score (no diversity) — current vector baseline
  B) MMR (lambda=0.6, current in PPR pipeline)
  C) File-dedup (max 2 per file, fill rest with next-best by score)
  D) MMR lambda=0.8 (less diversity, more score-weighted)

Applied to VECTOR scores only (since vector is already the best mode).
"""
import json
import urllib.request
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = ROOT / "sandbox"

nodes_data   = json.load(open(SANDBOX / "amo_nodes.json",       encoding="utf-8"))
eval_queries = json.load(open(SANDBOX / "query_rank_eval.json", encoding="utf-8"))

has_nz = lambda v: bool(v) and any(abs(float(x)) > 1e-12 for x in v)
candidates = {
    n["id"]: (np.array(n["embedding"], dtype=np.float32), n)
    for n in nodes_data
    if n.get("status") == "active" and has_nz(n.get("embedding", []))
    and not n.get("name", "").startswith("test_")
    and not n.get("file", "").replace("\\", "/").split("/")[-1].startswith("test_")
}
print(f"Candidates: {len(candidates)}")

GOD_NODES = {"close", "make_settings", "init_db", "health_ping", "add_event", "append"}

def cosine(a, b):
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    return float(np.dot(a, b) / (na * nb)) if na > 0 and nb > 0 else 0.0

preferred = ("llm_api_key_2", "llm_api_key2", "llm_api_key")
key = ""
for p in [ROOT / ".env"]:
    if not p.exists(): continue
    vals = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if "=" not in line: continue
        k, v = line.split("=", 1); vals[k.strip().lower()] = v.strip()
    for name in preferred:
        if vals.get(name): key = vals[name]; break
    if key: break

def embed(text):
    import time
    for attempt in range(3):
        try:
            payload = json.dumps({"model": "nvidia/llama-nemotron-embed-vl-1b-v2:free", "input": text}).encode()
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/embeddings", data=payload,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
            return np.array(json.loads(urllib.request.urlopen(req, timeout=30).read())["data"][0]["embedding"], dtype=np.float32)
        except Exception as e:
            if attempt < 2: time.sleep(2 ** attempt)
            else: return np.zeros(2048, dtype=np.float32)

def score_all(query_vec):
    return sorted(
        [(cosine(query_vec, emb), nid, n) for nid, (emb, n) in candidates.items()],
        reverse=True
    )

def mode_A(scored, k=10):
    """Top-k by score, no diversity."""
    return [(nid, n) for _, nid, n in scored[:k]]

def mode_B(scored, k=10, lam=0.6):
    """MMR with lambda."""
    selected = []
    remaining = [(s, nid, n) for s, nid, n in scored[:k*3]]
    while len(selected) < k and remaining:
        best_idx, best_score = None, -1e9
        for i, (base_score, nid, n) in enumerate(remaining):
            emb = candidates[nid][0]
            if not selected:
                sim_sel = 0.0
            else:
                sims = [cosine(emb, candidates[s_nid][0]) for _, s_nid, _ in selected]
                sim_sel = max(sims) if sims else 0.0
            score = lam * base_score - (1 - lam) * sim_sel
            if score > best_score:
                best_score, best_idx = score, i
        if best_idx is None: break
        selected.append(remaining.pop(best_idx))
    return [(nid, n) for _, nid, n in selected]

def mode_C(scored, k=10, max_per_file=2):
    """File-dedup: max max_per_file functions per file."""
    file_counts = {}
    result = []
    for _, nid, n in scored:
        fname = (n.get("file") or "").split("/")[-1]
        if file_counts.get(fname, 0) < max_per_file:
            result.append((nid, n))
            file_counts[fname] = file_counts.get(fname, 0) + 1
        if len(result) >= k:
            break
    return result

def hit_rank(result, target_name):
    for rank, (nid, n) in enumerate(result, 1):
        if n.get("name") == target_name:
            return rank
    return None

def god_count(result):
    return sum(1 for _, n in result if n.get("name", "") in GOD_NODES)

# ── Run ───────────────────────────────────────────────────────────────────────
print("\nRunning 13 queries...")
all_results = []

for q in eval_queries:
    query, target = q["query"], q["target_name"]
    print(f"  '{query[:50]}...' | {target}")

    qvec = embed(query)
    scored = score_all(qvec)

    r_A  = mode_A(scored, k=10)
    r_B6 = mode_B(scored, k=10, lam=0.6)
    r_B8 = mode_B(scored, k=10, lam=0.8)
    r_C  = mode_C(scored, k=10, max_per_file=2)
    r_C3 = mode_C(scored, k=10, max_per_file=3)

    row = {
        "id": q["id"], "target": target,
        "A_rank":  hit_rank(r_A,  target), "A_god":  god_count(r_A),
        "B6_rank": hit_rank(r_B6, target), "B6_god": god_count(r_B6),
        "B8_rank": hit_rank(r_B8, target), "B8_god": god_count(r_B8),
        "C2_rank": hit_rank(r_C,  target), "C2_god": god_count(r_C),
        "C3_rank": hit_rank(r_C3, target), "C3_god": god_count(r_C3),
    }
    def f(k): return str(row[k]) if row[k] else "MISS"
    print(f"    A={f('A_rank'):4} B(0.6)={f('B6_rank'):4} B(0.8)={f('B8_rank'):4} "
          f"C(max2)={f('C2_rank'):4} C(max3)={f('C3_rank'):4}")
    all_results.append(row)

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("EXPERIMENT 6 RESULTS")
print("=" * 70)

def summ(key_r, key_g):
    h = sum(1 for r in all_results if r[key_r])
    g = sum(r[key_g] for r in all_results)
    return h, g

modes = [
    ("A:  No diversity (top-k)", "A_rank",  "A_god"),
    ("B6: MMR lambda=0.6",        "B6_rank", "B6_god"),
    ("B8: MMR lambda=0.8",        "B8_rank", "B8_god"),
    ("C2: File-dedup max=2",      "C2_rank", "C2_god"),
    ("C3: File-dedup max=3",      "C3_rank", "C3_god"),
]

print(f"\n{'Mode':<30} {'HIT@13':>7} {'God-nodes':>9}")
print("-" * 48)
for label, rk, gk in modes:
    h, g = summ(rk, gk)
    print(f"  {label:<28} {h:>7} {g:>9}")

print("\nPer-query:")
print(f"{'Query ID':<30} {'A':>4} {'B6':>4} {'B8':>4} {'C2':>4} {'C3':>4}")
print("-" * 50)
for r in all_results:
    def f(k): return str(r[k]) if r[k] else "M"
    print(f"  {r['id']:<28} {f('A_rank'):>4} {f('B6_rank'):>4} {f('B8_rank'):>4} "
          f"{f('C2_rank'):>4} {f('C3_rank'):>4}")

# Save
out = {"experiment": 6, "results": all_results,
       "summary": {m[0]: {"hit": summ(m[1],m[2])[0], "god": summ(m[1],m[2])[1]} for m in modes}}
(SANDBOX / "exp6_diversity.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
print("\nResults saved: sandbox/exp6_diversity.json")

print("\nVERDICT:")
h_A, _ = summ("A_rank", "A_god")
best_mode = max(modes[1:], key=lambda m: summ(m[1], m[2])[0])
h_best, g_best = summ(best_mode[1], best_mode[2])
if h_best > h_A:
    print(f"  {best_mode[0]} improves: {h_A} -> {h_best} HIT@13")
    print(f"  ADOPT diversity mechanism: {best_mode[0]}")
elif h_best == h_A:
    print(f"  No diversity mode improves over top-k ({h_A}/13)")
    print(f"  KEEP: no-diversity (top-k by score) — simplest, works as well")
else:
    print(f"  All diversity modes match or hurt. KEEP: no-diversity.")
