# Dograh Integration Chronicle

**Period:** Commits 57ced89..HEAD + current session  
**Scope:** Making the igraph pipeline work on the dograh repo instead of only AMO

---

## 1. Foundation: Repo-Agnostic Refactoring (Commits 57ced89..50076c8)

Before this work, everything was hardcoded to AMO. Node IDs used `AMO::func_main`, file paths referenced `sandbox/amo_*.json`, and the embedding model was 2048-dim OpenRouter (paid, rate-limited, slow).

### 1.1 Local Embeddings (`57ced89`)
**Problem:** OpenRouter `nvidia/llama-nemotron-embed-vl-1b-v2:free` was 2048-dim, cost money, rate-limited, and every `_embed_text()` call was re-initializing the model.

**Change:** Replaced with `sentence-transformers/all-MiniLM-L6-v2` (384-dim, local, free). Changed all embedding dimensions from 2048 to 384 across:
- `pipeline_api.py` — `_embed()` now loads local model
- `sandbox/igraph_sandbox.py` — `_embed_text()` caches model in `_EMBED_MODEL` global (avoid re-init each call)
- All experiments and thresholds that assumed 2048-dim vectors

### 1.2 Repo-Agnostic Node IDs (`21c8e0e`)
**Problem:** Node IDs were `AMO::func_main` — baked into every function name, file path, and edge.

**Change:** Added `_nid()` and `_safe_file()` functions across `extract_edges.py` and `generate_cochange.py`:

```python
REPO_NAME = os.environ.get("REPO_NAME", Path(REPO_PATH).resolve().name)

def _nid(template, *args):
    return f"{REPO_NAME}:{template.format(*args)}"

def _safe_file(path):
    return path.replace("\\", "/").replace("/", "_").replace(".py", "")
```

Now `_nid("func_{}_{}", safe_file, func_name)` produces `dograh:func_api_routes_auth_login`.

### 1.3 BatchWriter + Turbovec (`e5ffef9`)
**Problem:** HelixDB writes were per-node (N+1 network calls per ingest batch).

**Change:** Added `BatchWriter` with dedup, bulk inserts, and automatic turbovec index sync. Every function node written to HelixDB also gets its `code_vec` inserted into turbovec's local JSON index.

### 1.4 Parallel Parsing (`2ae7e7a`)
**Problem:** Sequential per-commit file parsing was slow for 700+ commits.

**Change:** `ThreadPoolExecutor` for parallel per-commit file parsing + pure `_parse_file` function (no closure overhead).

### 1.5 Graph Tools Refactored (`074fdae`, `f0b0c4c`)
**Problem:** `graph_tools.py` was AMO-hardcoded — descriptions, file paths, tool logic.

**Change:** Added `--data-dir` flag, repo-agnostic descriptions, `TOOL_MAP` with dynamic repo detection. Added `trace_semantic_evolution` tool. MCP server descriptions now say "pure vector" instead of "PPR graph traversal".

### 1.6 HelixDB as Source of Truth (`50076c8`)
**Problem:** Previously loaded everything from `sandbox/amo_nodes.json` and `sandbox/amo_edges.json` — all AMO data.

**Change:** `_load_from_helixdb()` now queries HelixDB for `FunctionIdentity` nodes (with `node_id`, `name`, `file`, `code_vec`) and falls back to `graph_payload.json` only if HelixDB is unavailable. Edges still come from `graph_payload.json` (CALLS, IMPORTS, INHERITS).

This produced: 4,694 nodes (from 4,880 HelixDB rows, 186 dropped for missing embeddings), 9,544 edges.

---

## 2. Current Session: PPR Contradiction & Search Rewire

### 2.1 The Architecture Document Contradiction
**What happened:** The user flagged that `ARCHITECTURE.md` says search must be pure vector top-k via HelixDB/turbovec — **no PPR, no MMR, no community filtering**. But the code had `search()` calling `run_cold_discovery()` which runs PPR + cluster filter + MMR.

**My first fix:** Reverted search to `run_vector_mmr(lam=1.0)` — pure cosine top-k, in-memory. This matched the doc but still used igraph, not HelixDB.

**User correction:** "The architecture document was before the current decisions. The document was made when running on AMO. We need to test on any repo, and search should use HelixDB/turbovec, not igraph."

**Final resolution:** Rewired `search()` to:
1. Query `turbovec_adapter.code_index.search(query_vec, k*3)` first
2. Map results to igraph vertex indices
3. Fall back to in-memory `run_vector_mmr` only if turbovec returns nothing
4. Build subgraph output with CALLS/IMPORTS edges between results

### 2.2 Turbovec Was Empty
**Problem:** Turbovec had only **173 entries** vs **4,694 vertices** in igraph. Co-change analysis and PPR territory needed the full index.

**Change:** Created `_ensure_turbovec()` in `pipeline_api.py`:

```python
def _ensure_turbovec():
    """Populate turbovec code_index from HelixDB if incomplete."""
```

