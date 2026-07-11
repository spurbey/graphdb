"""
Deep diagnosis for the 3 remaining permanent misses:
  1. _filter_answer_grade_nodes  (vector rank 2, graph MISS)
  2. apply_install_plan           (MISS everywhere)
  3. ingest_hook_payload          (ambiguous query, MISS everywhere)

For each: raw cosine rank, cluster, seed status, call neighborhood,
PPR score estimate, mechanism hypothesis.
"""
import json
import random as _random
import urllib.request
from pathlib import Path
import numpy as np
import igraph as ig

ROOT = Path(__file__).resolve().parents[1]

# ── Load graph ────────────────────────────────────────────────────────────────
with open("sandbox/amo_nodes.json", encoding="utf-8") as f:
    nodes_data = json.load(f)
with open("sandbox/amo_edges.json", encoding="utf-8") as f:
    edges_data = json.load(f)

node_id_set = {n["id"] for n in nodes_data}
edges_data = [e for e in edges_data if e["source"] in node_id_set and e["target"] in node_id_set]
node_lookup = {n["id"]: n for n in nodes_data}

G = ig.Graph(directed=True)
node_ids = [n["id"] for n in nodes_data]
G.add_vertices(len(node_ids))
id_to_idx = {nid: i for i, nid in enumerate(node_ids)}

has_nonzero = lambda v: bool(v) and any(abs(float(x)) > 1e-12 for x in v)

for i, n in enumerate(nodes_data):
    G.vs[i]["id"]     = n["id"]
    G.vs[i]["file"]   = n["file"]
    G.vs[i]["name"]   = n["name"]
    G.vs[i]["status"] = n.get("status", "superseded")
    emb = n.get("embedding", [])
    G.vs[i]["embedding"] = np.array(emb, dtype=np.float32) if has_nonzero(emb) else None

edge_tuples = [(id_to_idx[e["source"]], id_to_idx[e["target"]]) for e in edges_data]
G.add_edges(edge_tuples)
for i, e in enumerate(edges_data):
    G.es[i]["type"]            = e["type"]
    G.es[i]["co_change_count"] = e.get("co_change_count", 0)
    G.es[i]["category"]        = e.get("category", "")

calls_edge_ids = [e.index for e in G.es if e["type"] == "CALLS"]
calls_only = G.subgraph_edges(calls_edge_ids, delete_vertices=False)

_random.seed(42)
np.random.seed(42)
_communities = calls_only.community_infomap()
for _i, _c in enumerate(_communities.membership):
    G.vs[_i]["cluster_id"] = _c


def _is_candidate(v) -> bool:
    name      = v["name"] or ""
    file      = v["file"] or ""
    file_base = file.replace("\\", "/").split("/")[-1]
    return (
        v["status"] == "active"
        and v["embedding"] is not None
        and not name.startswith("test_")
        and not file_base.startswith("test_")
    )


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    return float(np.dot(a, b) / (na * nb)) if na > 0 and nb > 0 else 0.0


# ── API key ───────────────────────────────────────────────────────────────────
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


def score_all_candidates(query_vec: np.ndarray) -> list[tuple]:
    scores = []
    for v in G.vs:
        if _is_candidate(v):
            scores.append((cosine(query_vec, v["embedding"]), v.index, v["name"],
                           v["file"].split("/")[-1], v["file"]))
    scores.sort(reverse=True)
    return scores


def node_connectivity(target_name: str, target_file_hint: str) -> dict:
    results = {}
    for v in G.vs:
        if v["name"] == target_name and target_file_hint in (v["file"] or ""):
            idx = v.index
            callers = [G.vs[e.source]["name"] for e in G.es
                       if e.target == idx and e["type"] == "CALLS"]
            callees = [G.vs[e.target]["name"] for e in G.es
                       if e.source == idx and e["type"] == "CALLS"]
            cochanges = [(G.vs[e.source if e.target == idx else e.target]["name"],
                          e["category"])
                         for e in G.es
                         if (e.source == idx or e.target == idx) and e["type"] == "CO_CHANGE"]
            results = {
                "cluster": G.vs[idx]["cluster_id"],
                "status": v["status"],
                "is_candidate": _is_candidate(v),
                "callers": callers[:10],
                "callees": callees[:10],
                "cochanges": cochanges[:10],
                "file": v["file"],
            }
            break
    return results


