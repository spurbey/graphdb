# Graph-Native Code Intelligence System — Full Engineering Record

**Repo:** `spurbey/graphdb`
**Subject repo for demo:** `auth/` module (in-memory auth system, intentionally simple)
**Subject repo for real validation:** `spurbey/agent-memory-orchestrator` (2120-node slice)
**Database:** HelixDB (`localhost:6969`) — Rust-backed graph database
**Agent interface:** MCP server (`localhost:7700`)
**Embedding model:** `nvidia/llama-nemotron-embed-vl-1b-v2:free` via OpenRouter (2048-dim, free tier)

---

## Purpose

Build a git-inspired knowledge graph that ingests a Python repo's full commit history and enables an AI agent to:
1. Find relevant code via natural language (semantic vector search)
2. Traverse architectural dependencies (graph traversal)
3. Understand how code evolved over time (time-travel)
4. Identify stale/vulnerable code relative to a timestamp
5. Patch code on disk and automatically re-ingest

This is **not** a file search tool. The graph encodes structural relationships (CALLS, CONTAINS, INHERITS), temporal relationships (PREVIOUS_VERSION, NEXT_COMMIT, GENERATED), and semantic summaries — so an agent can answer questions like "what calls this function and when did those callers last change?" in a single query.

---

## Repository Structure

```
graphdb/
├── scalable_ingest.py      — Main ingestion engine (tree-sitter AST → HelixDB)
├── semantic_pass.py        — Post-ingestion semantic enrichment
├── dump_viz.py             — Rebuild graph_viz.json artifact from graph_payload.json
├── auth/                   — Demo subject repo (in-memory auth module)
│   ├── models.py           — create_user, get_user, delete_user, list_users, user_exists
│   └── service.py          — signup, login, logout, delete_account, validate_username
├── tools/
│   ├── graph_tools.py      — 5 agent tools (all single-query, no N+1 loops)
│   ├── graph_mcp_server.py — HTTP MCP server on port 7700
│   ├── harness_graph_viz.html — D3 force-directed graph visualizer
│   ├── _test_queries.py    — Full 4-query validation suite
│   ├── _test_bench.py      — Performance benchmark (N+1 vs single query)
│   ├── _test_3hop.py       — 3-hop traversal validation
│   └── _test_vector.py     — Vector search validation
├── sandbox/                — AMO cold discovery pipeline (igraph prototype)
│   ├── amo_ingest.py       — Ingest first 100 AMO commits → nodes.json + edges.json
│   ├── igraph_sandbox.py   — Full pipeline: weights → Infomap → PPR → MMR
│   ├── amo_dump_viz.py     — Convert to D3 viz format
│   ├── amo_query_test.py   — Single query runner
│   └── README.md           — Sandbox-specific instructions
├── .kiro/settings/mcp.json — Registers MCP server with Kiro
└── .env                    — OpenRouter API key
```

---

## Graph Schema

```
Commit        --NEXT_COMMIT-->      Commit
Commit        --CONTAINS-->         FileIdentity
Commit        --GENERATED-->        FunctionState
FileIdentity  --CONTAINS-->         FunctionIdentity | ClassIdentity
ClassIdentity --CONTAINS-->         FunctionIdentity
ClassIdentity --INHERITS-->         ClassIdentity
FunctionIdentity --HAS_STATE-->     FunctionState
FunctionIdentity --CALLS-->         FunctionIdentity
FunctionState --PREVIOUS_VERSION--> FunctionState
FileIdentity  --IMPORTS-->          FileIdentity
```

### Node Properties

**Commit**
- `node_id` — `commit_<hash7>`
- `hash` — full SHA
- `author`, `msg`, `timestamp`
- `ai_rationale` — structural summary of what changed in this commit

**FunctionIdentity** (permanent, one per function across all commits)
- `node_id` — `func_<safe_file>_<func_name>`
- `name`, `file`

**FunctionState** (one per function per commit it changed in)
- `node_id` — `state_<safe_file>_<func_name>_<hash7>`
- `code` — raw source (up to 4000 chars)
- `code_hash` — SHA1 of code for dedup
- `commit` — which commit generated this state
- `function_id` — back-reference to FunctionIdentity
- `ai_summary` — human-readable behavioral description
- `ai_summary_vec` — 2048-dim float32 embedding of ai_summary
- `status` — `"active"` (HEAD version) or `"superseded"` (older version)

**FileIdentity**
- `node_id` — `file_<safe_path>`
- `file`, `external` (true if imported stdlib/third-party)

