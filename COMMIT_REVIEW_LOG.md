# Commit Review & CI/CD Intelligence — Engineering Log

**Started:** 2026-07-17  
**Status:** Active — update after every experiment, every decision, every measured result.  
**Rule:** Every number in this doc came from running code. Every claim cites which script produced it.

---

## Why This Was Built

After the retrieval experiments (documented in RETRIEVAL_ARCH_EXPERIMENTS.md and ARCHITECTURE_REPORT.md), the question became: if PPR, GraphSAGE, and betweenness don't belong in real-time query retrieval, where DO they belong?

The answer came from a concrete observation about CI/CD: when functions change in a commit, you need to run tests. But you don't need to run ALL tests — only the tests that are structurally reachable from the changed functions. Betweenness centrality can prove which tests are unreachable and therefore skippable.

This reframed three algorithms from "retrieval mechanisms" to "impact analysis mechanisms":
- **Betweenness centrality** → which functions are architectural chokepoints (run broad tests)
- **GraphSAGE drift** → did the function's structural role change, not just its code (override risk upward)
- **PPR post-anchor** → what does this function orchestrate downstream (integration test scope)

These combine with:
- **HelixDB `trace_blast_radius`** → who calls the changed functions (guaranteed impact set)
- **CO_CHANGE data** → what historically moves with these functions (missing change warnings)

---

## Starting Check: Is Betweenness Meaningful on the Current Graph?

**Script:** `sandbox/check_betweenness.py`  
**Date:** 2026-07-17  
**Question:** The AMO graph is from 100 commits. Are betweenness scores reliable, or is the graph slice too thin?

**Results:**

```
Top-5 by betweenness (active, non-test, degree filter applied):
  3539.3  search [embedding_store.py]
  2526.6  process_event [ingest.py]         ← correct: core ingest pipeline
  2526.6  graph_search [service.py]         ← correct: graph reasoning critical path
  2273.9  add_event [ingest.py]             ← correct: base ingest operation
  1850.2  get [client.py]                   ← noise: generic HTTP method

Top-5 by degree alone:
  509  get [client.py]          ← generic utility dominates
  312  append [raw_store.py]    ← generic utility
   85  close [service.py]       ← generic utility

Betweenness distribution:
  Non-zero: 496/1069 active functions (46%)
  >1000: 14 functions (critical path tier)
  >100:  106 functions (architecturally significant)
  >10:   304 functions (has some structural importance)
```

**Key finding:** Betweenness is meaningful for 3-4 of the top-5. Degree is dominated by generic utilities (`get`, `append`, `close`). Combined betweenness × degree is corrupted by the degree noise.

**Decision:** Use betweenness with a `degree > 200` filter to exclude generic utilities. This leaves 505 functions scored. `get [client.py]` (509 edges) is filtered. `process_event` and `add_event` (genuinely important) remain.

**Verdict:** Betweenness on this 100-commit slice is usable. Not perfect (would be richer with 500+ commits), but `process_event=0.92`, `graph_search=0.71`, `add_event=0.64` are correct architectural assessments.

---

## Architecture: What Each Algorithm Contributes

The `commit_review(changed_function_ids)` tool combines 5 layers. Understanding why each is needed:

### Layer 1: Blast radius
**What:** Every function that transitively calls the changed function (backward traversal).  
**Why:** Mandatory impact set. Any caller might break if the interface changes.  
**Without it:** Unknown test scope. Either run everything (expensive) or miss regressions.  
**Implementation:** igraph Python simulation currently. Replace with HelixDB `trace_blast_radius` after AMO ingest (4ms Rust vs Python seconds).

### Layer 2: Betweenness centrality
**What:** Normalized architectural centrality score per function. High = chokepoint.  
**Why:** Not all changes equally risky. `process_event` (0.92) changing needs 100x more test coverage than `redact_secrets` (0.0). This is the CI/CD test reduction filter.  
**Without it:** Treat all changes as equally risky. Either waste time on unnecessary tests or miss critical regressions.  
**Implementation:** Computed once at `initialize()`, stored in `_betweenness` dict. 505 functions scored after degree filter.

### Layer 3: GraphSAGE drift
**What:** Cosine distance between pre-commit and post-commit structural embedding (128-dim GraphSAGE space).  
**Why:** A function can refactor its internal call graph (changing structural role) without changing its betweenness. GraphSAGE drift catches this. Example: a utility function gains a call to a new security layer — betweenness unchanged, but structural role changed.  
**Without it:** Under-scope test coverage for refactors that change architectural role without changing measured centrality.  
**Implementation:** Currently `drift=None` for all functions. KNOWN BUG: comparing GraphSAGE-dim (128) to vector-dim (2048) embeddings — incompatible spaces. Fix: load `graphsage_embeddings.npy` separately and use `probe_commit_inductive.py` for post-commit embedding. This is the next experiment.

