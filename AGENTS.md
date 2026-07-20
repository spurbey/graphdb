# /annotate-commit

Annotates the semantic memory of functions changed in a commit.

**Usage:** `/annotate-commit [sha]`
- `sha`: commit hash (full or short, defaults to HEAD)

**What it does:**
For each function meaningfully changed in the commit, writes a memory note explaining what changed and why, and creates a typed edge from the commit to the function state (REDESIGNED / FIXED / EXTENDED / REFACTORED).

The sub-agent reads the diff, checks prior memories, calls LLM to classify the change, and persists the result to HelixDB. Unknown co-occurring function pairs are logged as candidates for future CO_CHANGE analysis.

**When to use:**
After completing a commit that represents a non-trivial change. Skip for formatting-only, dependency-update, or CI config commits.

**Example output:**
```
Annotated 3 functions:
  ingest_hook_payload (EXTENDED) — Added default_agent parameter
  normalize_event_payload (REFACTORED) — Extracted normalization logic
  session_exists (FIXED) — Fixed KeyError on missing session_id
Skipped 1 (trivial):
  update_timestamp (whitespace-only change)
```

# /query-history

Search a function's semantic memory history by topic.

**Usage:** `/query-history [func_id] [topic]`
- `func_id`: full function node ID (e.g. `src/ingest.py::ingest_hook_payload`)
- `topic`: natural language query (e.g. "session handling and agent normalization")

**What it does:**
Vector search on `memory_vec` across ALL FunctionState nodes for this function (not just active). Returns memories ranked by relevance to topic, with commit SHAs and edge types. Shows the timeline of what changed and why.

**When to use:**
Before modifying a function, to understand its design history. After checking `search_code_semantics` for cold discovery, use this for pre-edit research.