**ClassIdentity**
- `node_id` — `class_<safe_file>_<class_name>`
- `name`, `file`

---

## Ingestion Pipeline (`scalable_ingest.py`)

### What it does

Walks commits oldest→newest via GitPython. For each commit, tree-sitter parses changed `.py` files and extracts:
- Functions (with code, docstring, call sites)
- Classes (with inheritance)
- Imports

All nodes and edges are written to HelixDB in real time. A JSON artifact (`graph_payload.json`) is also written for the visualizer.

### Key design decisions

**Upsert pattern:** `FunctionIdentity` and `FileIdentity` are stable across commits — the `except: pass` on insert means "skip if already exists" (unique index on `node_id`). Only `FunctionState` and `Commit` nodes are always new.

**PREVIOUS_VERSION chain:** A `state_tracker` dict maps `func_id → latest_state_id`. Each new `FunctionState` checks this and draws a `PREVIOUS_VERSION` edge to the prior state before updating the tracker.

**NEXT_COMMIT chain:** An `edge_seen` set plus `prev_commit_id` variable chains commits in timeline order.

**Status patching:** After the full ingestion loop, a post-pass iterates all `FunctionState` nodes and sets `status = "active"` for the HEAD version of each function, `"superseded"` for all prior versions. Uses `set_property` (not drop+reinsert — see bug section).

**Skip list:** Tooling files are excluded from ingestion:
```python
_SKIP_FILES = {
    "scalable_ingest.py", "semantic_pass.py", "dump_viz.py",
    "level_1_parser.py", "ingestion_process.txt",
    "graph_mcp_server.py", "graph_tools.py",
    "_test_loop.py", "_test_queries.py", "_test_vector.py",
    "_test_3hop.py", "_test_bench.py"
}
```

**Inline summaries + embeddings at insert time:**
```python
_summary = _summarise(code)   # lookup in _SUMMARIES dict, fallback to AST
nodes.append({
    ...
    "ai_summary":     _summary,
    "ai_summary_vec": _embed(_summary),   # OpenRouter API call
    ...
})
```

**Critical:** Embeddings must be stored at node creation. HelixDB's vector index only indexes values present at insert time. Values written later via `set_property` are not indexed for vector search.

### Indexes created

```python
IndexSpec.node_unique_equality(kind, "node_id")   # for all 5 node kinds
IndexSpec.node_vector("FunctionState", "ai_summary_vec")  # vector search
```

---

## Semantic Pass (`semantic_pass.py`)

Runs after ingestion. For each commit in HelixDB:
1. Traverses `Commit --GENERATED--> FunctionState` to collect all states
2. Runs `_analyse(code)` to generate `ai_summary` (or looks up `_SUMMARIES` dict)
3. Patches `ai_summary` onto each `FunctionState` via `set_property`
4. Patches `ai_rationale` onto the `Commit` via `set_property`

**LLM hook:** `_llm_summarize()` stub exists — uncomment and set `OPENAI_API_KEY` to replace AST analysis with real LLM summaries.

### `_SUMMARIES` dict

Hand-written summaries for all 10 auth module functions, keyed by function name. These are also mirrored in `scalable_ingest.py` so they're available at insert time for vector indexing.

---

## The 5 MCP Tools (`tools/graph_tools.py`)

All tools are single-query — no Python loops between hops. Everything runs in Rust inside HelixDB.

### Tool 1: `search_code_semantics(prompt, k=5)`

**Purpose:** Find semantically relevant active functions from natural language.

**Implementation:**
1. Embed `prompt` via OpenRouter → 2048-dim float32 vector
2. `vector_search_nodes("FunctionState", "ai_summary_vec", vec, k*3)` — fetch `k*3` candidates
3. Filter: `status == "active"` AND `ai_summary is not null`
4. Limit to `k`
5. Project: `node_id, function_id, ai_summary, code`

The `k*3` overfetch is necessary because the vector index returns top-K before the filter is applied, so without it the `active` filter can starve the result set.

### Tool 2: `get_code_time_travel_diff(state_node_id)`

**Purpose:** Show how a function changed — current state vs previous version.

**Implementation:**
- Query 1: fetch current `FunctionState` by `node_id`
- Query 2: traverse `FunctionState --PREVIOUS_VERSION--> FunctionState` from the same node

Returns `{current: {...}, previous: {...} | null}`.

### Tool 3: `trace_blast_radius(function_identity_id, depth=3)`

**Purpose:** Find all functions that call a given function, up to N hops deep. Single Rust traversal.