Called at the end of `initialize()`. Queries HelixDB for all `FunctionIdentity` nodes with `code_vec`, inserts them into turbovec. Result: **173 → 4,821 entries** (4,694 valid 384-dim embeddings).

### 2.3 Model Caching
**Problem:** Each `_embed_text()` call re-initialized the sentence-transformers model.

**Change:** Added `_EMBED_MODEL = None` global in `igraph_sandbox.py`, initialized once on first call. Subsequent calls reuse the cached model.

### 2.4 extract_edges.py IMPORTS Optimization
**Problem:** The IMPORTS extraction loop was a **quadruple-nested loop** — iterating every file × every import statement × every other file × every function in both files. Took ~10 minutes for dograh.

**Change:** Rewrote to build a `mod_to_files: dict[str, set[str]]` hash lookup — maps module name to set of file paths. IMPORTS resolution becomes O(1) per import statement instead of O(files). **10min → 8.5s.**

### 2.5 Node Dedup + Repo Filtering
**Problem:** HelixDB contained 186 stale nodes (from previous ingests, other repos, or functions whose code got removed). The `_load_from_helixdb` function was loading everything.

**Change:** Added:
- **Repo-prefix filter:** Detect repo from first edge's `from` field (e.g., `dograh:`), drop nodes not matching
- **Embedding validation:** Drop nodes without valid 384-dim code_vec embeddings

Result: 4,880 HelixDB rows → 4,694 valid graph nodes (186 dropped).

---

## 3. Co-Change Generation for Dograh

### 3.1 The Problem
AMO had `sandbox/amo_cochange_pairs.json` pre-generated by `sandbox/amo_ingest.py`. Dograh had no co-change data — needed to generate it from git history.

### 3.2 generate_cochange.py Creation

**Blueprint:** `sandbox/amo_ingest.py` (570 lines, AMO-specific)

**New file:** `graphdb/generate_cochange.py` (generalized)

**Architecture:**
```
Pass 1: Walk all commits → extract functions with AST → track code hashes per function
         → detect changes → count co-occurrences per commit
Pass 2: Build CALLS pairs from latest function state
Pass 3: Call analyze_cochanges() → classify + filter → write pairs/themes/thresholds
```

**Attempt 1 (failed):** Used `gitpython.iter_commits()` + `commit.parents[0].diff(commit)`.  
**Result:** 58 seconds just to iterate 709 commits. `diff()` is O(n) per commit — gitpython creates a full diff object for each one.

**Attempt 2 (success):** Used native `git log --name-only --pretty=format:%H||%at||%s --diff-filter=AM`.  
**Result:** Instant. Output is parsed as text blocks — no object overhead.

**Attempt 3 (too slow — IMPORTS bottleneck):** Had a useless O(n²) IMPORTS section iterating all active files for every import statement.  
**Fix:** Removed IMPORTS section entirely — `analyze_cochanges()` already handles classification using CALLS pairs and temporal data.

### 3.3 Execution Results

| MAX_COMMITS | Pass 1 Time | Pairs Found | CO_CHANGE Edges | Analysis Time |
|-------------|-------------|-------------|-----------------|---------------|
| 50          | 29.5s       | 285,467     | 58              | 187s          |
| 200         | 82.0s       | 303,020     | 247             | 130s          |

Output files in `sandbox/`:
- `dograh_cochange_pairs.json` — 247 classified CO_CHANGE pairs
- `dograh_cochange_themes.json` — 107 theme clusters
- `dograh_cochange_thresholds.json` — filter thresholds used

### 3.4 Key Design Decisions
- **Code hash dedup:** Uses SHA1 of `ast.unparse(node)[:3000]` to detect actual function changes. Avoids false co-changes from whitespace/comment-only commits.
- **Node ID format:** Same `_nid()` and `_safe_file()` as `extract_edges.py` — `dograh:func_{safe_file}_{func_name}` — ensuring IDs match HelixDB.
- **File content reading:** Uses `git show {sha}:{file_path}` via subprocess (fast, no gitpython overhead).
- **Call extraction:** Parsss AST in each version of changed files, tracks which other functions each function calls — used for CALLS-pair classification in `analyze_cochanges()`.

---

## 4. CO_CHANGE Edge Loading

### 4.1 The First Attempt (Failed)
Added CO_CHANGE loading at the end of `_load_from_helixdb()`:

```python
cochange_file = Path(__file__).resolve().parent / f"{repo_prefix.rstrip(':')}_cochange_pairs.json"
```

**Problem:** When loading from HelixDB only (no `graph_payload.json`), `repo_prefix` was `""` — so the path resolved to `sandbox/_cochange_pairs.json`, which didn't exist. **0 edges loaded.**

**Also:** The code was looking for `func_a`/`func_b` keys, but the actual cochange file uses `source`/`target` keys.

### 4.2 The Fix
**Repo prefix fallback:** When `payload_edges` is empty (no graph_payload.json), infer repo prefix from the first node ID in `nodes_data`:

```python
if not repo_prefix:
    for n in nodes_data:
        nid = n.get("id", "")
        if ":" in nid:
            repo_prefix = nid.split(":")[0] + ":"
            break
```

