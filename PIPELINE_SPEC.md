# Graph-Native Code Intelligence — Pipeline Spec & Iteration Log

**Last updated:** 2026-07-12  
**Status:** Active. Update this doc after every experiment, every decision, every result.  
**Purpose:** Any future agent or engineer picking this up should be able to read this doc and know exactly what has been tried, what was measured, what worked, what didn't, and what to do next — without needing to read the conversation history.

---

## How to use this document

This is not a roadmap. It is a living engineering record.

- **Before any change:** Write what you expect to happen (specific queries expected to improve, specific queries that must not regress). If you cannot write this, do not make the change yet — you don't have a hypothesis.
- **After any change:** Record the actual result. If it matches the expectation, move on. If it doesn't, write a one-paragraph diagnosis before touching more code.
- **Never build on unverified assumptions.** If a claim says "probably" or "likely" without a number, run the script that produces the number before coding against that claim.

---

## The Layered Testing Discipline

Tests must be run in this order. Never skip a layer.

### Layer 1 — Foundation (is the measurement substrate correct?)

This layer is boring. It is also where most real bugs in this project have lived. Before trusting any algorithm result, verify:

1. **Determinism** — identical inputs produce identical outputs across runs. Non-deterministic algorithms (Infomap) must be seeded at startup, not per-query.
2. **Candidate pool cleanliness** — test functions, superseded nodes, unembedded nodes must be filtered before scoring. Contamination distorts every number built on top.
3. **Edge direction assumptions** — directed vs undirected matters for every PPR claim. Verify in code, not by reading docs.
4. **Dedup correctness** — `code_hash` dedup in the ingestor is not yet implemented. Every function in a touched file gets a new `FunctionState` even if code didn't change. This inflated CO_CHANGE to 489k edges. The co-change pipeline now filters this down to 437 real edges, but the root cause in `scalable_ingest.py` is unfixed.
5. **Baseline reproducibility** — run the ablation twice without changes. Results must be identical. If they differ, fix that first.

**Foundation layer is never "done."** Each new data artifact or algorithm component needs its own foundation check before being measured.

### Layer 2 — Algorithm (does a named change fix a named failure?)

A change is only justified when:
- A specific, measured failure mode has been diagnosed (not just observed)
- The failure mode points at exactly one mechanism
- A change to that mechanism has a falsifiable expected outcome

Do not test "let's try Leiden instead of Infomap" without a specific case where Infomap's behavior has been diagnosed as the root cause of a specific miss.

**Template for any algorithm experiment:**
```
Change:    [what code changes]
Justified by:   [specific diagnostic finding with script name and number]
Expects to fix: [specific queries by ID, specific mechanism]
Must not break: [specific queries by ID that currently pass]
Fallback:  [what to revert to if it fails]
```

### Layer 3 — Product (does the tool make an agent do better work?)

HIT@10 on a 10-query eval is a proxy. It measures whether the pipeline returns what you already know the answer should be. It does not measure whether a real agent, using this tool, makes better decisions or does less wasted work. That is the only measurement that actually answers "is this a product."

**This layer has not happened yet.** It requires:
1. A working tool interface (pipeline wired into MCP server — not yet done)
2. A real agent task with a defined correct outcome
3. A baseline (agent without the tool) and a treatment (agent with the tool)
4. Measurement of outcome quality, not retrieval score

---

## Current Measured State (as of 2026-07-12)

### What exists and works

**HelixDB tools (`tools/graph_tools.py`) — validated on auth module (10 functions)**

| Tool | What it does | Latency | Validated |
|------|-------------|---------|-----------|
| `search_code_semantics(prompt, k)` | Natural language → active functions via vector search | ~200ms (API) + <5ms (HelixDB) | ✅ auth module |
| `get_code_time_travel_diff(state_id)` | Current vs previous version of a function | <5ms | ✅ auth module |
| `trace_blast_radius(func_id, depth)` | Who calls this function, N hops, single Rust query | 4ms (8.4x faster than N+1) | ✅ auth module |
| `get_temporal_vulnerability_trace(func, ts)` | Callers that predate a security fix | 4.2ms (6.3x faster) | ✅ auth module |
| `edit_code(file, func, new_code)` | Patch function on disk + re-ingest | varies | ✅ auth module |