**Implementation:**
```python
g().n_with_label("FunctionIdentity")
   .where(Predicate.eq_param("node_id", "fid"))
   .repeat(
       RepeatConfig.new(SubTraversal.new().in_("CALLS"))
                   .times(depth)
                   .emit_all()
   )
   .dedup()
   .project([node_id, name, file])
```

Wire format sent to HelixDB (1 HTTP request regardless of depth):
```json
NWhere($label == "FunctionIdentity")
→ Where(node_id == param)
→ Repeat{ traversal: [In("CALLS")], times: 3, emit: "All" }
→ Dedup
→ Project[node_id, name, file]
```

**Performance:** 4.03ms vs 33.97ms for N+1 loop. **8.4x faster.**

**Why not `.in_("CALLS").in_("CALLS").in_("CALLS")`:** That returns only nodes at exactly depth=3, not nodes at depths 1, 2, AND 3. `repeat().emit_all()` emits at every depth level.

### Tool 4: `get_temporal_vulnerability_trace(target_func, timestamp_iso)`

**Purpose:** Find callers of a function whose code was committed before a given timestamp — identifies code that predates a security fix.

**Implementation (single chained query):**
```python
g().n_with_label("FunctionIdentity")
   .where(Predicate.eq_param("name", "fname"))
   .in_("CALLS")           # hop 1: who calls the target
   .out("HAS_STATE")        # hop 2: their function states
   .in_("GENERATED")        # hop 3: the commits that generated those states
   .where(Predicate.lt_param("timestamp", "ts"))  # filter: before cutoff
   .project([node_id, hash, timestamp, msg])
```

Path traversed: `FunctionIdentity <-[CALLS]- FunctionIdentity -[HAS_STATE]-> FunctionState <-[GENERATED]- Commit`

**Performance:** 4.20ms vs 26.36ms for nested loops. **6.3x faster.**

This proves HelixDB supports mixed-direction traversal (in → out → in) in a single request.

### Tool 5: `edit_code(file, function_name, new_code)`

**Purpose:** Patch a function in the working tree and re-ingest into the graph.

**Implementation:**
1. Validate `new_code` parses as valid Python via `ast.parse()`
2. Find function in existing file by AST line numbers (`ast.FunctionDef.lineno`, `end_lineno`)
3. Detect indentation of existing function
4. Replace exactly those lines with `new_code` (preserving indent)
5. Write file back
6. `importlib.reload(scalable_ingest)` → `run_ingestion()` to update graph

Returns `{status, file, function_name, lines_replaced, ingest}`.

---

## MCP Server (`tools/graph_mcp_server.py`)

Simple HTTP server on `localhost:7700`. Two endpoints:

- `GET /` or `GET /manifest` — returns tool manifest (schema_version, name, tools list)
- `POST /call` — `{"name": tool_name, "parameters": {...}}` → `{"result": ...}`

Registered with Kiro via `.kiro/settings/mcp.json`:
```json
{
  "mcpServers": {
    "graphdb": {
      "command": "python",
      "args": ["tools/graph_mcp_server.py"],
      "cwd": "${workspaceFolder}",
      "type": "http",
      "url": "http://127.0.0.1:7700"
    }
  }
}
```

---

## Bugs Found and Fixed

### Bug 1: drop+reinsert destroys edges

**Symptom:** `get_code_time_travel_diff` returned `previous: null` even though `PREVIOUS_VERSION` edges were being created during ingestion.

**Root cause:** `semantic_pass.py` was patching `ai_summary` by dropping the node and reinserting it. Dropping a node in HelixDB destroys all its edges — `PREVIOUS_VERSION`, `HAS_STATE`, `GENERATED` all gone.

**Fix:** Replace drop+reinsert with `set_property`:
```python
g().n_with_label("FunctionState")
   .where(Predicate.eq_param("node_id", "nid"))
   .set_property("ai_summary", PropertyInput.param("val"))
```

### Bug 2: NodeRef.var chaining broken in batch traversal

**Symptom:** `_get_states_for_commit` returned 0 states even though GENERATED edges existed.

**Root cause:** The original query used `NodeRef.var("commit")` to reference a previously-fetched node in a second `g().n(NodeRef.var(...))` traversal within the same batch. HelixDB doesn't support this cross-step reference pattern in dynamic batches.

**Fix:** Direct chained traversal from a single anchor:
```python
g().n_with_label("Commit")
   .where(Predicate.eq_param("node_id", "cid"))
   .out("GENERATED")
   .value_map()
```