def diagnose(query_text: str, target_name: str, target_file_hint: str,
             query_id: str, top_k_seeds: int = 15) -> None:
    print(f"\n{'='*70}")
    print(f"QUERY: {repr(query_text)}")
    print(f"TARGET: {target_name}  hint={target_file_hint}")
    print(f"{'='*70}")

    print(f"Embedding query...")
    qvec = embed_raw(query_text)

    scores = score_all_candidates(qvec)
    n_candidates = len(scores)

    # Find target rank
    target_rank = None
    target_score = None
    for rank, (sim, idx, name, fname, fpath) in enumerate(scores, 1):
        if name == target_name and target_file_hint in fpath:
            target_rank = rank
            target_score = sim
            break

    print(f"\nTotal candidates: {n_candidates}")
    print(f"Target '{target_name}': raw rank={target_rank}  score={target_score:.4f}" if target_rank
          else f"Target '{target_name}': NOT FOUND in candidate pool")

    # Top-20
    print(f"\nTop-20 by cosine:")
    for rank, (sim, idx, name, fname, fpath) in enumerate(scores[:20], 1):
        marker = " <-- TARGET" if name == target_name and target_file_hint in fpath else ""
        print(f"  {rank:3d}. [{sim:.4f}] {name} [{fname}]{marker}")

    # Seed window analysis
    seeds = scores[:top_k_seeds]
    seed_clusters: dict[int, int] = {}
    for _, idx, name, fname, fpath in seeds:
        cid = G.vs[idx]["cluster_id"]
        seed_clusters[cid] = seed_clusters.get(cid, 0) + 1

    print(f"\nSeed window (top-{top_k_seeds}) cluster distribution:")
    for cid, cnt in sorted(seed_clusters.items(), key=lambda x: -x[1]):
        print(f"  cluster {cid}: {cnt} seeds  weight={cnt/top_k_seeds:.3f}")

    # Target's relationship to seed window
    conn = node_connectivity(target_name, target_file_hint)
    if conn:
        tc = conn["cluster"]
        in_seeds = tc in seed_clusters
        seed_weight = seed_clusters.get(tc, 0) / top_k_seeds
        print(f"\nTarget cluster: {tc}")
        print(f"Target IS a seed: {target_rank is not None and target_rank <= top_k_seeds}")
        print(f"Target cluster in seed window: {in_seeds}  weight={seed_weight:.3f}")
        print(f"Callers  : {conn['callers'] or '(none)'}")
        print(f"Callees  : {conn['callees'] or '(none)'}")
        print(f"CO_CHANGE: {conn['cochanges'] or '(none)'}")

        # Mechanism hypothesis
        print(f"\nMechanism:")
        if target_rank is None:
            print("  NOT IN CANDIDATE POOL — filtered out (_is_candidate=False)")
        elif target_rank <= top_k_seeds and in_seeds and seed_weight > 0.1:
            print("  IS a seed with meaningful weight — PPR dilution likely if miss persists")
        elif target_rank <= top_k_seeds and seed_weight < 0.067:
            print("  IS a seed but minimal cluster weight — same PPR dilution as export_snapshot")
        elif not in_seeds:
            print("  CLUSTER HAS ZERO SEEDS — hard miss, soft reset gives zero PPR mass")
            print("  Same root cause as _filter_answer_grade_nodes would be if vector gets it")
        else:
            print("  Moderate seed weight — PPR should find it if call graph cooperates")

    # Cross-check with ablation stored top-10
    ab = json.loads((ROOT / "sandbox" / "ablation_results.json").read_text(encoding="utf-8"))
    sq = next((q for q in ab["per_query"] if q["id"] == query_id), None)
    if sq:
        stored = [r.split(" [")[0] for r in sq["top10_vector"]]
        ours   = [name for _, _, name, _, _ in scores[:10]]
        overlap = sum(1 for n in ours if n in stored)
        print(f"\nAblation cross-check: {overlap}/10 overlap with stored top-10"
              f"  ({'consistent' if overlap >= 7 else 'MISMATCH'})")


# ── Run all three diagnoses ───────────────────────────────────────────────────
diagnose(
    query_text="drop noisy answer grade nodes from current graph context results",
    target_name="_filter_answer_grade_nodes",
    target_file_hint="service",
    query_id="graph_context_filter",
)

diagnose(
    query_text="installer writes codex hook settings and applies the local mcp configuration",
    target_name="apply_install_plan",
    target_file_hint="service",
    query_id="install_hooks",
)

diagnose(
    query_text="codex hook captured user message should become durable memory evidence",
    target_name="ingest_hook_payload",
    target_file_hint="ingest",
    query_id="ambiguous_capture_persist",
)