**Critical gap:** None of these tools have been validated against AMO. All AMO work is in igraph/numpy sandbox, not HelixDB.

**Sandbox retrieval pipeline (`sandbox/igraph_sandbox.py`) — validated on AMO (1069 candidates)**

Current pipeline: vector seeds → Infomap community scope (fixed seed, startup) → PPR (soft proportional reset, full graph with theme-boosted CO_CHANGE weights) → MMR → subgraph JSON output.

| Mode | HIT@10 | Notes |
|------|--------|-------|
| Vector only | 6/10 | Fast baseline |
| CALLS PPR (no theme) | 5/10 | Soft reset + hub penalty fix in place |
| Theme overlay (PPR + CO_CHANGE) | 6/10 | Matches vector; adds `redact_secrets` recovery |

Source: `sandbox/ablation_results.json`. Run with fixed seed, test functions filtered, hub penalty exponent = 2.0.

### Known misses — diagnosed, not just observed

Each miss has a specific mechanism. Do not attempt to fix a miss without re-reading its diagnosis.

| Query ID | Target | Raw vector rank | Mechanism | Fix | Priority |
|----------|--------|-----------------|-----------|-----|----------|
| `old_snapshot` | `export_snapshot` | **15/1069** (score 0.336) | PPR dilution: rank-15 seed gets proportional weight 1/15=0.067, weak call neighborhood (_json, _rows_for_export), PPR mass dissipates. NOT an embedding failure. | Seed floor weighting | High — diagnosed, fix queued |
| `graph_context_filter` | `_filter_answer_grade_nodes` | 2/1069 | Partially diagnosed. Vector rank is fine (rank 2). PPR fails — seed `rebuild_graph_cache` has high PPR score but directed PPR may not amplify its callees sufficiently. Directed graph confirmed (source=caller, target=callee, PPR follows out-edges). Whether seed dilution is the mechanism needs one more diagnostic check. | Check if `rebuild_graph_cache` is a dilution victim. If yes, seed floor weighting covers it. | Medium — needs one diagnostic script before coding |
| `install_hooks` | `apply_install_plan` | MISS in all modes | Graph connectivity: function is barely called outside test functions. Not a pipeline bug. | Accept as permanent miss OR add non-test callers to graph — a codebase fact, not a retrieval problem. | Low — likely accept |
| `ambiguous_capture_persist` | `ingest_hook_payload` | MISS in all modes | Correct miss. Query "codex hook captured user message should become durable memory evidence" does not describe `ingest_hook_payload` in any lexical or structural way. The query is wrong, not the system. | Nothing. Accept. | Accepted |

### Known gaps in the foundation

| Gap | Impact | Status |
|-----|--------|--------|
| `code_hash` dedup not in `scalable_ingest.py` | Every function in a touched file gets a new FunctionState even if code didn't change. Co-change pipeline works around this (Jaccard filter) but root cause unfixed. | Unfixed |
| AMO never ingested into HelixDB | All AMO work is JSON+igraph. HelixDB tools untested on real codebase. | Unfixed |
| CO_CHANGE edges, community IDs, GraphSAGE embeddings not in HelixDB | Pipeline disconnected from tool interface. | Unfixed |
| Eval set has 10 queries, all tuned against | Overfit risk. No held-out queries. No cross-cutting queries (bridge functions). Overlapping communities decision blocked until bridge queries exist. | Partially addressed — cross-cutting queries pending |

---

## Next Steps — Ordered, Evidenced

### Step 1: Seed floor weighting (queued, evidence exists)

**Justified by:** `export_snapshot` diagnostic. Raw cosine rank = 15, score = 0.336. Proportional weight in reset vector = 0.067. PPR mass insufficient to overcome weak call neighborhood.

**Change:** Add a floor so all top-k seeds get at least `FLOOR × (1/k)` teleportation weight, regardless of cosine score.