### Bug 3: Vector index rejects string values

**Symptom:** All `FunctionState` inserts failing silently after vector index creation.

**Root cause:** `IndexSpec.node_vector("FunctionState", "ai_summary")` was created, but `ai_summary` was a string. HelixDB's vector index requires float32 arrays — any insert on that node kind fails if the indexed property is a string.

**Fix:** Use a separate property `ai_summary_vec` for the float32 array. Keep `ai_summary` as plain string. Create `node_vector` index on `ai_summary_vec`.

### Bug 4: N+1 query patterns

**Symptom:** `trace_blast_radius` and `get_temporal_vulnerability_trace` were sending multiple HTTP requests to HelixDB — one per node encountered per hop.

**Root cause:** Python loops were orchestrating the traversal client-side: fetch hop 1, loop over results, fetch hop 2 for each, etc.

**Fix:** See Tool 3 and Tool 4 implementations above. Single query per tool call regardless of depth or graph size.

### Bug 5: Text index doesn't pick up set_property updates

**Symptom:** `search_code_semantics` returned empty results even though `ai_summary` was populated.

**Root cause:** The text/vector index in HelixDB only indexes values present at node creation. Values written via `set_property` after creation are not indexed.

**Fix:** Compute and store `ai_summary` and `ai_summary_vec` at node creation time in `scalable_ingest.py`, not in the post-hoc semantic pass.

### Known Bug (unfixed): code_hash dedup not implemented

**Description:** `code_hash` is computed on every `FunctionState` but never compared against the prior state before creating a new node. If a commit touches a file, all functions in that file get new `FunctionState` nodes even if their code didn't change. This is most visible in the AMO sandbox where it inflated CO_CHANGE edges to 489,089 (21.8% of all possible pairs).

**Impact on graphdb (auth module):** Minor — only 2 files, 10 functions. In AMO it's severe.

**Correct fix:** Before inserting a new `FunctionState`, check if `code_hash` matches the prior state for the same `func_id`. If equal, skip the new state and keep the existing one as active.

---

## Full Query Validation (auth module demo)

All tests run via `tools/_test_queries.py`.

### Q1: Temporal Vulnerability Trace
```
get_temporal_vulnerability_trace("create_user", "2026-06-25T00:00:00+00:00")
```
**Result:** `signup` at commit `623241a`, timestamp `2026-06-24T17:48:44`, status `superseded`.
Correctly identifies that `signup` was calling `create_user` before the security upgrade that added password validation.

### Q2: Time-Travel Diff
```
get_code_time_travel_diff("state_auth_service_signup_eef5695")
```
**Result:**
- Current `[eef5695]`: full validation with `validate_username`, 8-char password check, structured JSON response
- Previous `[623241a]`: 4-line bare signup, no validation, plain string return

`PREVIOUS_VERSION` edge intact — `set_property` fix confirmed working.

### Q3: Semantic Vector Search
```
search_code_semantics("user registration and account creation pipeline", k=3)
```
**Result:** `signup` + `delete_account` returned. Both correct — signup is the registration entry point, delete_account is part of the account lifecycle pipeline.

### Q4: Full Automated Loop
1. `get_temporal_vulnerability_trace` → found 1 stale function (`signup`)
2. `get_code_time_travel_diff` → confirmed old code missing 8-char password check
3. `edit_code("auth/service.py", "signup", new_code)` → patched lines 30-41, re-ingested
4. New `FunctionState` node created in graph with patched code

### 3-hop traversal validation
```
search_code_semantics("look up a stored user credential", k=3)
→ anchor: get_user (func_auth_models_get_user)

trace_blast_radius("func_auth_models_get_user", depth=3)
→ signup  (calls get_user directly)
→ login   (calls get_user directly)
→ delete_account (calls login, which calls get_user)
```

Full chain: `get_user ← login ← delete_account` — 3-node dependency path resolved from natural language in one vector search + one Rust traversal.

---

## AMO Cold Discovery Pipeline (`sandbox/`)

### Context

The `agent-memory-orchestrator` repo is the real production codebase. The sandbox validates the cold discovery pipeline on a real 2120-node graph before porting to the production Rust-backed system.

### Data

- **Source:** First 100 commits of AMO (`61b51d9` Apr 30 → `a7d2162` May 15, 2026)
- **Nodes:** 2120 unique `FunctionIdentity` nodes
  - 1380 active (present at commit 100) — with embeddings
  - 740 superseded — graph structure only, no embedding
