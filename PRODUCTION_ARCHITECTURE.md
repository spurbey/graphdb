# GraphDB: Complete Project Archaeology & Production Architecture Proposal

> **Purpose:** This document traces the entire evolution of GraphDB from its first commit to its current experimental state, catalogs every subsystem, documents what was built/scrapped/adopted, and proposes a clean production-ready architecture for a new codebase.

---

## Table of Contents

1. [Project Timeline & Evolution](#1-project-timeline--evolution)
2. [Current Monolith Anatomy](#2-current-monolith-anatomy)
3. [Subsystem Catalog](#3-subsystem-catalog)
4. [What Stuck vs What Was Scrapped](#4-what-stuck-vs-what-was-scrapped)
5. [Production Architecture Proposal](#5-production-architecture-proposal)
6. [Proposed File Structure](#6-proposed-file-structure)
7. [Dependency Matrix](#7-dependency-matrix)
8. [Migration Checklist](#8-migration-checklist)

---

## 1. Project Timeline & Evolution

The project spans **65 commits** over **28 days** (June 24 – July 21, 2026). It evolved through 7 distinct phases.

### Phase 1: Foundation (June 24–25) — 5 commits
```
623241a  feat: initial auth module with models and service
34344c3  feat: tree-sitter ingestion, HelixDB writes, semantic pass, viz dump
364d736  fix: cross-file CALLS resolution, filter stdlib noise
c901594  fix: skip tooling files from ingestion, add NEXT_COMMIT timeline chain
eef5695  feat: add validation, error handling, and account management to auth module
```

**What happened:** The `auth/` module was created as a **toy target repository** to test ingestion. `scalable_ingest.py` was born — using tree-sitter to parse Python AST, GitPython to walk commit history, and HelixDB to store the graph. The core node schema was established: `Commit → GENERATED → FunctionState ← HAS_STATE ← FunctionIdentity`. Cross-file `CALLS` edge resolution was fixed. Standard library noise was filtered out.

### Phase 2: Schema Refinement & Agent Tooling (June 25–27) — 10 commits
```
5d3d872  refactor: flatten graph schema — remove ChangeNode/Concept/Rationale
4d5601b  feat: agent tooling — status tracking, vector index, MCP server
097981a  fix: resolve all traversal bugs — set_property patch, multi-hop Python join
81cc82a  feat: edit_code tool — closes the agentic loop
0051850  feat: vector embeddings via OpenRouter nvidia/llama-nemotron-embed-vl-1b-v2:free
4fd86ef  fix: vector search quality — sharper summaries, k*3 candidate pool
```

**What happened:** The original over-engineered schema (`ChangeNode`, `Concept`, `Rationale` nodes) was **flattened** — `ai_summary` moved onto `FunctionState`, `ai_rationale` onto `Commit`. The first 5 MCP tools were built. A critical HelixDB bug was discovered: `set_property` was the only safe way to update nodes (drop+reinsert destroyed edges). Vector embeddings were added via OpenRouter's free Nemotron model (2048-dim).

**Key failure:** String property vector indexing caused silent insert failures → fixed by creating dedicated `ai_summary_vec` float32 array property.

### Phase 3: Performance & Cold Discovery (June 30 – July 3) — 6 commits
```
319b7ee  perf: replace N+1 hop loop with repeat().emit_all() single Rust traversal
5f6cda8  perf: collapse get_temporal_vulnerability_trace from N+1 loops to single Rust query
86abbe5  feat: cold discovery pipeline for agent-memory-orchestrator
566afc8  refactor: move AMO igraph sandbox into sandbox/ folder
c4040e7  docs: add ARCHITECTURE.md — full engineering record
```

**What happened:** Client-side N+1 traversal loops were replaced with single HelixDB Rust-native `repeat().emit_all()` queries (8.4x speedup: 33.97ms → 4.03ms). The **AMO cold discovery pipeline** was born — ingesting the full `agent-memory-orchestrator` repo (2120 nodes, 100 commits) into the sandbox. `igraph_sandbox.py` became the central algorithm lab. `ARCHITECTURE.md` was written as the first formal engineering record.

### Phase 4: GraphSAGE Experiments (July 9) — 3 commits
```
9221a86  feat: prepare GraphSAGE data from sandbox AMO nodes/edges
6324596  feat: train 2-layer GraphSAGE mean aggregator for link prediction
663103e  feat: GraphSAGE reranking and inductive probe for new AMO commits
```

**What happened:** A 2-layer GraphSAGE GNN was trained on the AMO CALLS graph for link prediction (AUC ~0.813). The key finding: **GraphSAGE embeddings encode structural role/call-depth, not semantic intent.** They group functions by architectural tier (e.g., all "write to storage" functions cluster together regardless of their domain). This became the basis for `find_structural_siblings`.

### Phase 5: Retrieval Pipeline R&D (July 11–12) — 22 commits (the densest phase)
```
a43234e  phase 0: fix retrieval pipeline foundation
00a6a10  phase 2: integrate theme overlay into igraph_sandbox.py
022fb5b  docs: add PIPELINE_SPEC.md
4c87da1  fix+revert: seed floor weighting was no-op, reverted
ff34155  fix+revert: Option A' soft cluster filter tested and failed
4ca921a  feat: set top_k_seeds=20 for PPR
f26b087  feat: Step 4 complete — wire igraph pipeline into MCP tool interface
82d4f3d  exp1: BFS vs PPR results — nuanced outcome, not clean BFS win
5159172  exp4: CO_CHANGE bridges add nothing to retrieval — SKIP
b31db09  exp6: MMR lambda=0.8 wins — adopt as default retrieval mode
cf67d84  revert: MMR lambda=0.8 reverted — wrong for function retrieval
bcc6e42  exp5: GraphSAGE structural siblings — finds architectural tier groupings
3a96760  feat: add find_structural_siblings tool using GraphSAGE embeddings
305f2c8  eval: full tool evaluation — 5x token reduction demonstrated
```

**What happened:** This was the most intense R&D sprint. The retrieval pipeline went through:
- **Vector seeds → Infomap clustering → PPR → MMR** was established
- **Seed floor weighting** tested and reverted (no-op)
- **Soft cluster filter (Option A')** tested and reverted (god-node flooding)
- **BFS vs PPR** tested — neither beat pure vector search for query-time retrieval
- **CO_CHANGE bridges** tested — added nothing (sparse edges in 100-commit slice)
- **MMR λ=0.8** adopted then **reverted** (penalized semantically related but distinct functions)
- **GraphSAGE structural siblings** → adopted as `find_structural_siblings` tool

**The paradigm shift:** PPR was demoted from "query-time reranker" to "post-anchor exploration tool." The unified pipeline was replaced by a **multi-tool architecture** with 8 specialized tools. Product eval showed **5x token reduction** vs grep-only baseline.

### Phase 6: CI/CD Intelligence & Commit Review (July 13–17) — 7 commits
```
d0c8317  docs: add ARCHITECTURE_REPORT.md — complete retrospective
8cee7f9  Add landing page website for GraphDB
efc3110  analysis: betweenness centrality check on AMO CALLS graph
029ba0b  feat: add commit_review and select_tests tools
0bc06b0  docs: add COMMIT_REVIEW_LOG.md
```

**What happened:** Betweenness centrality analysis revealed architectural chokepoints. Two new CI/CD tools were built:
- `commit_review()`: 5-layer impact analysis (blast radius + betweenness + GraphSAGE drift + CO_CHANGE warnings + PPR territory)
- `select_tests()`: Multi-algorithm test scoping (99% test reduction: 4/311 tests selected)

A marketing landing page was built (`website/` and `website-v3/` with React + Three.js).

### Phase 7: Semantic Memory System (July 19–21) — 8 commits
```
7856ccc  spec: FUNCTION_MEMORY_SPEC.md — full build spec
0a28b02  phase1: add memory/memory_vec to FunctionState schema + INTRODUCED edge
4bb1b84  phase2: add 4 sub-agent tools for /annotate-commit
1607912  phase3-5: AGENTS.md, system prompt, evals, bug fixes
a1e463d  phase4-7: add annotate_commit + query_function_history, wire to MCP
133802f  fix(pipeline): resolve bugs 1 and 2 in read_function_memory_history
542b252  feat(pipeline): implement phase 7 memory enhancements
```

**What happened:** The most architecturally significant addition. `FunctionState` nodes gained persistent semantic memory (`memory` text + `memory_vec` embedding). Commits now create typed edges to function states: `REDESIGNED`, `FIXED`, `EXTENDED`, `REFACTORED`, `INTRODUCED`. Two slash commands were built: `/annotate-commit` (LLM-driven commit annotation) and `/query-history` (vector search over design decision timeline). `trace_semantic_evolution` was added as the recursive graph traversal tool that ties everything together.

---

## 2. Current Monolith Anatomy

The root directory is a flat dump of 40 files and 13 directories with no separation between engine, experiments, demos, patches, and dead code.

### Root-Level Files (40 files)

| File | Size | Role | Status |
|:-----|:-----|:-----|:-------|
| `pipeline_api.py` | 51 KB | Central intelligence engine (PPR, commit review, memory) | **CORE** |
| `scalable_ingest.py` | 20 KB | Git→AST→HelixDB ingestion engine | **CORE** |
| `semantic_pass.py` | 10 KB | Post-ingestion semantic enrichment | **CORE** |
| `full_eval.py` | 13 KB | Product evaluation harness (Condition A vs B) | Experimental |
| `product_test.py` | 18 KB | Product test (co-change explanation) | Experimental |
| `product_demo.py` | 8 KB | Product demo script | Experimental |
| `ppr_vs_mmr.py` | 10 KB | PPR vs MMR comparison experiment | Experimental |
| `query_comparison.py` | 13 KB | Query comparison benchmark | Experimental |
| `test_commit_review.py` | 1.4 KB | Commit review test | Test |
| `_apply_fixes.py` | 11 KB | One-off bug fix script | Dead code |
| `_patch_mcp.py` | 2.7 KB | One-off MCP patch | Dead code |
| `_patch_mcp2.py` | 3.2 KB | One-off MCP patch v2 | Dead code |
| `_patch_tools.py` | 5.8 KB | One-off tools patch | Dead code |
| `_read_convo2.py` | 0.7 KB | Conversation reader utility | Dead code |
| `check_json.py` | 1.9 KB | JSON validation utility | Dead code |
| `dump_viz.py` | 1.7 KB | Visualization dumper | Dead code |
| `level_1_parser.py` | 1.6 KB | Early parser prototype | Dead code |
| `mock_stdio_mcp.py` | 3 KB | MCP stdio bridge (this session) | Dead code |
| `patch_semantics.py` | 2.4 KB | Semantic patching utility | Dead code |
| `verify_semantics.py` | 5.3 KB | Semantic verification script | Dead code |
| `graph_payload.json` | 1.5 MB | Generated ingestion output | Generated artifact |
| `graph_viz.json` | 1.6 MB | Generated visualization data | Generated artifact |
| `graphsage.ipynb` | 2.2 KB | GraphSAGE notebook stub | Experimental |
| `graphsage_minimal_bundle.zip` | 10.6 MB | GraphSAGE model archive | Generated artifact |
| `convo1` | 4.2 MB | Agent conversation log | Debug artifact |
| `convo1_clean.json` | 1.2 MB | Cleaned conversation | Debug artifact |
| `convo2.json` | 2.5 MB | Agent conversation log | Debug artifact |
| `ingestion_process.txt` | 6.9 KB | Ingestion tutorial/blueprint | Documentation |
| `mcp.json` | 215 B | MCP server config | Config |
| `.env` | 359 B | API keys | Config |

### Directories (13 directories)

| Directory | Role | Status |
|:----------|:-----|:-------|
| `tools/` | Agent toolset + MCP server + test harnesses | **CORE** |
| `sandbox/` | AMO dataset + algorithm lab (22 .py scripts, ~200MB JSON) | Experimental |
| `auth/` | Toy target repository for testing ingestion | Demo fixture |
| `evals/` | 7-phase evaluation scripts for semantic memory | Test |
| `tests/` | 2 unit tests (co-change analysis, embedding cache) | Test |
| `skills/` | LLM prompt templates for `/annotate-commit` | **CORE** |
| `graphsage_minimal/` | GNN training scripts + model artifacts | Experimental |
| `website/` | React landing page v1 | Marketing |
| `website-v3/` | React + Three.js landing page v3 | Marketing |
| `.kiro/` | IDE configuration | Config |
| `.git/` | Git repository | VCS |
| `__pycache__/` | Python cache | Generated |
| `.pytest_cache/` | Pytest cache | Generated |

---

## 3. Subsystem Catalog

The experimental codebase contains **6 distinct subsystems** that were developed organically.

### 3.1 Ingestion Subsystem
**Files:** `scalable_ingest.py`, `semantic_pass.py`
**What it does:** Walks a Git repository's commit history using GitPython, parses Python files with tree-sitter AST, extracts structural nodes (`Commit`, `FileIdentity`, `ClassIdentity`, `FunctionIdentity`, `FunctionState`), builds relationship edges (`CONTAINS`, `IMPORTS`, `INHERITS`, `CALLS`, `HAS_STATE`, `GENERATED`, `PREVIOUS_VERSION`, `NEXT_COMMIT`, `INTRODUCED`), computes 2048-dim OpenRouter embeddings, and writes everything to HelixDB.
**Dependencies:** GitPython, tree-sitter, tree-sitter-python, HelixDB client, OpenRouter API.

### 3.2 Retrieval Subsystem
**Files:** `pipeline_api.py`, `sandbox/igraph_sandbox.py`
**What it does:** Loads the graph from HelixDB into an in-memory igraph representation. Runs Infomap community detection on the CALLS subgraph. Performs vector-seeded Personalized PageRank (PPR) with hub penalty, proportional community weighting, and consumer-mode policy filtering. Outputs ranked subgraphs with nodes + edges.
**Dependencies:** igraph, numpy, HelixDB client, OpenRouter API.

### 3.3 Analysis Subsystem
**Files:** `pipeline_api.py` (commit_review, select_tests, explain_coupling functions)
**What it does:** Multi-algorithm impact analysis combining betweenness centrality, GraphSAGE structural drift detection, CO_CHANGE historical warnings, blast radius traversal, and PPR downstream territory mapping. Powers `commit_review()` and `select_tests()`.
**Dependencies:** igraph, numpy, GraphSAGE embeddings (128-dim .npy file).

### 3.4 Semantic Memory Subsystem
**Files:** `pipeline_api.py` (annotate_commit, query_function_history, write_function_memory, read_function_memory_history), `skills/annotate_commit_prompt.md`
**What it does:** Adds persistent memory to function evolution. An LLM sub-agent reads commit diffs, classifies each change as `REDESIGNED`/`FIXED`/`EXTENDED`/`REFACTORED`, writes a memory note explaining the "what and why," and creates a typed edge from `Commit` to `FunctionState`. `query_function_history` performs vector search over the memory timeline.
**Dependencies:** OpenRouter API (Claude for classification, Nemotron for embeddings), GitPython, tree-sitter, HelixDB client.

### 3.5 Agent Toolset Subsystem
**Files:** `tools/graph_tools.py`
**What it does:** Thin wrapper exposing 14 Python functions to agents. Delegates to `pipeline_api.py` for graph intelligence and directly to HelixDB for raw traversals (`trace_blast_radius`, `get_code_time_travel_diff`, `get_temporal_vulnerability_trace`). Also includes `edit_code` for AST-based code patching with automatic re-ingestion.
**Dependencies:** pipeline_api, scalable_ingest, HelixDB client, OpenRouter API.

### 3.6 MCP Server Subsystem
**Files:** `tools/graph_mcp_server.py`
**What it does:** HTTP server on port 7700 exposing all 14 tools via `GET /manifest` (tool discovery) and `POST /call` (tool execution). Custom protocol — NOT standard MCP JSON-RPC.
**Dependencies:** Python http.server, graph_tools.

### 3.7 GraphSAGE Subsystem (Experimental, Partially Adopted)
**Files:** `graphsage_minimal/` (6 files)
**What it does:** Trains a 2-layer GraphSAGE mean aggregator on the CALLS graph for link prediction. Outputs 128-dim structural embeddings per function. Used only by `find_structural_siblings`.
**Dependencies:** PyTorch, numpy, sklearn.
**Status:** Training pipeline is experimental. The pre-computed embeddings (.npy file) are consumed by production tools.

---

## 4. What Stuck vs What Was Scrapped

### ✅ Adopted into Production

| Feature | Commit(s) | Status |
|:--------|:----------|:-------|
| tree-sitter AST ingestion | `34344c3` | Core |
| Dual identity model (FunctionIdentity + FunctionState) | `34344c3` | Core |
| HelixDB as graph backend | `34344c3` | Core |
| Flattened schema (ai_summary on FunctionState) | `5d3d872` | Core |
| `set_property` for safe node updates | `097981a` | Core |
| OpenRouter Nemotron 2048-dim embeddings | `0051850` | Core |
| Single Rust traversals (repeat().emit_all()) | `319b7ee` | Core |
| Infomap community detection on CALLS subgraph | `a43234e` | Core |
| PPR with hub penalty + proportional weighting | `4ca921a` | Core |
| top_k_seeds=20 for PPR | `4ca921a` | Core |
| Multi-tool architecture (replacing unified pipeline) | `f26b087` | Core |
| GraphSAGE structural siblings | `3a96760` | Core |
| 5-layer commit_review | `029ba0b` | Core |
| select_tests multi-algorithm scoping | `029ba0b` | Core |
| Semantic memory on FunctionState | `0a28b02` | Core |
| Typed commit→function edges (REDESIGNED/FIXED/EXTENDED/REFACTORED) | `4bb1b84` | Core |
| /annotate-commit with LLM classification | `a1e463d` | Core |
| /query-history vector search over memory timeline | `a1e463d` | Core |
| trace_semantic_evolution recursive traversal | `a1e463d` | Core |

### ❌ Scrapped / Reverted

| Feature | Commit(s) | Why It Failed |
|:--------|:----------|:--------------|
| ChangeNode/Concept/Rationale nodes | `5d3d872` (removed) | Over-engineered; flattened into properties |
| Drop+reinsert node updates | `097981a` (fixed) | Destroyed all connected edges |
| Client-side N+1 hop loops | `319b7ee` (replaced) | 8.4x slower than Rust traversal |
| Seed floor weighting (SEED_FLOOR=0.3) | `4c87da1` (reverted) | No score improvement; no-op |
| Soft cluster filter (Option A') | `ff34155` (reverted) | God-node/utility flooding |
| BFS as query-time reranker | `82d4f3d` (rejected) | Flooded results with 10 god-nodes |
| CO_CHANGE bridges for retrieval | `5159172` (skipped) | 0 new bridges; sparse edge coverage |
| MMR λ=0.8 as default | `cf67d84` (reverted) | Penalized semantically distinct but related functions |
| File-level deduplication | (exp6, rejected) | Neutral results, unnecessary complexity |
| PPR as query-time reranker | `d0c8317` (demoted) | Unconditioned random walk pools mass in hubs |

---

## 5. Production Architecture Proposal

### 5.1 Design Principles

1. **Separation of Concerns:** Each subsystem gets its own Python package with clear boundaries.
2. **No Experimental Code in Production:** All sandbox scripts, one-off patches, conversation logs, and dead code stay in the experimental repo.
3. **Plugin Architecture for Backends:** HelixDB is the current backend, but the architecture should allow swapping to Neo4j, Kùzu, or an embedded graph DB.
4. **Standard MCP Protocol:** Replace the custom HTTP server with a proper JSON-RPC stdio MCP server that works with Antigravity, Cursor, Kiro, etc.
5. **Docker-First Distribution:** Single `docker-compose up` to run HelixDB + GraphDB server.
6. **CLI Entry Point:** `graphdb ingest`, `graphdb serve`, `graphdb annotate <sha>`.

### 5.2 Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                     IDE / Agent Client                      │
│              (Antigravity, Cursor, Kiro, etc.)              │
└──────────────────────────┬──────────────────────────────────┘
                           │ MCP (JSON-RPC stdio or SSE)
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                      graphdb-server                         │
│                  (MCP Protocol Adapter)                     │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  Tool Router  │  │  Tool Schema │  │  Auth / Config   │  │
│  └──────┬───────┘  └──────────────┘  └──────────────────┘  │
│         │                                                   │
│         ▼                                                   │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                   Tool Registry                     │    │
│  │                                                     │    │
│  │  search_code_semantics    trace_semantic_evolution   │    │
│  │  find_structural_siblings  explain_coupling          │    │
│  │  commit_review            select_tests               │    │
│  │  annotate_commit          query_function_history     │    │
│  │  trace_blast_radius       get_time_travel_diff       │    │
│  │  get_vulnerability_trace  edit_code                  │    │
│  │  pipeline_status                                     │    │
│  └─────────────────────────┬───────────────────────────┘    │
└────────────────────────────┼────────────────────────────────┘
                             │
            ┌────────────────┼────────────────┐
            ▼                ▼                ▼
┌──────────────────┐ ┌──────────────┐ ┌──────────────────┐
│    Retrieval      │ │  Ingestion   │ │  Memory          │
│    Engine         │ │  Engine      │ │  Engine          │
│                   │ │              │ │                  │
│ igraph PPR        │ │ GitPython    │ │ LLM Annotation   │
│ Infomap           │ │ tree-sitter  │ │ Vector Search    │
│ Hub penalty       │ │ AST parsing  │ │ Edge Typing      │
│ Consumer modes    │ │ Embedding    │ │ Co-occurrence    │
│ Betweenness       │ │ Dedup        │ │                  │
│ GraphSAGE k-NN    │ │              │ │                  │
└────────┬─────────┘ └──────┬───────┘ └────────┬─────────┘
         │                  │                   │
         └──────────────────┼───────────────────┘
                            ▼
              ┌──────────────────────────┐
              │     Storage Adapter      │
              │   (Backend Interface)    │
              ├──────────────────────────┤
              │  HelixDB  │  Kùzu  │ .. │
              └──────────────────────────┘
                            │
                            ▼
              ┌──────────────────────────┐
              │     Graph Database       │
              │   (HelixDB on :6969)     │
              └──────────────────────────┘
```

---

## 6. Proposed File Structure

```
graphdb/
├── pyproject.toml                    # Package definition, dependencies, CLI entry points
├── Dockerfile                        # Build the graphdb-server image
├── docker-compose.yml                # HelixDB + graphdb-server
├── README.md                         # User-facing quickstart
├── LICENSE
│
├── src/
│   └── graphdb/
│       ├── __init__.py
│       ├── cli.py                    # CLI entry: graphdb ingest | serve | annotate
│       ├── config.py                 # Configuration loading (.env, env vars, defaults)
│       │
│       ├── ingestion/                # === Ingestion Engine ===
│       │   ├── __init__.py
│       │   ├── git_walker.py         # GitPython commit traversal (from scalable_ingest.py)
│       │   ├── ast_parser.py         # tree-sitter AST extraction (from scalable_ingest.py)
│       │   ├── embedder.py           # OpenRouter embedding client (from scalable_ingest.py)
│       │   ├── graph_builder.py      # Node/edge construction logic (from scalable_ingest.py)
│       │   ├── semantic_pass.py      # Post-ingestion enrichment (from semantic_pass.py)
│       │   └── schema.py             # Node/edge type definitions & constants
│       │
│       ├── retrieval/                # === Retrieval Engine ===
│       │   ├── __init__.py
│       │   ├── pipeline.py           # initialize(), search(), status() (from pipeline_api.py)
│       │   ├── ppr.py                # Personalized PageRank logic (from igraph_sandbox.py)
│       │   ├── infomap.py            # Infomap community detection (from igraph_sandbox.py)
│       │   ├── hub_penalty.py        # Hub suppression logic (from igraph_sandbox.py)
│       │   ├── consumer_modes.py     # Mode-specific edge/node filtering policies
│       │   └── graphsage.py          # GraphSAGE embedding loader + k-NN (from pipeline_api.py)
│       │
│       ├── analysis/                 # === Analysis Engine ===
│       │   ├── __init__.py
│       │   ├── commit_review.py      # 5-layer impact analysis (from pipeline_api.py)
│       │   ├── test_selector.py      # Minimum test set scoping (from pipeline_api.py)
│       │   ├── coupling.py           # CO_CHANGE explanation (from pipeline_api.py)
│       │   └── centrality.py         # Betweenness centrality (from pipeline_api.py)
│       │
│       ├── memory/                   # === Semantic Memory Engine ===
│       │   ├── __init__.py
│       │   ├── annotator.py          # LLM-driven commit annotation (from pipeline_api.py)
│       │   ├── history.py            # read/write function memory (from pipeline_api.py)
│       │   ├── evolution.py          # trace_semantic_evolution (from graph_tools.py)
│       │   └── prompts/
│       │       └── annotate_commit.md  # LLM system prompt (from skills/)
│       │
│       ├── tools/                    # === Agent Tool Definitions ===
│       │   ├── __init__.py
│       │   ├── registry.py           # Central tool registry mapping names → functions
│       │   ├── search.py             # search_code_semantics, search_helix
│       │   ├── traversal.py          # blast_radius, time_travel_diff, vulnerability_trace
│       │   ├── structural.py         # find_structural_siblings
│       │   ├── historical.py         # explain_coupling, query_function_history
│       │   ├── ci.py                 # commit_review, select_tests, annotate_commit
│       │   └── edit.py               # edit_code with AST replacement + re-ingest
│       │
│       ├── server/                   # === MCP Server ===
│       │   ├── __init__.py
│       │   ├── stdio_server.py       # Standard JSON-RPC MCP over stdio
│       │   ├── sse_server.py         # Optional SSE transport for web clients
│       │   └── protocol.py           # JSON-RPC message framing & validation
│       │
│       └── storage/                  # === Storage Adapter ===
│           ├── __init__.py
│           ├── base.py               # Abstract interface (read/write/traverse)
│           ├── helixdb.py            # HelixDB implementation (from graph_tools.py)
│           └── embedded.py           # Future: embedded graph DB fallback
│
├── tests/                            # === Test Suite ===
│   ├── __init__.py
│   ├── test_ingestion.py
│   ├── test_retrieval.py
│   ├── test_analysis.py
│   ├── test_memory.py
│   ├── test_tools.py
│   ├── test_server.py
│   └── fixtures/
│       ├── auth/                     # Toy repo for ingestion tests (from auth/)
│       └── mock_memories.json        # Mock graph data (from sandbox/)
│
├── benchmarks/                       # === Evaluation Harnesses ===
│   ├── eval_token_reduction.py       # (from full_eval.py)
│   ├── eval_retrieval_quality.py     # (from product_test.py)
│   └── eval_memory_annotation.py     # (from evals/)
│
└── docs/                             # === Documentation ===
    ├── architecture.md               # (from ARCHITECTURE.md)
    ├── pipeline_spec.md              # (from PIPELINE_SPEC.md)
    ├── experiments.md                # (from RETRIEVAL_ARCH_EXPERIMENTS.md)
    ├── commit_review.md              # (from COMMIT_REVIEW_LOG.md)
    ├── memory_spec.md                # (from FUNCTION_MEMORY_SPEC.md)
    └── retrospective.md             # (from ARCHITECTURE_REPORT.md)
```

---

## 7. Dependency Matrix

### Production Dependencies

| Package | Used By | Purpose |
|:--------|:--------|:--------|
| `helixdb` | storage, ingestion, tools | Graph database client |
| `gitpython` | ingestion, memory | Git commit traversal |
| `tree-sitter` | ingestion | AST parsing engine |
| `tree-sitter-python` | ingestion | Python grammar for tree-sitter |
| `python-igraph` | retrieval | In-memory graph for PPR/Infomap |
| `numpy` | retrieval, analysis, memory | Vector arithmetic, cosine similarity |
| `python-dotenv` | config | Environment variable loading |

### Optional Dependencies

| Package | Used By | Purpose |
|:--------|:--------|:--------|
| `torch` | graphsage training only | GNN model training (not needed at runtime) |

### External Services

| Service | Port | Purpose |
|:--------|:-----|:--------|
| HelixDB | 6969 | Graph database backend |
| OpenRouter API | HTTPS | Embeddings (Nemotron) + LLM reasoning (Claude) |

---

## 8. Migration Checklist

### Phase 1: Scaffolding
- [ ] Create new repository with the proposed file structure
- [ ] Set up `pyproject.toml` with all production dependencies
- [ ] Write `Dockerfile` and `docker-compose.yml`
- [ ] Implement `cli.py` with `ingest`, `serve`, `annotate` commands

### Phase 2: Extract Core Engine
- [ ] Extract `scalable_ingest.py` → `ingestion/git_walker.py` + `ast_parser.py` + `graph_builder.py`
- [ ] Extract `semantic_pass.py` → `ingestion/semantic_pass.py`
- [ ] Extract `igraph_sandbox.py` → `retrieval/ppr.py` + `infomap.py` + `hub_penalty.py`
- [ ] Extract `pipeline_api.py` search/status → `retrieval/pipeline.py`
- [ ] Extract `pipeline_api.py` commit_review/select_tests → `analysis/commit_review.py` + `test_selector.py`
- [ ] Extract `pipeline_api.py` memory functions → `memory/annotator.py` + `history.py`
- [ ] Extract `graph_tools.py` → individual tool files in `tools/`

### Phase 3: Storage Abstraction
- [ ] Define `storage/base.py` abstract interface
- [ ] Implement `storage/helixdb.py` wrapping all HelixDB-specific queries
- [ ] Ensure all engine code calls storage adapter, not HelixDB directly

### Phase 4: MCP Server
- [ ] Implement standard JSON-RPC stdio server in `server/stdio_server.py`
- [ ] Wire tool registry to server
- [ ] Test with Antigravity, Cursor, and Kiro

### Phase 5: Docker & Distribution
- [ ] Build and test Docker image
- [ ] Test `docker-compose up` end-to-end (HelixDB + graphdb-server)
- [ ] Publish image to Docker Hub or GitHub Container Registry

### Phase 6: Testing & Docs
- [ ] Port sandbox test harnesses to `tests/`
- [ ] Port eval scripts to `benchmarks/`
- [ ] Move all `.md` docs to `docs/`
- [ ] Write user-facing `README.md` with quickstart

---

> **This document was generated by analyzing 65 commits, 8 documentation files, 5 core engine files (120+ KB of Python), 22 sandbox experiment scripts, and 13 auxiliary directories from the experimental GraphDB codebase.**