```python
# In igraph_sandbox.py and cochange_ablation.py, reset vector construction:
SEED_FLOOR = 0.3  # every seed gets at least 30% of uniform weight

total_seeds = len(top_seeds)
uniform_weight = 1.0 / total_seeds

reset_vector = np.zeros(G.vcount())
for idx, score in top_seeds:
    cid = G.vs[idx]["cluster_id"]
    proportional = community_weight.get(cid, 0.0) * max(score, 0.0)
    floor = SEED_FLOOR * uniform_weight
    reset_vector[idx] = max(proportional, floor)
```

**Expected to fix:** `export_snapshot` (old_snapshot query).  
**Must not break:** `memory_search` (rank 1 in both modes), `tool_contracts` (rank 1), `redact_secrets` (rank 3), `memory_metrics`/`rebuild_indexes` passing queries.  
**Fallback:** Revert `SEED_FLOOR` to 0.0 (identical to current behavior).

**Run:** `sandbox/cochange_ablation.py` → compare ablation_results.json before/after.  
**Record result here after running.**

---

### Step 2: Diagnose `_filter_answer_grade_nodes` before coding

**Do not code yet.** Run one diagnostic script first.

Question: for the `graph_context_filter` query, what is `rebuild_graph_cache`'s cosine rank, and what is its proportional weight in the reset vector? Is it in the top-15 seeds?

If `rebuild_graph_cache` is a seed with adequate weight AND directed PPR follows its out-edges toward `_filter_answer_grade_nodes`, but `_filter_answer_grade_nodes` still doesn't surface — that's a different mechanism than dilution (possibly: too many outgoing CALLS from `rebuild_graph_cache`, each getting only a small fraction of PPR mass).

Script to write: load graph, embed the query "drop noisy answer grade nodes from current graph context results", score all candidates, report rank and weight of `rebuild_graph_cache`, then simulate what fraction of its PPR mass flows to `_filter_answer_grade_nodes`.

**Only after this number exists** does it become clear whether Step 1's floor weighting also covers this miss.

---

### Step 3: Add cross-cutting queries to eval set

**Why before anything else algorithmic:** The overlapping-communities question has been deferred since the beginning of this project on the claim that "we need a bridge-function case to test it." Until this step happens, that decision can never be made. The current 10-query eval only tests module-internal retrieval — every target function lives clearly inside one Infomap community. That's not representative of real agent queries.

**What to add:** 2-3 queries where the target function architecturally bridges two modules. Write the expected answer BEFORE running. Do not add queries you've already looked at in the data.

**Format:** add to `sandbox/query_rank_eval.json` with same schema as existing entries.

**What to measure:** do bridge-function targets miss in Mode B (CALLS PPR) but appear in Mode A (vector)? If yes — overlapping communities is worth testing. If no — the current hard-partition Infomap is adequate and the question is closed.

---

### Step 4: Wire pipeline into MCP tool interface

**Prerequisite for any product test.** Right now `igraph_sandbox.py` and `tools/graph_tools.py` are disconnected. A coding agent calling `search_code_semantics` gets raw HelixDB vector search — no PPR, no community scoping, no theme overlay, no subgraph edges.

**What needs to change:**

1. Make `igraph_sandbox.py` importable as a module. Extract startup into `initialize(data_root)`. Expose `run_cold_discovery` and `build_subgraph_output` as a clean API.

2. In `tools/graph_tools.py`, replace `search_code_semantics` body:
   ```python
   # Currently: raw HelixDB vector search
   # Replace with: igraph pipeline call
   from sandbox.igraph_sandbox import initialize, run_cold_discovery, build_subgraph_output
   
   # At server startup (graph_mcp_server.py):
   initialize(data_root=REPO_ROOT)
   
   # In search_code_semantics:
   vec = _embed(prompt)
   selected, ppr_scores, vec_scores = run_cold_discovery(vec, final_k=k)
   return build_subgraph_output(selected, prompt, ppr_scores, vec_scores)
   ```

3. The raw HelixDB `search_code_semantics` can stay as `search_code_semantics_helix` for comparison.

**This step does not change any algorithm.** It is plumbing. Do not mix algorithm changes with plumbing changes in the same commit.

---

### Step 5: The product test (nothing else matters until this happens)

**Task definition:** Choose one real coding task that an agent would plausibly run against AMO. Example: "Find where session snapshots are exported, understand what co-changes with it, and explain why `memory_export` in tools.py always moves with the snapshot pipeline."