- **Edges:** ~643k total (CALLS 4,440 + CO_CHANGE ~489k + IMPORTS ~150k)
- **Embeddings:** 1380 calls to OpenRouter free model, one per active function summary

**Note on edge count:** The number 494,009 printed during the original run was a bug — it was printed before IMPORTS edges were appended to the list. The real total is approximately 643,828.

### The 4 Algorithms

#### Algorithm 1: Vector Seed Search (cosine similarity)

Computes cosine similarity between the query embedding and all 1380 active function embeddings. Returns top-15 most similar functions as "seeds."

```python
sim = np.dot(query_vec, func_vec) / (norm(query_vec) * norm(func_vec))
```

These seeds are the semantic anchor — they tell the graph "start your walk near here."

#### Algorithm 2: Infomap Community Detection

Groups functions into communities based on who calls whom. Run on CALLS-only subgraph — CO_CHANGE and IMPORTS edges are excluded because at 489k connections they collapse the graph into 2-3 giant communities, destroying the signal.

Infomap minimizes the description length of a random walk on the graph. Functions that are tightly interconnected via CALLS form coherent communities (e.g., the memory ingest pipeline, the session management cluster, the MCP tool layer).

Used for: scoping PPR to the top-3 dominant seed communities, preventing god-nodes from other parts of the codebase from absorbing PageRank mass.

#### Algorithm 3: Personalized PageRank (PPR)

Standard PageRank but with a personalized reset vector instead of uniform teleportation. The reset vector is built from the seed similarity scores — so random walkers keep teleporting back to semantically relevant seeds.

```python
reset_vector[seed_idx] = cosine_similarity(query, seed)
reset_vector /= reset_vector.sum()

ppr_scores = calls_only.personalized_pagerank(
    damping=0.85,
    reset=reset_vector.tolist()
)
```

**What PPR adds beyond vector search:** Functions that are *structurally close* to the seeds (called by seeds, or sharing callers with seeds) also score highly even if they don't match the query semantically. This surfaces the architectural neighborhood, not just the single best match.

Run on CALLS-only graph. Candidates filtered to top-3 seed clusters before ranking.

#### Algorithm 4: MMR (Maximal Marginal Relevance)

From the top-30 PPR candidates, selects 10 that maximize:
```
score = 0.6 × PPR_score - 0.4 × max_cosine_similarity_to_already_selected
```

Ensures diversity — without MMR, PPR might return 10 near-identical utility functions from the same file. MMR trades off relevance vs redundancy.

### Edge Weight Computation

Applied to CALLS graph edges:

```python
# A. Structural cost
ast_cost = 1.0 if CALLS else 1.5 if IMPORTS else 2.0

# B. Temporal cost (inverse co-change frequency)
temporal_cost = 1.0 / (1.0 + co_change_count) if co_change_count >= 1 else 1.0

base_cost = (0.4 × ast_cost) + (0.6 × temporal_cost)

# C. Hub penalty (god-node suppression)
if target_degree > max_calls_degree × 0.05:
    base_cost *= log(target_degree + 1) ** 1.5
```

The hub penalty prevents high-degree nodes (`main`, `__init__`, config loaders) from acting as universal connectors that make every node equidistant.

### Validation Results

| Query | Target | Rank | Status |
|-------|--------|------|--------|
| "memory ingestion pipeline hook processing" | `ingest_hook_payload (ingest.py)` | 1 | ✅ Clean |
| "retrieve context from memory for agent" | `memory_context_pack (tools.py)` | 1 | ✅ Clean |
| "store and save session memory snapshot" | `export_snapshot (snapshots.py)` | 7 | ⚠️ Partial |

**Q3 analysis (honest):** Ranks 1-6 include `memory_import`, `session_exists`, `memory_write` (legitimately related) and `_parse_metadata`, `_require_text` (PPR noise from structural proximity). Rank 7 for the most obvious target needs explaining, not just a checkmark. The result is promising but not clean.

**Ambiguous query:** `"something broke when the agent tried to remember"` returned `normalize_agent` (rank 1), `amo_graph_search` (rank 2), `search_memories` (rank 6). `amo_graph_search` and `search_memories` are correct. `normalize_agent` at rank 1 is PPR noise — it normalizes an agent identifier string, structurally connected to `tool_contracts` which was a high vector seed. No pre-committed ground truth was established before this run — the result was narrative-fit after the fact. This is a methodology gap, not a pipeline success.

### Warm Connector (Weighted Dijkstra)

```python
path = G.get_shortest_paths(source_idx, to=target_idx, weights=G.es["weight"], mode="out")
```

