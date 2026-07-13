# From PPR-as-Reranker to Multi-Tool Code Intelligence
## Architecture Report — 2026-07-12/13

**Scope:** Everything from the moment we questioned PPR's role in retrieval through experiments, tool rebuilding, and product evaluation.

---

## Starting Point

The system had a single retrieval pipeline:

```
query → embed → cosine seeds (k=20) → PPR random walk → cluster filter → MMR → subgraph JSON
```

Scores on 13 eval queries: **VectorOnly 7/13, CallsPPR 7/13, ThemeOverlay 7/13**

All three modes tied. PPR was adding complexity without adding value. That was the signal to investigate.

---

## Why PPR Fails at Query Time — The Actual Mechanism

A 5-query isolation test (PPR helped 0/5, hurt 1/5, neutral 4/5) revealed the root cause:

**PPR is a random walk.** At each step, the walker follows a CALLS edge (85% probability) or teleports back to a seed (15%). The seeds are the top-k semantic matches. After convergence, each node's score is its visit frequency.

The problem: **the random walk doesn't know what the query is about.** It only knows graph structure. High-degree nodes (`memory_write`, `append`, `close`) absorb disproportionate mass regardless of query semantics. When `redact_secrets` (vector rank 2) becomes PPR-MISS, it's because the walker starts near the ingest seeds, walks into the storage layer, finds `memory_write` with 50+ callers, and pools there. `redact_secrets` has one caller — no structural amplification.

Every patch applied to PPR over this session was fighting the symptom:
- Soft community weighting → partially fixed community isolation but not hub accumulation
- k=20 seeds → helped boundary cases by giving isolated clusters a seed
- Floor weighting → no-op, the floor was already below proportional weights
- Option A' (soft cluster filter) → god-nodes flooded from zero-seed clusters

None addressed the root cause: random walk mass accumulates by structural degree, not semantic relevance.

**The retroactive insight:** Vector-only was sitting at 10/13 with 2 god-nodes the entire time — the best single mode throughout. Every PPR experiment was trying to improve on something that was already being beaten by a simpler approach.

---

## Experiment 1: BFS as PPR Replacement

**Hypothesis:** BFS from top-5 seeds would match or beat PPR with less contamination.

**Results:**
| Mode | HIT@13 | God-nodes |
|------|--------|-----------|
| Vector only | 10 | 2 |
| PPR (k=20) | 7 | 5 |
| BFS-out 2-hop | 10 | **10** |
| BFS-in 2-hop | 9 | 3 |
| BFS-both 1-hop | 10 | 9 |

BFS-out matched vector but added 10 god-nodes — following what functions call leads directly to generic utilities. At 2,120 nodes that's manageable; at 50,000 it's unusable. BFS-in was cleaner but missed some queries. Neither improved on vector alone.

**Verdict:** Don't replace PPR with BFS. The problem isn't the traversal algorithm — it's using any traversal algorithm as a pre-anchor reranker for semantic search. Vector search finds the anchors. Traversal belongs after the anchors are known.

---

## Experiment 4: CO_CHANGE Bridges

**Hypothesis:** Explicitly adding CO_CHANGE neighbors (scored by theme relevance) would recover cross-module functions.

**Clarification (resolves apparent contradiction with theme overlay):** `redact_secrets` has **zero filtered CO_CHANGE pairs** in the 437-pair dataset. The theme overlay previously recovered it at rank 3 via PPR random walk — when CO_CHANGE edge costs were theme-boosted, the walk happened to route mass toward `redact_secrets` through intermediate nodes. The bridge expansion (direct CO_CHANGE neighbor check) correctly found nothing because no direct edge exists. Two different mechanisms reaching the same node; the bridge approach requires a direct edge, PPR doesn't.

**Result:** 10/13 at every threshold including unthresholded. Zero bridges fired for any missing query.

**Why:** The 437 filtered pairs from 100 commits don't span the missing queries. The missing functions (`export_snapshot`, `apply_install_plan`, `_filter_answer_grade_nodes`) have no direct CO_CHANGE edges to any function that appears as a top-5 vector seed for their queries.

