"""
Experiment 4: CO_CHANGE bridges as explicit cross-module expansion.

GENERAL question: Does adding CO_CHANGE neighbors (scored by theme
relevance to the query) to vector results improve HIT@13 without
adding god-node contamination?

Tested across all 13 eval queries, not just redact_secrets.

Modes compared:
  A) Vector only (current best: 10/13, 2 god-nodes)
  B) Vector + CO_CHANGE bridges (theme_score > threshold)
  C) Vector + CO_CHANGE bridges (any category, no theme scoring)

The threshold sweep shows: at what boost level does adding bridges
help vs hurt? Measured by HIT@13 and god-node count at each threshold.
"""
import json
import time
import urllib.request
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = ROOT / "sandbox"

# ── Load data ─────────────────────────────────────────────────────────────────
print("Loading data...")
nodes_data   = json.load(open(SANDBOX / "amo_nodes.json",          encoding="utf-8"))
pairs_data   = json.load(open(SANDBOX / "amo_cochange_pairs.json", encoding="utf-8"))
themes_data  = json.load(open(SANDBOX / "amo_cochange_themes.json",encoding="utf-8"))
eval_queries = json.load(open(SANDBOX / "query_rank_eval.json",    encoding="utf-8"))

# Build lookup maps
node_by_id = {n["id"]: n for n in nodes_data}
has_nz = lambda v: bool(v) and any(abs(float(x)) > 1e-12 for x in v)

candidates = {
    n["id"]: np.array(n["embedding"], dtype=np.float32)
    for n in nodes_data
    if n.get("status") == "active"
    and has_nz(n.get("embedding", []))
    and not n.get("name", "").startswith("test_")
    and not n.get("file", "").replace("\\", "/").split("/")[-1].startswith("test_")
}
print(f"  Candidates: {len(candidates)}")
print(f"  CO_CHANGE pairs: {len(pairs_data)}")
print(f"  Themes: {len(themes_data)}")

GOD_NODES = {"close", "make_settings", "init_db", "health_ping", "add_event", "append"}

def cosine(a, b):
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    return float(np.dot(a, b) / (na * nb)) if na > 0 and nb > 0 else 0.0

# Build CO_CHANGE adjacency: node_id -> list of (neighbor_id, pair)
cochange_adj = {}
for p in pairs_data:
    src, tgt = p.get("source",""), p.get("target","")
    if src not in cochange_adj: cochange_adj[src] = []
    if tgt not in cochange_adj: tgt_list = cochange_adj.setdefault(tgt, [])
    cochange_adj[src].append((tgt, p))
    cochange_adj.setdefault(tgt, []).append((src, p))

# Build theme label vectors (embed once)
def theme_label(theme):
    if theme.get("dependency_id"):
        dep = theme["dependency_id"].replace("function:","").replace("file:","")
        return f"shared dependency {dep}"
    msgs = " ".join(m.splitlines()[0] for m in (theme.get("commit_messages") or [])[:3] if m)
    return f"{theme.get('category','cochange')} {msgs}".strip()[:200]

# ── API key ───────────────────────────────────────────────────────────────────
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

def embed(text, retries=3):
    for attempt in range(retries):
        try:
            payload = json.dumps({"model": "nvidia/llama-nemotron-embed-vl-1b-v2:free", "input": text}).encode()
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/embeddings", data=payload,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
            return np.array(json.loads(urllib.request.urlopen(req, timeout=30).read())["data"][0]["embedding"], dtype=np.float32)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                print(f"  [embed error] {e}")
                return np.zeros(2048, dtype=np.float32)

# Embed all theme labels once
print("Embedding theme labels (81 themes)...")
theme_vecs = {}
for t in themes_data:
    label = theme_label(t)
    if label.strip():
        theme_vecs[t["theme_id"]] = embed(label)
        time.sleep(0.3)
print(f"  Embedded {len(theme_vecs)} theme vectors")


# ── Core functions ─────────────────────────────────────────────────────────────