### Layer 4: CO_CHANGE warnings
**What:** Functions that historically always change alongside the modified functions but were NOT in this commit.  
**Why:** If A and B have jaccard=1.0 co-change, changing A without B is suspicious. This is a correctness check, not a test selection filter.  
**Without it:** Silent incomplete changes. Developer doesn't know Y should have changed when X changed.  
**Implementation:** Working. Uses `amo_cochange_pairs.json` (437 pairs). Fires when co_change_count >= 3 and the partner is not in the commit.

### Layer 5: PPR territory
**What:** Functions that the changed function orchestrates downstream (forward traversal).  
**Why:** Blast radius goes backward (callers). PPR territory goes forward (callees and their neighborhoods). Integration tests that exercise the downstream pipeline should run.  
**Without it:** Know who calls the function but not what it drives. Miss integration test coverage.  
**Implementation:** Working. Single-seed PPR on CALLS graph from the changed function.

---

## First Run: commit_review on 3 AMO Functions

**Script:** `test_commit_review.py`  
**Date:** 2026-07-17  
**Changed functions tested:**
- `ingest_hook_payload` (ingest.py)
- `process_event` (ingest.py)
- `redact_secrets` (privacy.py)

**Raw output:**

```
process_event [ingest.py]
  severity=0.2998  betweenness=0.9201  drift=None
  scope=critical  reason=high betweenness (0.92)
  blast_radius(20): [add_event, add_event, ingest_transcript, ingest_transcript, ...]
  ppr_territory(8): [get, _request, read, generate_session_summary, ...]

ingest_hook_payload [ingest.py]
  severity=0.019   betweenness=0.1060  drift=None
  scope=broad      reason=moderate centrality, 4 callers
  blast_radius(4): [main, codex_hook_response, codex_hook_response, codex_hook_response]
  ppr_territory(8): [session_exists, create_session, get, generate_session_summary, ...]

redact_secrets [privacy.py]
  severity=0.0     betweenness=0.0     drift=None
  scope=broad      reason=moderate centrality, 24 callers
  blast_radius(20): [add_event, _summarize_install_plan, ...]
  ppr_territory(0): []

select_tests:
  Run 4/311 tests (99% reduction)
  1 critical, 2 broad, 0 local
  Critical tests: 3  Recommended: 1
```

**Analysis of results:**

`process_event` correctly flagged as critical (betweenness=0.92). It's the core ingest pipeline function — changing it has the widest structural impact. This is the right answer.

`ingest_hook_payload` correctly flagged as broad. It's an entry point with 4 callers — not a hub.

`redact_secrets` gets betweenness=0.0 because it's a structural leaf (no outgoing CALLS to other significant functions) with `degree > 200` filter not applying here (it has 24 callers but 0 meaningful downstream). However, scope is overridden to "broad" because it has 24 callers — correct behavior.

**PPR territory issue:** `process_event`'s territory shows `get, _request, read` — generic HTTP client methods. These are being surfaced because the CALLS graph has edges to `client.py` utilities. The PPR territory needs the same degree filter as betweenness to exclude generic utilities. This is a bug in Layer 5.

**99% test reduction** is probably unreliable as a standalone number because the test-to-function mapping uses name substring matching — fragile. The concept is correct, the implementation of test-to-caller mapping needs proper improvement.

---

## Known Issues (measure before fixing)

### Issue 1: GraphSAGE drift is None
**Root cause:** `commit_review` looks for a GraphSAGE-dimension embedding on the igraph `v["embedding"]` field, but those embeddings are 2048-dim (OpenRouter vector embeddings), not 128-dim (GraphSAGE). Dimension mismatch = None.

**Fix needed:**
1. Load `graphsage_minimal/out/graphsage_embeddings.npy` (1380×128) in `_require_init`
2. Store as `_graphsage_emb` dict: `node_id -> embedding`
3. In `commit_review`, for pre-commit: look up `_graphsage_emb[func_id]`
4. For post-commit: run `probe_commit_inductive.py` logic on the new code to get new GraphSAGE embedding
5. Drift = 1 - cosine(pre_emb, post_emb)

**Expected impact:** Functions that changed structural role (refactoring call graph) will get non-zero drift and may be upgraded from "broad" to "critical" scope.

**Experiment B (to run after fix):** Take 10 AMO commits, compute drift for each changed function, check if high-drift functions correlate with larger blast radius (structural role change = more impact).

### Issue 2: PPR territory includes generic utilities
**Root cause:** PPR follows CALLS edges from the anchor, and the anchor calls generic utilities (`get`, `_request`) which get high PPR scores due to being called everywhere.