**Baseline condition:** Agent has access to grep, file read, standard tools. No graph tool.  
**Treatment condition:** Same agent, same task, with `search_code_semantics` (advanced pipeline), `trace_blast_radius`, `explain_coupling`.

**Measure:**
- Number of tool calls to reach correct answer
- Whether the agent correctly identifies the co-change relationship
- Whether the agent hallucinates function names or file paths that don't exist

**This is the only measurement that answers whether any of this is a product.** HIT@10 on 10 known queries is a pipeline health metric, not a product metric.

---

## Algorithm Decisions Log — Settled and Deferred

### Settled decisions (do not re-litigate without new evidence)

| Decision | What was decided | Evidence | Date |
|----------|-----------------|----------|------|
| Infomap over Leiden | Infomap for CALLS community detection. Leiden has resolution limit for small tight modules. No case where Infomap behavior was diagnosed as root cause of a miss. | No specific miss attributed to Infomap's algorithm choice vs Leiden's | 2026-07-11 |
| CALLS-only for Infomap | CO_CHANGE and IMPORTS excluded from community detection. At 437 CO_CHANGE edges they would distort structural module boundaries. Infomap is for structural flow, not historical coupling. | Validated: 502 communities on CALLS-only, meaningful module structure | 2026-07-11 |
| Hub penalty exponent = 2.0 | Raised from 1.5 after god-nodes (`close`, `make_settings`, `add_event`) appeared in 5/10 queries. At 2.0: same god-nodes appear in ≤1/10 queries. | `sandbox/_count_test_contamination.py` measurements, before/after | 2026-07-11 |
| Soft proportional community weighting | Replaced binary dominant-cluster mask with proportional weighting (k/total_seeds per community). Minority communities (1-2 seeds) get small but non-zero teleportation mass. | Phase 1 fix. PPR score 3→5/10. | 2026-07-11 |
| Test functions excluded from candidate pool | `_is_candidate` filters by function name prefix AND file name prefix. Test functions remain in graph (can carry PPR mass) but are not returned as results. | Reduced test contamination from 33% to 8% of result slots. | 2026-07-11 |
| Theme overlay on full graph, PPR | Theme overlay (CO_CHANGE edge cost reduction) runs PPR on full graph. Infomap community detection still runs on CALLS-only. These are separate: structure vs traversal cost. | Validated: `redact_secrets` recovered at rank 3 only with theme overlay. Without: MISS. | 2026-07-11 |
| Consumer policy mode on subgraph edges | `temporal_burst` CO_CHANGE edges excluded from `general_retrieval` output. One-time refactor events are misleading context for an agent. | `cochange_consumer_policy` function in `cochange_analysis.py` | 2026-07-11 |
| `ingest_hook_payload` (ambiguous query) accepted as permanent miss | Query "codex hook captured user message should become durable memory evidence" does not describe the function in any recoverable way. Query is wrong, not the system. | Vector rank: MISS in all modes. No lexical or structural path from query to function. | 2026-07-11 |
| `export_snapshot` is PPR dilution, not embedding failure | Raw cosine rank 15/1069, score 0.336. Embedding is adequate. Mechanism: weak proportional reset weight + generic call neighborhood. | `sandbox/_check_raw_rank.py` — 10/10 alignment with stored ablation top-10 | 2026-07-11 |
| Directed PPR confirmed | `G = ig.Graph(directed=True)`. `calls_only = G.subgraph_edges(...)` inherits directedness. `personalized_pagerank(directed=True)`. Source=caller, target=callee. PPR follows out-edges: from callers toward callees. | Code inspection + `G.is_directed()` check | 2026-07-12 |

### Deferred decisions (need specific evidence to unblock)