**Example path found:**
```
_utc_now (service.py)
→ main (cli.py)
→ _confirm (wizard.py)
→ _codex_hooks_cleanup_operation (service.py)
```

**Note:** This Dijkstra run used weights computed on the full graph (including CO_CHANGE and IMPORTS), and the hub penalty was calibrated against the inflated full-graph degree. Until the CO_CHANGE dedup bug is fixed, this path may route through artifically low-weight hub nodes.

---

## Known Issues and Next Steps

### 1. code_hash dedup not implemented (critical)

**What:** Every function in a touched file gets a new FunctionState node on every commit, even if its code didn't change.

**Why it matters:** Creates spurious CO_CHANGE edges between every function pair that ever shared a commit with a changed file. In the AMO sandbox this produced ~489k CO_CHANGE edges (21.8% of all possible pairs) — completely drowning the signal.

**Correct fix:**
```python
new_hash = _code_hash(code)
prev_state_id = state_tracker.get(func_id)
if prev_state_id:
    # fetch prev hash from HelixDB
    prev_hash = get_state_hash(c, prev_state_id)
    if prev_hash == new_hash:
        # code unchanged — skip new state, just update tracker
        continue
```

**Expected impact:** CO_CHANGE edges drop dramatically. Real co-change signal (functions that genuinely changed together across multiple commits) becomes usable as a Jaccard-weighted graph signal rather than noise to be discarded.

### 2. Ambiguous query needs pre-committed ground truth

Before running cold discovery on any ambiguous query, state the expected top-3 answer before seeing results. The current "validate after seeing output" approach cannot distinguish a correct pipeline from narrative-fitting.

### 3. Semantic summaries in AMO are structural/lexical, not semantic

The 1380 AMO function embeddings were computed from docstrings + mechanical AST descriptions (`function_name — calls X, Y — returns value`). The `_llm_summarize()` stub was never activated. Vector search in the AMO sandbox is running on auto-generated text, not meaning-aware summaries. This still works because function names and call patterns carry lexical signal, but it should not be described as semantic search until real summaries are in place.

### 4. Warm connector uses inflated-degree weights

The hub penalty in `compute_weights()` uses `calls_only.degree()` for the penalty calculation, but the penalty threshold was tuned on a graph where CO_CHANGE edges inflate connectivity. After the dedup fix, recalibrate the threshold.

---

## Performance Summary

| Tool | Before | After | Method | Speedup |
|------|--------|-------|--------|---------|
| `trace_blast_radius` depth=3 | 33.97ms | 4.03ms | `repeat().emit_all()` | **8.4x** |
| `get_temporal_vulnerability_trace` | 26.36ms | 4.20ms | Single chained query | **6.3x** |
| `semantic_pass` node patching | destroyed edges | preserved edges | `set_property` | correctness |

---

## How to Run

### Prerequisites
```bash
pip install helix-db gitpython tree-sitter tree-sitter-python igraph numpy
# HelixDB must be running on localhost:6969
```

### Start from scratch
```bash
cd graphdb
python scalable_ingest.py      # ingest commits, write to HelixDB + graph_payload.json
python semantic_pass.py        # patch ai_summary onto states
python dump_viz.py             # rebuild graph_viz.json
python tools/graph_mcp_server.py  # start MCP server on :7700
```

### Run validation suite
```bash
python tools/_test_queries.py   # 4-query validation
python tools/_test_bench.py     # performance benchmark
python tools/_test_3hop.py      # 3-hop traversal test
```

### AMO sandbox
```bash
cd graphdb
python sandbox/amo_ingest.py    # ~1380 embedding calls, takes ~5 minutes
python sandbox/igraph_sandbox.py  # full pipeline + 3-query validation
python sandbox/amo_query_test.py  # single ambiguous query test
python sandbox/amo_dump_viz.py    # generate viz JSON
# Open tools/harness_graph_viz.html → Load JSON → sandbox/amo_viz.json
```

### Re-ingest after HelixDB restart
HelixDB is in-memory — data is lost on restart. Always wipe before re-ingesting:
```bash
python -c "
from helixdb import Client, g, write_batch
c = Client('http://127.0.0.1:6969')
for label in ['FunctionState','FunctionIdentity','ClassIdentity','FileIdentity','Commit']:
    try: c.query().dynamic(write_batch().var_as('d', g().n_with_label(label).drop()).returning(['d']).to_dynamic_request()).send()
    except: pass
print('wiped')
"
python scalable_ingest.py
```