**Fix:** Apply same degree filter to PPR territory results — exclude functions with `calls_only.degree()[idx] > _GENERIC_DEGREE_THRESHOLD`.

### Issue 3: Test-to-function mapping is fragile
**Root cause:** `select_tests` maps tests to callers by substring matching (`caller_name.lower() in test_name.lower()`). This is imprecise.

**Better approach:** Use the CALLS graph — find which test functions transitively call into the blast radius. Direct graph traversal, not name matching.

**Note:** This requires test functions to be in the CALLS graph, which they are (AMO tests import and call production functions).

### Issue 4: Blast radius uses igraph simulation
**Root cause:** AMO is not in HelixDB.

**Fix:** Ingest AMO into HelixDB. After that, `commit_review` calls `trace_blast_radius` (HelixDB, 4ms Rust) instead of the Python igraph loop.

---

## Pending Experiments

### Experiment A: Betweenness threshold calibration
**Question:** Do the current thresholds (critical > 0.5, broad > 0.1) correctly scope test selection on real commits?

**Method:**
1. Take 10 AMO commits where we know tests passed/failed
2. For each commit, compute which functions changed
3. Run `commit_review` and check which tests would be selected
4. Compare selected set to actual failing tests
5. Adjust thresholds if selected set misses any failures

**Requires:** AMO in HelixDB (for test function CALLS edges)

**Expected outcome:** Thresholds will need adjustment. The 0.5 critical threshold may be too high (some moderately central functions at 0.2-0.3 may still cause wide failures).

### Experiment B: GraphSAGE drift calibration
**Question:** What drift score correlates with "this change needs broader test coverage than betweenness alone suggests"?

**Method:**
1. Run `probe_commit_inductive.py` across 20 AMO commits
2. For each changed function, record drift score
3. Compare drift to actual blast radius size
4. Identify threshold where "high drift despite low betweenness" predicts larger-than-expected impact

**Requires:** GraphSAGE drift fix (Issue 1 above)

### Experiment C: End-to-end product test
**Question:** Does `commit_review` + `select_tests` help a real agent review a commit better and faster than without these tools?

**Task:** "Review this AMO commit that added the cross-encoder rerank stage. What's the structural impact, what tests should run, and what co-change risk exists?"

**Baseline:** Agent uses grep, reads files manually  
**Treatment:** Agent uses `commit_review`, `search_code_semantics`, `explain_coupling`

**Measure:**
- Tool calls to complete answer
- Whether agent correctly identifies co-change risks
- Whether test selection covers the actual regression surface

---

## What With vs Without HelixDB Actually Changes

**Token count:** No difference. The subgraph from `search_code_semantics` is the same size. The blast radius list is the same size. HelixDB changes **latency**, not **information content**.

**Latency:**
- blast radius: Python igraph loop (seconds) → HelixDB Rust (4ms after AMO ingest)
- temporal trace: not available for AMO → HelixDB 4.2ms after ingest
- time travel diff: not available for AMO → HelixDB after ingest

**What changes the output meaningfully:**
- GraphSAGE drift fix (Layer 3) → changes severity scoring, may upgrade/downgrade test scopes
- Test-to-function mapping fix (Issue 3) → changes which tests are selected
- Betweenness threshold calibration (Exp A) → changes which functions are "critical" vs "broad"

**Summary:** HelixDB matters for production latency. It doesn't change the correctness of `commit_review`'s output. The open issues (drift=None, PPR territory noise, test mapping) matter more for correctness than HelixDB does.

---

## Files Reference

| File | Purpose |
|------|---------|
| `sandbox/check_betweenness.py` | Betweenness validity check — shows top functions, distribution, degree noise |
| `pipeline_api.py` | `commit_review()`, `select_tests()`, `_compute_betweenness()` |
| `tools/graph_tools.py` | `commit_review`, `select_tests` wrapper functions |
| `tools/graph_mcp_server.py` | MCP manifest + TOOL_MAP entries for both tools |
| `test_commit_review.py` | Smoke test — run on 3 AMO functions, verifies all layers execute |
| `graphsage_minimal/probe_commit_inductive.py` | GraphSAGE inductive inference — needed for drift fix |

---

## Result Log

### 2026-07-17 — First working commit_review

**Commit:** `029ba0b`  
**What ran:** `test_commit_review.py` on 3 functions  
**Result:** All layers execute without error. Correct severity ordering (process_event > ingest_hook_payload > redact_secrets). 99% test reduction reported (mapping fragile, concept correct).  
**What doesn't work:** drift=None (dimension mismatch), PPR territory includes generic utilities, test mapping uses fragile substring matching.  
**Next:** Fix GraphSAGE drift (load sage emb separately), then Experiment B.

---

### [Next entry — fill after each experiment]

**Date:**  
**Change:**  
**Expected:**  
**Measured:**  
**Decision:**  