| Decision | What is deferred | What evidence would unblock it | Status |
|----------|-----------------|-------------------------------|--------|
| Overlapping communities | Should Infomap be replaced with an overlapping variant (requires real `infomap` package, not igraph built-in)? | 2-3 cross-cutting queries that test bridge functions. If those miss in graph modes but pass in vector-only, overlapping communities is justified. If they pass in current pipeline, question is closed. | Blocked on Step 3 |
| GraphSAGE reranking in production | Should GraphSAGE k-NN (architectural sibling search) be added to the pipeline? | Step 4 (pipeline wired into tool) must complete first. Only add once the base pipeline is a working product. | Blocked on Step 4 |
| `top_k_seeds` expansion | Should seed window be expanded from 15 to 20 or 25? | Currently no miss where the target lands just outside the top-15. `export_snapshot` is rank 15 — it IS in the window. Expansion would only be justified if a new miss was diagnosed as "target was rank 16-20." | No current evidence |
| LLM summaries activation | Replace mechanical AST summaries with real LLM summaries in embeddings? | `export_snapshot` analysis showed rank 15 is already achievable with mechanical summaries. Low priority until a miss is specifically attributed to poor summary quality (not PPR mechanics). | Low priority |

---

## Eval Set Status

**File:** `sandbox/query_rank_eval.json`  
**Queries:** 10  
**All 10 were used during tuning.** Overfit risk is real past ~8/10.

### Eval health rules
1. Never add a query to the eval set while actively tuning against it. Only add queries that will be genuinely held out.
2. Write the expected answer before running any new query through the pipeline.
3. If a query was used to diagnose a specific miss (all 10 current queries were), it cannot be used as an independent validation.
4. Target: grow to 20+ queries, with the top-10 as "diagnostic set" and the rest as "held-out set." Score against held-out set only when claiming progress.

### Current eval breakdown

| Query ID | Category | Target | Current best | Notes |
|----------|----------|--------|-------------|-------|
| `old_ingest` | Module-internal | `ingest_hook_payload` | Rank 2 (theme overlay) | ✅ |
| `old_context` | Module-internal | `memory_context_pack` | Rank 6 | ✅ |
| `old_snapshot` | Module-internal | `export_snapshot` | MISS | PPR dilution — floor weighting queued |
| `ambiguous_capture_persist` | Ambiguous | `ingest_hook_payload` | MISS | Accepted — query doesn't describe function |
| `ambiguous_tool_contract` | Direct | `tool_contracts` | Rank 1 | ✅ |
| `install_hooks` | Graph-connectivity | `apply_install_plan` | MISS | Function barely in live graph |
| `privacy_redaction` | Cross-module | `redact_secrets` | Rank 3 (theme overlay) | ✅ — theme overlay uniquely recovers this |
| `graph_context_filter` | Module-internal | `_filter_answer_grade_nodes` | MISS | Mechanism partially diagnosed |
| `memory_search` | Direct | `memory_search` | Rank 1 | ✅ |
| `rebuild_indexes` | Direct | `memory_rebuild_indexes` | Rank 1 (theme overlay) | ✅ |

**Needed:** 2-3 cross-cutting (bridge function) queries. Write expected answers before adding.

---

## What Each File Does — Navigation Guide

```
sandbox/
├── igraph_sandbox.py           — THE running pipeline. Start here.
│                                 initialize(), run_cold_discovery(), build_subgraph_output()
├── cochange_ablation.py        — Ablation runner. Measures Mode A/B/C across all eval queries.
│                                 Run this to get updated ablation_results.json.
├── cochange_analysis.py        — Co-change edge categorization + consumer policy table.
│                                 Do not modify without re-running amo_ingest.py.
├── amo_ingest.py               — Builds amo_nodes.json + amo_edges.json + cochange artifacts
│                                 from raw AMO git history. Slow (~5 min). Run only if AMO data changes.
├── ablation_results.json       — Latest measured results. Source of truth for all score claims.
├── query_rank_eval.json        — The 10 eval queries. Ground truth.
├── amo_cochange_pairs.json     — 437 filtered co-change pairs with categories + themes.
├── amo_cochange_themes.json    — 81 themes with labels, member functions, commit evidence.
├── _count_test_contamination.py — Measures test function contamination in ablation results.
├── _check_raw_rank.py          — Measures a target function's raw cosine rank for a query.
│                                 Use when diagnosing a miss before claiming "embedding failure."
├── _check_export_snapshot_rank.py — Specific diagnosis for export_snapshot (graphsage-centered space vs raw).
├── _diagnose_misses.py         — Full 4-miss breakdown with mechanism identification.
├── _diagnose_snapshot.py       — Specific connectivity analysis for export_snapshot.
├── _inspect_subgraph.py        — Pretty-print a subgraph JSON from sandbox/out/.
├── out/                        — Subgraph JSON outputs from run_validation().

tools/
├── graph_tools.py              — 5 MCP tools for HelixDB. search_code_semantics currently
│                                 does raw vector search only. To be updated in Step 4.
├── graph_mcp_server.py         — HTTP MCP server on :7700. Wraps graph_tools.py.

graphsage_minimal/
├── train_graphsage.py          — Trains 2-layer GraphSAGE on CALLS link prediction.
├── rank_with_graphsage.py      — Ranking sweep with GraphSAGE embeddings.
├── probe_commit_inductive.py   — Inductive embedding for new commits (no retraining).
├── out/graphsage_embeddings.npy — 1380×128 structural embeddings. Not yet in HelixDB.
```