**CO_CHANGE is still valuable — just not for retrieval expansion at current dataset size.** It answers "why do X and Y change together?" (the `explain_coupling` tool). At 500+ commits the graph would be denser and bridges would fire more often.

---

## Experiment 6: MMR

**Tested:** No-diversity (top-k), MMR lambda=0.6, lambda=0.8, file-dedup max=2/3.

**Result:** MMR lambda=0.8 showed 11/13 — then was correctly identified as an eval-set artifact and reverted.

**The domain-specific insight (correctly applied):** MMR was designed for document retrieval where chunks can be near-duplicates. In a codebase, every function is a distinct entity serving its own purpose. Penalizing `memory_search [tools.py]` because `memory_search [server.py]` was already selected is wrong — both are functions an agent might need. The diversity penalty is the wrong model for function-level retrieval.

This makes MMR the second of the original four algorithms (PPR, MMR, betweenness, Steiner paths) to be tested and rejected on real data. Not a failure of the original brainstorming — that's the process working as intended.

**Verdict:** Pure cosine top-k. No diversity penalty. Functions are distinct.

---

## Experiment 5: GraphSAGE Structural Siblings

**Background:** GraphSAGE was trained on AMO's CALLS graph link prediction (AUC 0.81). The 1380×128 structural embeddings encode each function's architectural role — call depth, fan-out pattern, community membership.

**The core finding (validated across 4 anchors):**

| Anchor | Vector/SAGE overlap | What GraphSAGE found |
|--------|--------------------|--------------------|
| `memory_write [server.py]` | 0/8 | `memory_write [tools.py]`, `add_memory_unit`, `process_event` — MCP→tool→storage chain |
| `rebuild_graph_cache` | 1/8 | `current_context`, `do_GET`, `do_POST`, `rebuild_central_from_evidence` — service-level operations at same tier |
| `ingest_hook_payload` | 1/8 | `ingest_transcript`, `import_codex_sessions`, `generate_session_summary` — top-level pipeline entry points |
| `add_memory_unit` | 0/8 | Mostly test functions (contamination — fixed by `_is_candidate` filter in production) |

Near-zero overlap between GraphSAGE and vector results across 3 of 4 anchors. They are finding genuinely different things:
- **Vector** finds functions with similar names/descriptions
- **GraphSAGE** finds functions at the same architectural tier with the same call-graph position

**The key validation:** `memory_write [server.py]` → `memory_write [tools.py]` with similarity 0.978. These are two different functions in two different files — one is the MCP entry point, one is the backing tool implementation. Vector search would rank `memory_write [tools.py]` as semantically similar (same name). GraphSAGE finds it for the right reason: same structural position in the call hierarchy.

**Correct scoping:** `find_structural_siblings` is a separate tool answering "what other functions play the same architectural role as X?" — not a retrieval reranker. This is the first concrete, validated use case for GraphSAGE after multiple earlier experiments showed it shouldn't be used for semantic search ranking.

---

## The Architecture That Emerged

The system moved from "one pipeline" to "multiple specialized tools, each with a specific purpose."

### What each tool is for

**`search_code_semantics`** — "Find functions relevant to my task"
- Pure cosine top-k, returns subgraph JSON (nodes + edges between them)
- The edges are the key output — agent sees `codex_hook_response CALLS ingest_hook_payload` without reading code

**`find_structural_siblings`** — "What other functions play the same architectural role as X?"
- GraphSAGE 128-dim k-NN, test functions filtered
- Finds functions at same call depth, same community, same fan-out
- Zero overlap with vector search — genuinely complementary information

**`explain_coupling`** — "Why do X and Y always change together?"
- Direct CO_CHANGE edge lookup → category + jaccard + occurrence count + theme proportions
- Categories: `shared_dependency`, `shared_commit_only`, `structural_redundant`, `temporal_burst`
- Impossible without git history analysis

