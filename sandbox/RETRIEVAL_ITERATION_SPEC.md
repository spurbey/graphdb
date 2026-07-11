# Retrieval Pipeline — Iteration Spec

**Date:** 2026-07-11  
**Scope:** sandbox/ only. No HelixDB, no production code touched.  
**Goal:** Get to a retrieval pipeline that reliably returns the right subgraph for a natural language query, with known failure modes documented and tested.

---

## Current Baseline (measured, not estimated)

Source: `sandbox/ablation_results.json`, 10-query eval, theme embedding active.

| Mode | HIT@10 | Notes |
|------|--------|-------|
| Vector only | 5/10 | Fast, no graph. Wins on lexically obvious queries. |
| CALLS PPR (Mode B) | 3/10 | Worse than vector on this eval. Infomap hard boundary causing misses. |
| Theme overlay (Mode C) | 4/10 | Proves one additional recovery (`redact_secrets`). But also loses `memory_search` vs vector-only. |

**Known misses across all modes:**
- `export_snapshot` — MISS in all 3 modes. Vector top-10 has test variants but not the function itself.
- `apply_install_plan` — MISS in all 3 modes. PPR gets `build_install_plan` (adjacent) but not the target.
- `_filter_answer_grade_nodes` — MISS in PPR/theme. Vector-only ranks it 2nd. Infomap scoping kills it in graph modes.
- `ingest_hook_payload` — Ranks 3 vector, 4 PPR/theme. Should be rank 1 for its query.
- `memory_search` — Ranks 2 vector, 8 PPR, MISS in theme overlay. Theme overlay made it worse.

**Critical observation from the data:**  
PPR (Mode B) scores 3/10 vs vector-only 5/10. The graph is actively hurting 2 queries
that vector search gets right. This is the Infomap hard boundary + god-node contamination
working together. Fix this before integrating theme overlay further.

---

## What is NOT in scope for this iteration

- HelixDB migration
- GraphSAGE integration
- LLM summary activation
- Overlapping communities (deferred — needs cross-cutting query eval first)
- Commit review pipeline
- Co-change explainability tool

---

## Iteration Plan

### Phase 0 — Stabilize the measurement foundation (do this before Phase 1)

Phase 0 must complete before any Phase 1 numbers are trusted. Three fixes,
ordered deliberately — 0a and 0b run together, 0c only after remeasuring.

#### Change 0a: Fix Infomap non-determinism

**Problem:** `community_infomap()` is randomized. Currently called inside
`run_cold_discovery` on every query. Community assignments can shift between
runs, making acceptance criteria untrustworthy — a query could pass or fail
purely due to algorithmic noise with no code change.

**Fix:** Hoist Infomap to run once at module load with a fixed seed. Store
`membership` array on `G.vs["cluster_id"]` at startup. Remove Infomap call
from inside `run_cold_discovery`.

igraph's `community_infomap` does not accept a `random_seed` parameter
directly — fix randomness via `random.seed()` + `numpy.random.seed()` before
the call:

```python
import random as _random
import numpy as _np

# At module level, after graph is built:
_random.seed(42)
_np.random.seed(42)
communities = calls_only.community_infomap()
for i, c in enumerate(communities.membership):
    G.vs[i]["cluster_id"] = c
INFOMAP_N_COMMUNITIES = len(set(communities.membership))
```

Apply in both `igraph_sandbox.py` and `cochange_ablation.py`.

#### Change 0b: Filter test functions from candidate pool

**Problem:** Test functions occupy 23-33% of top-10 slots across all modes.
For `old_snapshot`, 7 of 10 vector-only slots are test variants of `snapshot`,
directly crowding out `export_snapshot`. This is causing at least one known
miss independently of the PPR/Infomap issues.

**Fix:** Exclude nodes where `name.startswith("test_")` from the active
candidate pool used for vector seeding AND from PPR candidates AND from MMR
selection.