def vector_top_k(query_vec, k=10):
    scores = [(cosine(query_vec, emb), nid) for nid, emb in candidates.items()]
    scores.sort(reverse=True)
    return [nid for _, nid in scores[:k]]

def cochange_bridges(anchor_ids, query_vec, threshold=0.2, max_bridges=5):
    """
    For each anchor, find CO_CHANGE neighbors where theme relevance > threshold.
    Returns list of (neighbor_id, boost_score) sorted by boost descending.
    Excludes nodes already in anchor_ids and test/inactive nodes.
    """
    seen = set(anchor_ids)
    bridges = []

    for anchor_id in anchor_ids:
        neighbors = cochange_adj.get(anchor_id, [])
        for neighbor_id, pair in neighbors:
            if neighbor_id in seen:
                continue
            if neighbor_id not in candidates:  # not active or not embedded
                continue
            seen.add(neighbor_id)

            # Score themes against query
            theme_props = pair.get("theme_proportions", {})
            boost = sum(
                prop * cosine(query_vec, theme_vecs[tid])
                for tid, prop in theme_props.items()
                if tid in theme_vecs
            )

            if boost >= threshold:
                bridges.append((neighbor_id, boost))

    bridges.sort(key=lambda x: x[1], reverse=True)
    return bridges[:max_bridges]

def run_mode_B(query_vec, threshold, k=10):
    """Vector top-5 anchors + CO_CHANGE bridges above threshold."""
    anchors = vector_top_k(query_vec, k=5)
    bridges = cochange_bridges(anchors, query_vec, threshold=threshold)
    bridge_ids = [nid for nid, _ in bridges]
    # Merge: anchors first, then bridges, pad with more vector results if needed
    result = anchors + bridge_ids
    # If fewer than k, add more from vector
    if len(result) < k:
        more = vector_top_k(query_vec, k=k*2)
        for nid in more:
            if nid not in set(result):
                result.append(nid)
                if len(result) >= k:
                    break
    return result[:k]

def run_mode_C(query_vec, k=10):
    """Vector top-5 + ANY CO_CHANGE neighbor (no theme scoring)."""
    anchors = vector_top_k(query_vec, k=5)
    seen = set(anchors)
    bridges = []
    for anchor_id in anchors:
        for neighbor_id, _ in cochange_adj.get(anchor_id, []):
            if neighbor_id not in seen and neighbor_id in candidates:
                bridges.append(neighbor_id)
                seen.add(neighbor_id)
    result = anchors + bridges
    if len(result) < k:
        more = vector_top_k(query_vec, k=k*2)
        for nid in more:
            if nid not in set(result):
                result.append(nid)
                if len(result) >= k: break
    return result[:k]

def hit_rank(result_ids, target_name):
    for rank, nid in enumerate(result_ids, 1):
        n = node_by_id.get(nid, {})
        if n.get("name") == target_name:
            return rank
    return None

def god_count(result_ids):
    return sum(1 for nid in result_ids if node_by_id.get(nid, {}).get("name","") in GOD_NODES)


# ── Run all queries ───────────────────────────────────────────────────────────
print("\nRunning all 13 eval queries...")
print("=" * 70)

THRESHOLDS = [0.1, 0.2, 0.3, 0.4]
all_results = []

for q in eval_queries:
    query, target = q["query"], q["target_name"]
    print(f"\n  '{query[:55]}' | target: {target}")

    qvec = embed(query)

    r_A = vector_top_k(qvec, k=10)
    r_C = run_mode_C(qvec, k=10)
    r_B = {t: run_mode_B(qvec, threshold=t, k=10) for t in THRESHOLDS}

    row = {
        "id": q["id"],
        "target": target,
        "A_rank": hit_rank(r_A, target),
        "A_god":  god_count(r_A),
        "C_rank": hit_rank(r_C, target),
        "C_god":  god_count(r_C),
    }
    for t in THRESHOLDS:
        row[f"B{t}_rank"] = hit_rank(r_B[t], target)
        row[f"B{t}_god"]  = god_count(r_B[t])

    def fmt(r): return str(r) if r else "MISS"
    print(f"    A(vec):        rank={fmt(row['A_rank'])}  god={row['A_god']}")
    for t in THRESHOLDS:
        bridges_added = len(cochange_bridges(vector_top_k(qvec, k=5), qvec, threshold=t))
        print(f"    B(t={t}): rank={fmt(row[f'B{t}_rank'])}  god={row[f'B{t}_god']}  bridges_added={bridges_added}")
    print(f"    C(any cochange):rank={fmt(row['C_rank'])}  god={row['C_god']}")

    all_results.append(row)