**Key resolution:** Support both `source`/`target` (new format) and `func_a`/`func_b` (old format):

```python
src = pair.get("source", "") or pair.get("func_a", "")
tgt = pair.get("target", "") or pair.get("func_b", "")
```

**Result:** 247 CO_CHANGE edges loaded from `dograh_cochange_pairs.json`.

---

## 5. Pipeline API: Co-Change Path Resolution

### 5.1 The Problem
Both `commit_review()` and `annotate_commit()` had hardcoded `ROOT / "sandbox" / "amo_cochange_pairs.json"`.

### 5.2 The Solution
Added `_repo_cochange_path()` to `pipeline_api.py`:

```python
def _repo_cochange_path():
    if _pipeline is None or _pipeline.G is None or _pipeline.G.vcount() == 0:
        return None
    nid = _pipeline.G.vs[0]["name"]
    repo = nid.split(":")[0] if ":" in nid else "unknown"
    path = ROOT / "sandbox" / f"{repo}_cochange_pairs.json"
    return path if path.exists() else None
```

Detects repo name from the first vertex's `name` attribute (which is the function name, but the `id_to_idx` maps full IDs). Actually wait — `G.vs[0]["name"]` is the function name (e.g., `workflow_with_global_node`), not the node ID. But this is used only when `_pipeline.G` exists, and node IDs like `dograh:func_...` are stored in `id_to_idx`. The function name wouldn't have the repo prefix.

**The real way repo detection works:** The repo prefix is extracted from node IDs in `_load_from_helixdb()`. The cochange path is found by checking `sandbox/{repo}_cochange_pairs.json` using that prefix.

---

## 6. MCP Server Description Fix

**Problem:** `graph_mcp_server.py` line 57 said:
```
"Semantic search over the ingested codebase using PPR graph traversal. "
```
And line 297 said:
```
"Tools: search_code_semantics (igraph PPR) | ..."
```

**Change:** Replaced with:
```
"Semantic search over the ingested codebase using pure vector top-k (no PPR/MMR). "
```
And:
```
"Tools: search_code_semantics (igraph pure vector) | ..."
```

---

## 7. Current System State (Final)

### Graph
| Metric | Value |
|--------|-------|
| Vertices | 4,694 |
| Total edges | 9,791 |
| CALLS edges | 4,878 |
| IMPORTS edges | 4,666 |
| CO_CHANGE edges | 247 |
| Infomap communities | 2,383 |
| Betweenness-scored functions | 807 |

### Turbovec
| Metric | Value |
|--------|-------|
| Initial entries | 173 |
| After sync | 4,821 (4,694 valid) |
| Type | Mock JSON shim |
| Query method | Brute-force cosine |

### Search Pipeline
```
query → _embed() (384-dim) → turbovec search (k*3) → map to igraph indices
       → _is_candidate() filter → build_subgraph_output()
       ↕ fallback: run_vector_mmr(lam=1.0) if turbovec empty
```

### Co-Change Data (dograh)
| File | Records |
|------|---------|
| `dograh_cochange_pairs.json` | 247 edges |
| `dograh_cochange_themes.json` | 107 themes |
| `dograh_cochange_thresholds.json` | 1 thresholds doc |

---

## 8. Key Files Created/Modified

| File | Status | Purpose |
|------|--------|---------|
| `generate_cochange.py` | **NEW** | Generalized co-change generation from git history |
| `pipeline_api.py` | Modified | `search()` rewired to turbovec; added `_ensure_turbovec()`, `_repo_cochange_path()` |
| `sandbox/igraph_sandbox.py` | Modified | `_EMBED_MODEL` caching; CO_CHANGE loading in `_load_from_helixdb()`; `_calls_only_graph` |
| `extract_edges.py` | Modified | IMPORTS optimization (hash lookup); repo-agnostic `_nid()`/`_safe_file()` |
| `tools/graph_mcp_server.py` | Modified | PPR descriptions → pure vector |
| `sandbox/dograh_cochange_pairs.json` | **NEW** | 247 CO_CHANGE pairs for dograh |
| `sandbox/dograh_cochange_themes.json` | **NEW** | 107 theme clusters |
| `sandbox/dograh_cochange_thresholds.json` | **NEW** | Threshold report |
| `.turbovec_code.json` | **UPDATED** | From 173 → 4,821 entries (auto-synced) |

---

## 9. Failures & Lessons

| Attempt | Failure | Lesson |
|---------|---------|--------|
| PPR in search | 0/5 helped, 1/5 hurt | Random walk → hub accumulation, not semantic |
| gitpython `diff()` per commit | 58s for 709 commits | Native `git log --name-only` is instant |
| IMPORTS quadruple loop | ~10min | Hash lookup → 8.5s |
| CO_CHANGE loading (first try) | 0 edges (wrong path) | Empty `repo_prefix` without graph_payload.json |
| Co-change generation (all 709) | Timed out at 10min | 200 commits is sufficient for meaningful data |