---

## Result Log — Append after every experiment

Format: date, what changed, what was measured, what was observed, what was decided.

---

### 2026-07-11 — Phase 0 through Phase 3 complete

**Changes:**
- Infomap hoisted to startup with fixed seed=42 (deterministic)
- Test functions filtered from candidate pool by name AND file prefix
- Hub penalty exponent raised from 1.5 → 2.0
- Soft proportional community weighting (replaced binary cluster mask)
- All-seed-communities filter (removed hard top-3 cutoff)
- Theme overlay integrated into `igraph_sandbox.py` (was only in `cochange_ablation.py`)
- `build_subgraph_output()` added — returns nodes + edges JSON with consumer-policy filtering
- `run_cold_discovery` returns `(selected, ppr_scores, vec_score_map)` tuple

**Measured (all in `sandbox/ablation_results.json`):**
- VectorOnly: 5→6/10
- CallsPPR: 3→5/10
- ThemeOverlay: 4→6/10
- God-node contamination (`close`, `make_settings`, `add_event`, `init_db`): 5/10 queries → ≤1/10
- Test function contamination: 33% of result slots → 8%

**Subgraph output validated:**
- `memory ingestion pipeline hook processing` → 10 nodes, 7 edges, ppr/vector scores present
- `retrieve context from memory for agent` → 10 nodes, 22 edges

**Diagnostic findings recorded:**
- `export_snapshot`: raw cosine rank 15/1069, score 0.336. PPR dilution, not embedding failure. Fix: seed floor weighting. See `_check_raw_rank.py`.
- Directed PPR confirmed: source=caller, target=callee, PPR follows out-edges.
- `apply_install_plan`: function barely in live graph, called almost exclusively by tests. Graph-connectivity miss, not pipeline bug.

**Commits:** `a43234e` (phase 0), `00a6a10` (phase 2), `02f861e` (phase 3)

---

### [NEXT ENTRY — fill in after seed floor weighting experiment]

**Changes:**  
**Expected:**  
**Measured:**  
**Decided:**  

---

## Anti-patterns — Do Not Repeat

These are mistakes that happened in this project. Document them so they don't recur.

1. **Claiming a fix without the number.** "This is an embedding failure" was stated before checking the embedding rank. It was wrong. `_check_raw_rank.py` took 5 minutes to write and completely reversed the diagnosis. Always run the diagnostic before the claim.

2. **Building on an unverified assumption.** "The graph is undirected" was left as an assumption for multiple diagnosis cycles before being checked. Takes 2 minutes: `G.is_directed()`. Do it at the time the question arises, not later.

3. **Mixing algorithm changes with structural changes in the same commit.** Makes it impossible to know which change caused a regression.

4. **Using the eval set to tune AND to validate.** All 10 current queries were used during tuning. Any score above 6/10 on these specific queries is suspect. Need held-out queries.

5. **Accepting "it's probably fine" on contamination.** Test function contamination was 33% of result slots before it was measured. It was measured because someone pushed for the number. The number always matters.

6. **Treating a specific query fix as a general fix.** Seed floor weighting fixes `export_snapshot`. It does not fix `apply_install_plan` (different mechanism) or the ambiguous query miss (correct miss). Do not claim 6→8/10 from this change. The expectation is 6→7/10, maybe 6→7 or 8/10 if `_filter_answer_grade_nodes` is also a dilution victim (pending Step 2 diagnostic).