**`trace_blast_radius`** — "What's affected if I change X?"
- HelixDB `repeat().emit_all()` single Rust query, 4ms
- Returns all callers up to N hops

**`get_temporal_vulnerability_trace`** — "What code predates a security fix?"
- HelixDB multi-hop (in→out→in), 4.2ms
- Finds callers whose code was committed before a given timestamp

**`get_code_time_travel_diff`** — "How did this function change?"
- HelixDB PREVIOUS_VERSION traversal

**`edit_code`** — "Apply this fix and update the graph"
- AST parse + line replacement + re-ingest

**`pipeline_status`** — Health check, confirms which search mode is active

### Three types of intelligence

| Type | Tools | What it answers |
|------|-------|----------------|
| **Semantic** | `search_code_semantics` | What's related to my task? |
| **Structural** | `find_structural_siblings`, `trace_blast_radius` | What plays the same role? What's affected? |
| **Historical** | `explain_coupling`, `get_temporal_vulnerability_trace`, `get_code_time_travel_diff` | What changes together? What's stale? |

---

## Product Test: Measured Results

**Task:** Understand AMO memory ingestion pipeline — find functions, find structural equivalents, explain co-changes, identify blast radius.

**Condition A (generic grep, 3 calls):**
- 18 functions found, ~3592 tokens consumed
- Zero structural relationships, zero co-change data, blast radius unknown

**Condition B (our tools, 6 calls):**
- 10 ranked functions, 6 structural edges — agent sees CALLS/IMPORTS relationships without reading code
- 8 architectural siblings (`ingest_transcript`, `import_codex_sessions`, `generate_session_summary`) — cross-module functions with same structural role, impossible to find with grep
- 2 co-change relationships (`codex_hook_response <-> ingest_hook_payload`, jaccard=1.0, shared_dependency)
- 4 direct callers identified in one query
- ~720 tokens consumed

**5x token reduction, 10x more information per token.**

The extra 3 tool calls (vs generic's 3) each answered a question that grep cannot answer at all, not just more slowly.

---

## What's Known, What's Not

### Settled

| Question | Answer | Evidence |
|----------|--------|----------|
| PPR for query-time retrieval | No — hurts more than helps | 5-query test: 0/5 helped |
| MMR for function-level retrieval | No — functions are distinct entities | Exp 6: 11/13 was eval artifact |
| BFS for retrieval | No — god-node contamination at scale | Exp 1: 10 god-nodes at 2120 nodes |
| CO_CHANGE bridges for retrieval | No — dataset too sparse at 100 commits | Exp 4: zero bridges fired across 13 queries |
| GraphSAGE for retrieval reranking | No — wrong use case | Earlier experiments, Q1 rank 138 |
| GraphSAGE for structural similarity | Yes — validated across 4 anchors | Exp 5: near-zero overlap with vector, finds architectural tiers |
| Default retrieval mode | Pure vector top-k | 10/13 HIT@13, 2 god-nodes |
| CO_CHANGE for relationship explanation | Yes — works, deployed | `explain_coupling` tested and working |

### Open

| Question | Status |
|----------|--------|
| AMO in HelixDB | Not done — HelixDB tools work on auth (10 functions) only |
| Real LLM agent test | Not done — product demo was simulated, not real agent loop |
| `main`/`__init__` filter for GraphSAGE siblings | Known issue, not fixed |
| Overlapping Infomap | Deferred — needs cross-cutting query evidence (partially gathered) |
| Commit review tool | Architecture designed, not built |
| 500+ commits CO_CHANGE dataset | Would enable bridge-based retrieval |

---

## The Single Most Important Remaining Step

Run the product test with a real LLM agent — give it the task, measure tool calls to correct answer, hallucination rate on function/file names, decision quality. Every number in this report is pipeline health metrics. The only measurement that tells you whether any of this is worth building is whether a real agent, using these tools, makes better decisions on real code tasks than one without them.

That test hasn't happened yet. Everything else is infrastructure. The infrastructure is solid. The test is what's missing.