```python
def _is_candidate(v) -> bool:
    return (
        v["status"] == "active"
        and v["embedding"] is not None
        and not v["name"].startswith("test_")
    )
```

Apply this filter at three points:
1. Vector seed search (only score non-test active nodes)
2. PPR ranked candidates (filter before MMR)
3. Subgraph output (don't include test nodes in returned results)

NOTE: test functions remain in the graph and participate as intermediate nodes
in PPR traversal (they can still carry PPR mass through). They are only
excluded from being *returned* as results.

**Run 0a and 0b together, then remeasure before doing 0c.**

Expected after 0a+0b:
- `old_snapshot`: `export_snapshot` should now appear — 7 of 10 blocker slots
  freed up
- God-node frequency table will shift — measure it fresh before tuning
- HIT@10 baseline may improve across all modes before any algorithmic change

#### Remeasure after 0a+0b

Run `sandbox/cochange_ablation.py` with theme embedding active. Record:
1. New HIT@10 for all three modes — this becomes the NEW baseline
2. New god-node frequency table (close, make_settings, init_db, health_ping,
   add_event) — this becomes the target for 0c tuning
3. Confirm `old_snapshot` is fixed or still missing

#### Change 0c: Retune hub penalty (ONLY after 0a+0b remeasure)

**Problem (measured):** `close`, `make_settings`, `add_event` each appear in
5/10 queries in PPR modes. Current penalty `log(degree+1)^1.5` on top-5%
CALLS-degree nodes is failing at its one job. This will get worse when Phase 1
soft reset opens more community paths.

**Fix:** Raise the penalty exponent. Starting point: `^2.0`. If god-nodes still
appear in >3/10 queries after that, try `^2.5` or tighten the threshold from
top-5% to top-3%.

```python
# Current (failing):
base_cost *= math.log(calls_deg + 1) ** 1.5

# Proposed starting point:
base_cost *= math.log(calls_deg + 1) ** 2.0
```

Tune by rerunning ablation and checking god-node frequency after each change.
Target: no single god-node appears in top-5 results for more than 2/10 queries.

**Acceptance criterion for Phase 0:**
- Infomap runs once at startup with fixed seed. Same query produces identical
  results on repeated runs.
- Test functions do not appear in returned top-10 results for any query.
- God-node frequency (close, make_settings, init_db, health_ping, add_event)
  measured on clean (test-filtered) pool and documented as the Phase 0 baseline.
- Hub penalty tuned until no god-node appears in top-5 for >2/10 queries.
- New HIT@10 baseline documented for all three modes — this replaces the old
  3/10 and 5/10 numbers as the Phase 1 acceptance bar.

---

### Phase 1 — Fix PPR so it stops being worse than vector search

**Target:** Mode B (CALLS PPR) >= vector-only (5/10). Currently 3/10.

#### Change 1A: Soft community weighting in reset vector

**File:** `sandbox/igraph_sandbox.py` (and mirror in `cochange_ablation.py` `_run_retrieval`)  
**Location:** Inside `run_cold_discovery`, Step 3 reset vector construction.

**Current code:**
```python
dominant_cluster = max(cluster_counts, key=cluster_counts.get)
cluster_mask = np.array([
    1.0 if G.vs[i]["cluster_id"] == dominant_cluster else 0.0
    for i in range(G.vcount())
])
reset_vector = np.zeros(G.vcount())
for idx, score in top_seeds:
    reset_vector[idx] = max(score, 0.0)
reset_vector *= cluster_mask  # hard zero for non-dominant clusters
```

**Proposed code:**
```python
total_seeds = len(top_seeds)
community_weight = {
    cid: count / total_seeds
    for cid, count in cluster_counts.items()
}
reset_vector = np.zeros(G.vcount())
for idx, score in top_seeds:
    cid = G.vs[idx]["cluster_id"]
    w = community_weight.get(cid, 0.0)
    reset_vector[idx] = max(score, 0.0) * w
total = reset_vector.sum()
if total > 0:
    reset_vector /= total
else:
    for idx, _ in top_seeds:
        reset_vector[idx] = 1.0 / len(top_seeds)
```

**Why:** Binary mask zeros out communities with 1-2 seeds entirely. Proportional
weighting keeps them alive at small weight. A community with 1/15 seeds gets
`0.067` of the teleportation mass instead of 0.

#### Change 1B: Soft cluster filter after PPR

**Current code:**
```python
top_clusters = sorted(cluster_counts, key=cluster_counts.get, reverse=True)[:3]
top_cluster_set = set(top_clusters)
ranked_ppr = [
    (idx, score) for idx, score in ranked_ppr
    if G.vs[idx]["cluster_id"] in top_cluster_set
][:30]
```

**Proposed code:**
```python
# Include all clusters that had at least 1 seed, not just top-3
top_cluster_set = set(cluster_counts.keys())
ranked_ppr = [
    (idx, score) for idx, score in ranked_ppr
    if G.vs[idx]["cluster_id"] in top_cluster_set
][:30]
```

**Why:** Currently cluster 1 (`export_snapshot`'s home) doesn't make top-3 seeds
so it gets hard-removed after PPR too. Double elimination.

#### Validation check for Phase 1 (REQUIRED — both directions)

Run `sandbox/igraph_sandbox.py` validation after changes. Check:

**(a) Misses fixed:**
- `export_snapshot` — does it appear in top-10 for query "store and save session memory snapshot"?
- `_filter_answer_grade_nodes` — does it appear for "drop noisy answer grade nodes"?
- `ingest_hook_payload` — does it rank higher than 4 (currently rank 4 in PPR)?

**(b) No regression on passing queries (check these explicitly):**
- `memory_rebuild_indexes` — currently rank 1 in PPR. Must stay in top-3.
- `tool_contracts` — currently rank 6 in PPR. Must stay in top-10.
- `memory_search` — currently rank 8 in PPR. Must stay in top-10.

**(c) God-node creep check:**
After the soft reset opens more communities, check whether utility/config functions
appear in top-10 results where they don't belong:
- `close [service.py]` — appears in top-3 in current PPR for multiple queries. Should drop or stay low.
- `make_settings [test_graph_merge.py]` — same.
- `init_db [service.py]` — same.
- `health_ping [server.py]` — same.

If (c) shows god-nodes increasing, the hub penalty multiplier needs to be raised
(currently `log(degree+1)^1.5`, try `^2.0`).

**Acceptance criterion for Phase 1:**
Mode B HIT@10 >= Phase 0 baseline (measured after 0a+0b+0c), AND no
previously-passing query drops out of top-10, AND god-node count in top-3
results does not increase vs Phase 0 baseline. The old "5/10" target is
replaced by whatever Phase 0 measures — it may be higher if test filtering
alone recovers missed queries.

---

### Phase 2 — Integrate theme overlay properly

Only start this after Phase 1 acceptance criterion is met.

**Problem with current Mode C:**
- `memory_search` goes from rank 8 (PPR) to MISS (theme overlay). Theme overlay
  actively broke a working result.
- `memory_rebuild_indexes` goes from rank 1 (PPR) to MISS in theme overlay.
  Same problem.
- The theme boost is applied to CO_CHANGE edges and then PPR runs on the FULL
  graph. But the Infomap scoping still uses the hard binary mask. So the theme
  overlay is running on a broken reset vector.

**The correct order:**
Fix Phase 1 first. Then apply theme overlay on top of the fixed reset vector.
The theme overlay logic from `cochange_ablation.py` (`_theme_boosted_weights`)
is correct as written — it just needs to sit on a fixed foundation.

**What to integrate:**
Copy `_theme_boosted_weights()` from `cochange_ablation.py` into `igraph_sandbox.py`
as a function. Add it as a parameter to `run_cold_discovery`:

```python
def run_cold_discovery(query_embedding, top_k_seeds=15, final_k=10, use_theme_overlay=False):
    ...
    if use_theme_overlay:
        G.es["weight"] = _theme_boosted_weights(query_embedding)
    else:
        G.es["weight"] = static_weights
    ...
```

Default `use_theme_overlay=False` so the baseline doesn't change unless explicitly enabled.

**Validation for Phase 2:**
Run with `use_theme_overlay=True` against all 10 queries.

Expected:
- `redact_secrets` should still appear (the one thing theme overlay demonstrably fixes).
- `memory_search` and `memory_rebuild_indexes` must NOT regress from Phase 1 baseline.
- Total HIT@10 should be >= Phase 1 result.

If theme overlay improves >= 1 query and regresses 0, integrate permanently.
If it regresses any query that Phase 1 fixed, investigate which theme is causing
the bad boost and either tune the boost formula or exclude that category.

---

### Phase 3 — Subgraph output format

Only after Phase 2 is stable.

**Current output:** flat list of function names printed to stdout. No edges.
No machine-readable format. Useless to an LLM agent.

**Required output:** structured subgraph JSON that an LLM can reason about.

```python
def build_subgraph_output(selected_indices: list[int], query: str, scores: dict) -> dict:
    """
    Given MMR-selected node indices, return a subgraph:
    - nodes: each with id, name, file, summary, code, ppr_score, vector_score
    - edges: all edges that exist between selected nodes in the full graph
    """
    selected_set = set(selected_indices)

    nodes = []
    for idx in selected_indices:
        v = G.vs[idx]
        node_data = next((n for n in nodes_data if n["id"] == v["id"]), {})
        nodes.append({
            "id": v["id"],
            "name": v["name"],
            "file": v["file"],
            "summary": node_data.get("text_summary", ""),
            "code": node_data.get("code", ""),
            "ppr_score": scores.get("ppr", {}).get(idx, 0.0),
            "vector_score": scores.get("vector", {}).get(idx, 0.0),
            "community_id": v.get("cluster_id", -1),
        })

    edges = []
    for edge in G.es:
        if edge.source in selected_set and edge.target in selected_set:
            edges.append({
                "source": G.vs[edge.source]["id"],
                "target": G.vs[edge.target]["id"],
                "type": edge["type"],
                "co_change_category": edge.get("category", ""),
                "co_change_count": edge.get("co_change_count", 0),
            })

    return {
        "query": query,
        "nodes": nodes,
        "edges": edges,
    }
```

**Validation for Phase 3:**
For query "memory ingestion pipeline hook processing":
- `ingest_hook_payload` must appear in nodes.
- CALLS edges between `ingest_hook_payload` → `_validate_payload`, `memory_write` must appear in edges if those nodes are also selected.
- Output must be valid JSON.
- Edge list must only contain edges where both endpoints are in the selected node set.

---

## Constraints

1. **No API calls during validation runs unless necessary.** Use cached query
   embeddings where possible. Only embed new queries.

2. **Infomap runs on CALLS-only subgraph always.** Never run Infomap on the full
   graph including CO_CHANGE. CO_CHANGE at 437 edges would distort community
   detection away from structural module boundaries.

3. **PPR for Mode B runs on CALLS-only subgraph.** For Mode C (theme overlay),
   PPR runs on full graph with theme-boosted weights. This is the correct
   separation — Infomap defines structure, theme overlay affects traversal cost.

4. **Hub penalty stays in place.** `log(degree+1)^1.5` on nodes in top 5% by
   CALLS degree. Do not remove it. If god-nodes creep back after Phase 1,
   raise the exponent before considering removing the penalty.

5. **eval set is the 10 queries in `query_rank_eval.json`.** Do not add queries
   during Phase 1 or 2. Add cross-cutting queries only after Phase 2 is stable,
   for the overlapping-communities decision.

6. **Both igraph_sandbox.py and cochange_ablation.py must stay consistent.**
   Any change to the reset vector construction applies to both files. They share
   the same algorithm — diverging implementations will make ablation results
   unreliable.

---

## Failure modes and what to do

| Symptom | Diagnosis | Action |
|---------|-----------|--------|
| Phase 1 fixes misses but god-nodes increase | Soft reset opened too many communities | Raise hub penalty exponent to 2.0, rerun |
| Phase 1 fixes misses but previously-passing queries regress | Proportional weighting pulling wrong community mass | Check which community is contaminating. Consider `w^0.5` decay instead of linear |
| Phase 2 theme overlay breaks memory_search again | Theme boost on CO_CHANGE edges is over-weighting wrong theme | Print top-5 theme scores for that query. Check if any high-scoring theme has CO_CHANGE edges that shortcut into wrong community |
| Theme overlay HIT@10 < Phase 1 HIT@10 | Theme overlay net negative | Do not integrate. Keep Phase 1 result and investigate why |
| Subgraph edges are empty | Selected nodes not connected to each other in the graph | Check if MMR is selecting nodes from completely different communities. If so, lower MMR lambda (currently 0.6) to weight diversity less |

---

## What success looks like at end of Phase 3

- Mode B (fixed PPR) >= 5/10 HIT@10
- Mode C (theme overlay on fixed PPR) >= 5/10 HIT@10, ideally 6/10
- God-nodes (`close`, `make_settings`, `init_db`, `health_ping`) not in top-3
  for any query where they don't belong
- `run_cold_discovery` returns a structured subgraph JSON (nodes + edges)
- All 10 eval queries produce valid JSON output
- Results are reproducible: same query same result on reruns (Infomap is
  non-deterministic — seed it with a fixed random seed)

---

## Open questions (not blocking Phase 1-3, decide later)

1. **Overlapping communities** — Need 2-3 cross-cutting queries added to eval
   before this can be decided. Not blocking Phase 1-3.

2. **GraphSAGE reranking** — Layer on after Phase 3. The structural k-NN
   approach only helps once the base pipeline is solid.

3. **Infomap non-determinism** — `calls_only.community_infomap()` can return
   different community assignments on different runs (randomized algorithm).
   Currently called inside `run_cold_discovery` on every query. Should be moved
   outside and run once at startup with a fixed seed. Not blocking but affects
   reproducibility.

4. **Test functions in results** — Current top-10 results frequently contain
   test functions (`test_codex_install_applies_managed_hooks_and_mcp`,
   `test_ingest_and_search`). These are legitimate nodes in the graph but likely
   not what an agent wants. Consider adding a `status == "active" and not
   name.startswith("test_")` filter in the PPR candidate list.

---

## Files that will be modified

| File | Phase | Change |
|------|-------|--------|
| `sandbox/igraph_sandbox.py` | 0a | Hoist Infomap to startup, fixed seed |
| `sandbox/cochange_ablation.py` | 0a | Same Infomap hoist |
| `sandbox/igraph_sandbox.py` | 0b | `_is_candidate` filter, exclude test functions from results |
| `sandbox/cochange_ablation.py` | 0b | Same candidate filter |
| `sandbox/igraph_sandbox.py` | 0c | Hub penalty exponent tuning |
| `sandbox/cochange_ablation.py` | 0c | Same hub penalty tuning |
| `sandbox/igraph_sandbox.py` | 1 | Soft reset vector, soft cluster filter |
| `sandbox/cochange_ablation.py` | 1 | Same reset vector change in `_run_retrieval` |
| `sandbox/igraph_sandbox.py` | 2 | Add `_theme_boosted_weights`, `use_theme_overlay` param |
| `sandbox/igraph_sandbox.py` | 3 | Add `build_subgraph_output` with consumer mode param |
| `sandbox/RETRIEVAL_ITERATION_SPEC.md` | ongoing | Update results after each phase |

**Files that will NOT be modified:**
- `sandbox/cochange_analysis.py`
- `sandbox/amo_nodes.json`, `amo_edges.json`, `amo_cochange_*.json`
- `sandbox/query_rank_eval.json`
- Anything outside `sandbox/`
