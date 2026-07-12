# Retrieval Architecture Experiments

**Created:** 2026-07-12  
**Purpose:** Spec-driven experiments to find what actually works before committing to a final architecture.  
**Rule:** Every experiment has a written hypothesis before running. Result is recorded regardless of outcome. No coding until hypothesis is written.

---

## Context: What We Know So Far (Measured, Not Assumed)

### Current pipeline (igraph_sandbox.py)
```
query → embed → cosine top-20 seeds → PPR (random walk, CALLS graph) → cluster filter → MMR → subgraph JSON
```

### Measured scores on 13 queries
| Mode | HIT@10 |
|------|--------|
| Vector only | 7/13 |
| CALLS PPR (k=20) | 7/13 |
| Theme overlay PPR (k=15) | 7/13 |

### What the data showed about each component

**Vector search:** Works well. When the answer is semantically obvious, vector rank 1-3. Correct baseline.

**PPR (random walk):** 5-query test showed: PPR helped 0/5, hurt 1/5, neutral 4/5. Root cause: random walk accumulates mass in structural hubs (memory_write, append, close) regardless of query. Hub penalty helps but doesn't eliminate this. PPR is correct for post-anchor neighborhood exploration, wrong as pre-anchor query reranker.

**Cluster filter:** Hard-removes any function not in a community that had at least 1 seed. Was causing export_snapshot miss (cluster 33, 0 seeds). Softening it (Option A') caused god-node flooding. Hard filter is necessary but placement is wrong.

**MMR:** Wrong for code. Penalizes semantically similar functions that are distinct code entities an agent needs. Sometimes accidentally rescues PPR-demoted functions (Q3 ingest_hook_payload recovered via MMR diversity selection). Not a reliable mechanism.

**CO_CHANGE theme overlay:** Correct concept — semantically conditions edge weights so PPR can cross community boundaries. Works for redact_secrets (vector rank 9, PPR rank 3 with theme overlay). But still subject to PPR's hub-accumulation problem.

**k=20 seeds:** Cleanly better than k=15 for PPR mode. Zero regressions. export_snapshot and apply_install_plan recover. Adopted.

### What Graphify does (reference)
- Build: Tree-sitter AST + LLM semantics → NetworkX graph → Leiden clustering
- Query: Vector/name match to find seeds → BFS/DFS outward → return subgraph within token budget
- Clustering role: build-time only — orients agent, labels surprises, not a runtime filter

---

## The Question Stack

These are the open questions this experiment set will answer, in dependency order. Lower numbers must be answered before higher numbers can be properly tested.

**Q1:** Is BFS/DFS from vector seeds better than PPR for retrieval? (Core architecture question)

**Q2:** How deep should BFS go? At what depth does it start adding noise?

**Q3:** Does clustering (community labels) help BFS decide where to stop or what to include?

**Q4:** Can CO_CHANGE edges be used as explicit bridges (not PPR edge weights) to cross community boundaries?

**Q5:** Is there any query type where PPR is actually better than BFS? (When does PPR add value?)

**Q6:** Does MMR on vector scores help or hurt compared to just top-k? (Is any diversity selection needed?)

**Q7:** What is the right output format — ranked list vs subgraph? Does subgraph help an agent more?

---

## Experiment 1: BFS vs PPR — Core Architecture

### Hypothesis
BFS from top-5 vector seeds (2 hops in CALLS graph) will match or beat PPR on HIT@10 across the 13-query eval, without god-node contamination and without cluster filter side effects.

**Why I believe this:** 5-query test showed PPR helped 0/5. Graphify (production system solving the same problem) uses BFS not PPR. BFS is anchored to the semantic hits — it expands outward from what vector already found correctly. PPR disperses mass through the whole graph structure.

### Expected outcome
- BFS HIT@10 >= PPR HIT@10 (7/13)
- God-node frequency (close, make_settings, append): near zero — BFS from semantic seeds won't reach distant utility functions in 2 hops
- redact_secrets: may MISS (it's not in the CALLS neighborhood of the ingest seeds — needs CO_CHANGE bridge, tested separately in Exp 4)
- export_snapshot: may MISS (cluster 33, not directly reachable via CALLS from any seed)

### What "2 hops BFS" means
From each seed: include the seed itself + all nodes it calls (hop 1) + all nodes those call (hop 2). Cap at N total nodes (budget). Do NOT include callers (in-edges) — that's blast radius, not context.

Actually reconsider: for "find the entry point" queries, callers matter more than callees. Try both directions.

**Two variants to test:**
- BFS-out: seed → what it calls → what those call (good for understanding what a function does)
- BFS-in: seed → what calls it → what calls those (good for blast radius / entry point discovery)
- BFS-both: both directions, 1 hop each (balanced context)

### Fallback
If BFS scores LOWER than PPR, diagnose which queries regressed. If the regressed queries are cases where PPR found something via structural walk that BFS missed, keep PPR for those query types and use BFS for others.

### How to measure
Run `cochange_ablation.py` pattern but replace `_run_retrieval` with three BFS variants. Compare HIT@10 on same 13 queries. Run god-node frequency check.

---

## Experiment 2: BFS Depth Tuning

### Prerequisite
Experiment 1 must show BFS >= PPR, otherwise depth tuning is irrelevant.

### Hypothesis
Depth 1 is too shallow (misses relevant callees). Depth 2 is correct. Depth 3+ introduces noise faster than it recovers misses.

**Why:** Code call graphs are usually shallow — an entry point calls 3-5 things, each of those calls 3-5 things. By depth 2 you've covered the functional territory. Depth 3 reaches generic utilities (json.dumps, logging) that aren't useful context.

### Expected outcome
- Depth 1: lower HIT@10 than depth 2
- Depth 2: optimal
- Depth 3: same or lower HIT@10 as depth 2, more god-node contamination
- Node budget: 10-15 nodes total is probably right (same as current k=10 final output)

### Fallback
If depth 2 has more god-nodes than depth 1 with no HIT improvement, use depth 1 + community-boundary stopping (Exp 3).

---

## Experiment 3: Clustering as BFS Boundary

### Prerequisite
Experiments 1 and 2 done.

### Hypothesis
BFS that stops expanding at community boundaries (includes the cross-boundary node but doesn't expand through it) will give cleaner results than BFS that ignores communities, with equal or better HIT@10.

**Why:** A function in a completely different Infomap community from all seeds is structurally distant. Including it in results but not expanding through it prevents the BFS from drifting into unrelated modules. This is the Graphify "surprising connections" insight — cross-community edges are notable but shouldn't be expansion origins.

### Implementation
```python
def bfs_with_community_boundary(seeds, max_nodes=15, depth=2):
    seed_communities = {G.vs[i]["cluster_id"] for i in seeds}
    
    visited = set(seeds)
    result = list(seeds)
    queue = [(i, 0) for i in seeds]  # (node_idx, current_depth)
    
    while queue and len(result) < max_nodes:
        current, d = queue.pop(0)
        if d >= depth:
            continue
        for neighbor in get_out_neighbors_calls(current):
            if neighbor not in visited:
                visited.add(neighbor)
                result.append(neighbor)
                neighbor_community = G.vs[neighbor]["cluster_id"]
                if neighbor_community in seed_communities:
                    queue.append((neighbor, d + 1))  # expand further
                # else: cross-community — include but don't expand
    return result
```

### Expected outcome
- HIT@10 same or better than unconstrained BFS
- God-node contamination lower (utility functions in distant communities don't get pulled in)
- Cross-community nodes (like redact_secrets) appear in results when BFS reaches them via CO_CHANGE bridges (Exp 4) but don't cause their whole community to flood in

### Fallback
If community-boundary BFS misses things that unconstrained BFS found, the communities are too tight. Use unconstrained BFS but increase budget.

---

## Experiment 4: CO_CHANGE as Explicit Bridge (Not PPR Weight)

### Prerequisite
Experiment 1 done. Best BFS variant identified.

### Hypothesis
Explicitly checking CO_CHANGE edges from seeds (with theme scoring against the query) will recover cross-module functions (like redact_secrets) that BFS on CALLS graph misses, without the hub-accumulation problem of PPR theme overlay.

**Why:** redact_secrets fails in BFS because there's no CALLS path from the ingest seeds to privacy.py. But there IS a CO_CHANGE edge. The theme on that edge scores high against "remove sensitive data" queries. So: for each seed, check its CO_CHANGE neighbors, score the themes, if score > threshold include that neighbor.

### Implementation
```python
def cochange_bridges(seed_idx, query_vec, threshold=0.3):
    """Return CO_CHANGE neighbors of seed where theme relevance > threshold."""
    bridges = []
    for edge in G.es:
        if (edge.source == seed_idx or edge.target == seed_idx) and edge["type"] == "CO_CHANGE":
            neighbor_idx = edge.target if edge.source == seed_idx else edge.source
            theme_props = edge["theme_proportions"] or {}
            boost = sum(
                prop * cosine(query_vec, _theme_vec_map.get(tid, zeros))
                for tid, prop in theme_props.items()
            )
            if boost > threshold:
                bridges.append((neighbor_idx, boost))
    return sorted(bridges, key=lambda x: x[1], reverse=True)
```

Run this for all top-5 seeds. Collect all bridges with boost > threshold. Add them to the BFS result set.

### Expected outcome
- redact_secrets: should recover (it has CO_CHANGE edge to extract_memories_for_chunk, theme score should be high for privacy/cleaning queries)
- add_memory_unit: may recover (depends on whether its CO_CHANGE edges have relevant theme scores for the query)
- No god-node contamination (only semantically relevant CO_CHANGE neighbors are included, not all CO_CHANGE neighbors)
- HIT@10: 7/13 → potentially 8/13 or 9/13

### Verification steps before coding
1. Check: does extract_memories_for_chunk have a CO_CHANGE edge to redact_secrets?
2. Check: does the theme on that edge score high against "remove sensitive data before persisting"?
3. If yes to both: mechanism is sound, implement.
4. If no: different mechanism, diagnose before proceeding.

### Fallback
If CO_CHANGE bridges bring in too many unrelated nodes (threshold too low), raise threshold. If threshold at 0.5+ still doesn't recover the target, the CO_CHANGE data for this specific pair doesn't have enough theme signal. Accept as a miss for this query type.

---

## Experiment 5: PPR Post-Anchor (Correct Use of PPR)

### This is NOT a retrieval experiment. It tests a different tool.

### Hypothesis
PPR anchored to a single KNOWN function (not a query) correctly identifies the functional territory of that function — the pipeline it's part of, what it depends on downstream.

**Why:** The 5-query test was about using PPR to FIND functions. This is about using PPR to UNDERSTAND a function already found. Different question, different answer.

### Test
Take `ingest_hook_payload` as the single seed (reset weight = 1.0). Run PPR. Check: do the top-10 PPR-scored functions represent the actual ingest pipeline (`normalize_event_payload`, `session_exists`, `add_event`, `extract_memories_for_chunk`)?

**Expected:** Yes. This is the correct use of PPR. The result becomes the "neighborhood" of the function — useful for an agent that found it and wants to understand what it does.

**If yes:** Expose this as `explore_function_neighborhood(func_id)` tool. Do not mix with query-time retrieval.

**If no:** The CALLS graph doesn't flow in the right direction, or hub accumulation still dominates. Diagnose.

---

## Experiment 6: MMR vs No-MMR vs File-Dedup

### Hypothesis
Simple file-level deduplication (don't return more than 2 functions from the same file) performs better than MMR as a diversity mechanism for code retrieval.

**Why you said MMR is wrong:** "In code repos each function is distinct, so we can't actually remove any function from the scoring." MMR's cosine-based penalty removes semantically similar functions that are genuinely distinct code entities. File-dedup removes actual near-duplicates (same file, same purpose) while keeping semantically similar but structurally distinct functions.

### Test
Three variants on same 13 queries:
- No diversity (straight top-k by score)
- MMR (current, lambda=0.6)
- File-dedup (max 2 per file, fill remaining with next-best by score)

### Expected outcome
- File-dedup >= MMR on HIT@10
- File-dedup avoids the cases where MMR kicked out extract_memory_candidates (vector rank 1, MMR displaced it)
- No-diversity probably also >= MMR since top-k already finds the right answer in most cases

### Fallback
If no-diversity and file-dedup produce the same results, just use no-diversity (simpler). If file-dedup hurts something specific, diagnose that case.

---

## Experiment 7: Query Type Classification

### Only run after Experiments 1-4 establish the base architecture.

### Hypothesis
Different query types benefit from different retrieval strategies. A single pipeline is suboptimal for all of them.

**Query type taxonomy:**
1. **Direct/lexical:** query contains the function name or close synonym. Vector rank 1-2. Best: pure vector, no expansion.
2. **Architectural:** want the entry point + its pipeline. Best: BFS-out from vector seed.
3. **Cross-module:** answer not lexically obvious, lives in different community from lexical matches. Best: BFS + CO_CHANGE bridges.
4. **Relationship:** "why do X and Y change together." Best: `explain_coupling` directly, not retrieval pipeline.
5. **Review/blast radius:** "what's affected if I change X." Best: `trace_blast_radius` (HelixDB) + PPR post-anchor.

### How to classify at runtime
Simple heuristic first: if top vector seed cosine score > 0.55, it's a direct query (trust vector, skip expansion). If < 0.55, it needs structural expansion. Measure: does this threshold correctly classify the 13 queries?

### Expected outcome
- Direct queries (memory_search, tool_contracts, rebuild_indexes): vector score > 0.55, skip BFS, return vector top-k directly
- Architectural queries (ingest_hook_payload, memory_context_pack): score 0.35-0.55, run BFS
- Cross-module queries (redact_secrets): score < 0.45, run BFS + CO_CHANGE bridges

---

## Scoring Standard

Every experiment runs against all 13 queries in `query_rank_eval.json`.

**Metrics:**
1. HIT@10: is the target in the top-10 returned nodes?
2. Target rank: exact rank of target (not just hit/miss)
3. God-node count: how many of {close, make_settings, init_db, health_ping, add_event} appear in top-5 across all queries (should be 0)
4. Test function count: how many test_ functions in results (should be 0)

**Acceptance for any change:**
- HIT@10 >= previous baseline
- No regression on any currently-passing query
- God-node count = 0

**Two-sided check is non-negotiable.** Before running any experiment: write which queries are expected to improve and which must not regress.

---

## Result Log

### Experiment 1: BFS vs PPR — 2026-07-12

**Change:** Replaced PPR+MMR with three BFS variants (out, in, both) from top-5 vector seeds, depth=2.

**Expected:** BFS >= PPR (7/13). Expected vector = 7/13 (wrong — actual baseline was 10/13 on this run, query embeddings varied slightly from earlier ablation).

**Actual results:**

| Mode | HIT@13 | God-nodes total |
|------|--------|-----------------|
| Vector only | 10/13 | 2 |
| PPR (current) | 7/13 | 5 |
| BFS-out 2-hop | 10/13 | 10 |
| BFS-in 2-hop | 9/13 | 3 |
| BFS-both 1-hop | 10/13 | 9 |

**Key findings:**
1. BFS-out: matches vector on HIT@10 but adds 10 god-nodes (append, health_ping, etc.) — following callees leads to generic utilities
2. BFS-in: cleanest at 3 god-nodes, 9/13 HIT — following callers of the seed is cleaner but misses some queries
3. BFS-both: 10/13 but 9 god-nodes — same contamination as BFS-out
4. **Vector alone is already 10/13 with only 2 god-nodes** — BFS matches vector but doesn't improve it
5. PPR uniquely finds: export_snapshot (rank 9), apply_install_plan (rank 2) — both boundary-of-seed-window cases that k=20 already handles
6. BFS uniquely finds: ambiguous_capture_persist (BFS-out rank 8, BFS-both rank 10) — because `ingest_hook_payload` is 2 hops out from codex hook functions via CALLS

**Verdict: Don't replace PPR with BFS. Outcome is more nuanced.**

- Vector alone (10/13) is already the best single-mode retrieval
- BFS-out adds contamination without net improvement
- BFS-in (9/13) is cleanest but regresses vs vector on some queries
- PPR adds value for boundary-of-seed-window cases (export_snapshot, apply_install_plan) — but k=20 already handles those
- The real finding: most queries are already solved by vector. The remaining misses (redact_secrets, _filter_answer_grade_nodes, apply_install_plan, export_snapshot) need different mechanisms — CO_CHANGE bridge for redact_secrets, k=20 for the boundary cases

**Next: Experiment 4 (CO_CHANGE bridges) to handle redact_secrets. Experiment 3 (clustering as BFS boundary) to reduce BFS-out god-node count.**

---

### [Experiment 2 — fill in after running]

---

## Files to Modify

Only these files. No others.

| File | Purpose |
|------|---------|
| `sandbox/igraph_sandbox.py` | Main pipeline — replace PPR+MMR with BFS variants |
| `sandbox/cochange_ablation.py` | Ablation runner — add new modes |
| `sandbox/query_rank_eval.json` | Eval set — add new queries if needed (write expected answers first) |
| `sandbox/ablation_results.json` | Updated after each ablation run |
| `RETRIEVAL_ARCH_EXPERIMENTS.md` | This file — update Result Log after each experiment |
| `PIPELINE_SPEC.md` | Update settled decisions table when experiments confirm architecture |

**Do not modify:**
- `tools/graph_tools.py` — don't touch the MCP interface until architecture is settled
- `tools/graph_mcp_server.py` — same
- `pipeline_api.py` — same
- Any HelixDB tools — they work, leave them alone

---

## Order of Execution

```
Exp 1 (BFS vs PPR) 
  → if BFS wins: Exp 2 (depth tuning)
      → Exp 3 (clustering as boundary)
          → Exp 4 (CO_CHANGE bridges)
              → Exp 6 (MMR vs file-dedup)
                  → Exp 7 (query type classification)
  → if PPR wins or ties: diagnose which queries PPR does better on, then Exp 5 (PPR post-anchor)
  
Exp 5 (PPR post-anchor) runs independently of the main chain — it's testing a different tool
```

Total expected time for Exp 1-4: 4-6 hours of coding + measurement. Each experiment is a contained script, not a full refactor.

---

## What This Is NOT

- Not a full rewrite of the pipeline
- Not production-ready code
- Not a decision on HelixDB migration
- Not a decision on overlapping Infomap
- Not a decision on LLM summaries

All of those decisions wait until the retrieval algorithm is settled by measurement.