# ── Summary ───────────────────────────────────────────────────────────────────
print("\n\n" + "=" * 70)
print("EXPERIMENT 4 SUMMARY")
print("=" * 70)

def summarize(results, rank_key, god_key):
    hits = sum(1 for r in results if r[rank_key])
    gods = sum(r[god_key] for r in results)
    return hits, gods

h_A, g_A = summarize(all_results, "A_rank", "A_god")
h_C, g_C = summarize(all_results, "C_rank", "C_god")
print(f"\n{'Mode':<25} {'HIT@13':>7} {'God-nodes':>9}")
print("-" * 44)
print(f"  A: Vector only          {h_A:>7} {g_A:>9}")
for t in THRESHOLDS:
    h, g = summarize(all_results, f"B{t}_rank", f"B{t}_god")
    print(f"  B: +CO_CHANGE (t={t})    {h:>7} {g:>9}")
print(f"  C: +Any CO_CHANGE       {h_C:>7} {g_C:>9}")

print("\nPer-query breakdown:")
print(f"{'Query ID':<35} {'A':>5}", end="")
for t in THRESHOLDS: print(f" {'B'+str(t):>7}", end="")
print(f" {'C':>5}")
print("-" * 70)
for r in all_results:
    def f(k): return str(r[k]) if r[k] else "M"
    print(f"  {r['id']:<33} {f('A_rank'):>5}", end="")
    for t in THRESHOLDS: print(f" {f(f'B{t}_rank'):>7}", end="")
    print(f" {f('C_rank'):>5}")

# Save
out = {"experiment": 4, "results": all_results,
       "summary": {
           "vector": {"hit": h_A, "god": g_A},
           "cochange_any": {"hit": h_C, "god": g_C},
           **{f"bridge_t{t}": {"hit": summarize(all_results,f"B{t}_rank",f"B{t}_god")[0],
                                "god": summarize(all_results,f"B{t}_rank",f"B{t}_god")[1]}
              for t in THRESHOLDS}
       }}
outpath = SANDBOX / "exp4_cochange_bridge.json"
outpath.write_text(json.dumps(out, indent=2), encoding="utf-8")
print(f"\nResults saved: {outpath}")

print("\nVERDICT:")
best_b = max(THRESHOLDS, key=lambda t: summarize(all_results,f"B{t}_rank",f"B{t}_god")[0])
h_best, g_best = summarize(all_results, f"B{best_b}_rank", f"B{best_b}_god")
if h_best > h_A:
    print(f"  CO_CHANGE bridges (t={best_b}) improved: {h_A} -> {h_best} HIT@13")
    if g_best <= g_A + 2:
        print(f"  God-nodes acceptable: {g_best} (baseline {g_A})")
        print(f"  ADOPT: add CO_CHANGE bridge expansion at threshold={best_b}")
    else:
        print(f"  God-nodes too high: {g_best} vs baseline {g_A}")
        print(f"  Raise threshold or filter god-nodes explicitly")
elif h_best == h_A:
    print(f"  CO_CHANGE bridges: no improvement over vector alone ({h_A}/13)")
    print(f"  The co-change pairs in the dataset don't bridge the missing queries")
    print(f"  SKIP: CO_CHANGE bridge expansion not useful for retrieval")
else:
    print(f"  CO_CHANGE bridges hurt: {h_A} -> {h_best} HIT@13. REJECT.")
