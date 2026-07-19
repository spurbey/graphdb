# FunctionState Semantic Memory — Build Spec

**Created:** 2026-07-19  
**Status:** Pre-implementation. Update after each phase with actual results.  
**Rule:** Every phase has an expected output defined before building. Every eval checks actual vs expected. If actual diverges, stop and diagnose before proceeding.

---

## What We Are Building

A semantic memory layer on top of the existing code graph. When a developer makes a meaningful code change, a sub-agent (triggered by `/annotate-commit`) reads the diff and writes:
- A `memory` note on the `FunctionState` node: what changed and why
- A typed edge from the `Commit` to the `FunctionState`: REDESIGNED / FIXED / EXTENDED / REFACTORED

This enables agents to query: "what do I need to know about this function's history before modifying it?"

---

## Prerequisites (must be true before any phase starts)

1. HelixDB running on localhost:6969
2. AMO repo ingested via `scalable_ingest.py` (FunctionIdentity, FunctionState, Commit nodes exist)
3. `sandbox/amo_nodes.json` and `sandbox/amo_edges.json` exist (for igraph-based tools)
4. `.env` has valid OpenRouter API key

**Verification check:**
```python
from pipeline_api import initialize, status
initialize()
s = status()
assert s["pipeline"] == "igraph"
assert s["nodes"] > 1000
print("Prerequisites OK")
```

---

## Phase 1: HelixDB Schema Extension

### What we're changing

Add two new properties to `FunctionState` nodes and two new edge types.

**New properties on FunctionState:**
- `memory` (string | null) — LLM-written note. What changed and why. null until annotated.
- `memory_vec` (float32[2048] | null) — embedding of `memory`. null until annotated.

**New edge types from Commit → FunctionState (in addition to existing GENERATED):**
- `INTRODUCED` — function created for the first time in this commit
- `REDESIGNED` — architectural or logic intent changed
- `FIXED` — bug correction
- `EXTENDED` — new capability or parameter added
- `REFACTORED` — same behavior, different internal structure

### Files to modify

- `scalable_ingest.py` — add `memory: null` and `memory_vec: null` to FunctionState node creation; add `INTRODUCED` edge when function is new; add `IndexSpec.node_vector("FunctionState", "memory_vec")` for vector search on memories

### Expected output after Phase 1

Running a fresh ingest on any Python repo, every `FunctionState` node should have:
```json
{
  "node_id": "state_...",
  "code": "...",
  "ai_summary": "...",
  "ai_summary_vec": [...],
  "memory": null,
  "memory_vec": null,
  "status": "active"
}
```

New functions (first appearance) should have a `INTRODUCED` edge from their creating Commit.

### Eval 1: Schema verification

```python
# After ingest:
from helixdb import Client, g, read_batch
c = Client("http://127.0.0.1:6969")

# Check 1: FunctionState has memory field (null for new ingest)
result = c.query()...  # fetch 5 active FunctionState nodes
for state in result:
    assert "memory" in state
    assert state["memory"] is None
    assert "memory_vec" in state
    assert state["memory_vec"] is None

# Check 2: At least one INTRODUCED edge exists (for functions added in first commit)
result = c.query()...  # fetch edges of type INTRODUCED
assert len(result) > 0

print("Phase 1 eval: PASS")
```

**If eval fails:** Check `scalable_ingest.py` node creation dict, check HelixDB index creation order (memory_vec index must be created before any nodes are inserted with memory_vec values).

---

## Phase 2: Sub-Agent Tools

### What we're building

Four Python functions that the sub-agent will call as tools:

**Tool 1: `list_functions_changed_in_commit(sha) -> list[dict]`**
- Uses GitPython to read the commit
- Parses changed `.py` files with tree-sitter
- Returns: `[{func_id, name, file, is_new, diff_text}]`
- `is_new` = True if function didn't exist in parent commit

**Tool 2: `read_function_memory_history(func_id) -> list[dict]`**
- Queries HelixDB for all FunctionState nodes for this function
- Follows PREVIOUS_VERSION chain
- Returns: `[{commit_sha, memory, edge_type, timestamp}]` sorted newest-first
- Returns empty list if no memories exist yet

**Tool 3: `write_function_memory(func_id, commit_sha, edge_type, memory_text) -> bool`**
- Validates: `edge_type` must be one of REDESIGNED / FIXED / EXTENDED / REFACTORED
- Embeds `memory_text` via OpenRouter to get `memory_vec`
- Writes `memory` and `memory_vec` to the FunctionState node via `set_property`
- Creates the typed edge from Commit to FunctionState in HelixDB
- Returns True on success, False on failure

**Tool 4: `log_new_cooccurrence(func_id_a, func_id_b, commit_sha) -> None`**
- Appends to `sandbox/cooccurrence_candidates.jsonl`
- Format: `{"source": func_id_a, "target": func_id_b, "commit": commit_sha, "timestamp": ...}`
- Does NOT promote to CO_CHANGE. Just logs. Existing `cochange_analysis.py` gate handles promotion on next full run.

### Expected output after Phase 2

Each tool called in isolation returns correct output:

```python
# Tool 1: commit with 3 changed functions returns 3 entries
fns = list_functions_changed_in_commit("abc1234")
assert len(fns) >= 1
assert all("func_id" in f for f in fns)
assert all("diff_text" in f for f in fns)

# Tool 2: function with no prior memory returns empty list
history = read_function_memory_history("src/.../ingest.py::ingest_hook_payload")
assert isinstance(history, list)  # may be empty, that's fine

# Tool 3: write succeeds, node has memory afterward
ok = write_function_memory(
    func_id="src/.../ingest.py::ingest_hook_payload",
    commit_sha="abc1234",
    edge_type="EXTENDED",
    memory_text="Added default_agent parameter to support codex sessions."
)
assert ok == True
# Verify in HelixDB: memory field is now populated

# Tool 4: candidate logged without error
log_new_cooccurrence("func_a", "func_b", "abc1234")
# cooccurrence_candidates.jsonl has one new line
```

### Eval 2: Tool correctness

Run each tool against a known AMO commit (use commit `8e240ad` which touched 3 functions — already used in `probe_commit_inductive.py`).

```
Expected:
  list_functions_changed_in_commit("8e240ad") → 3 functions
  write_function_memory(one of them, "8e240ad", "EXTENDED", "...") → True
  read_function_memory_history(that func) → 1 entry with the written memory
```

**If Tool 1 fails:** Check GitPython repo path, tree-sitter parsing
**If Tool 3 fails:** Check HelixDB `set_property` pattern (known to work from earlier bugs), check vector index accepts writes after creation

---

## Phase 3: The System Prompt

### What we're building

A fixed, versioned system prompt stored as `skills/annotate_commit_prompt.md`. This is what the sub-agent gets as its system instruction.

### The prompt spec

The prompt must produce consistent outputs across different sub-agent sessions. Evaluated against: given the same diff, two sub-agent runs should produce the same edge type and a functionally equivalent memory text.

**Required sections:**
1. Role statement — what the agent is doing and why
2. Skip criteria — exact definition of "trivial" (whitespace, rename-only, docstring-only)
3. Edge type definitions — exact one-line definition of each type with a decision tree
4. Memory format requirements — what makes a good memory vs bad memory
5. Output format — JSON schema the agent must return
6. What to do with co-occurrences — log candidates, do not promote

**Decision tree for edge type (must be in prompt):**
```
Is this the first time this function exists? → INTRODUCED (not LLM, automatic)
Did the function signature change? (new params, changed return type) → lean EXTENDED or REDESIGNED
Did behavior change to fix wrong output? → FIXED
Did internal structure change but behavior is identical? → REFACTORED
Did the overall purpose/approach of the function change? → REDESIGNED
Did new code paths or capabilities get added? → EXTENDED
```

### Expected output after Phase 3

Given this input to the sub-agent:
```
Commit: "feat: add cross encoder rerank stage"
Function: memory_context_pack
Diff: [adds new parameter cross_encoder=False, adds conditional reranking block]
```

Expected output:
```json
{
  "func_id": "src/.../tools.py::memory_context_pack",
  "skip": false,
  "edge_type": "EXTENDED",
  "memory": "Added cross_encoder parameter enabling optional cross-encoder reranking of retrieved memory chunks. New code path activates when cross_encoder=True, calling the rerank stage before returning context."
}
```

**Eval 3: Prompt consistency**

Run the sub-agent on the same commit twice in separate sessions. Check:
- Same `edge_type` both times
- `memory` texts are semantically equivalent (not necessarily identical — both mention the same what + why)

**If inconsistent:** Tighten the decision tree in the prompt. The edge type should be deterministic. The memory text will vary in wording but must contain the same core facts.

---

## Phase 4: `annotate_commit()` in pipeline_api.py

### What we're building

A function that:
1. Calls `list_functions_changed_in_commit(sha)`
2. For each function, calls `read_function_memory_history(func_id)` to give prior context
3. Runs the sub-agent with the system prompt + diff + history as context
4. Parses JSON output from sub-agent
5. For non-skipped functions: calls `write_function_memory()`
6. For co-occurrences of unknown pairs: calls `log_new_cooccurrence()`
7. Returns: `{annotated: [func_ids], skipped: [func_ids], errors: [func_ids]}`

Debug mode (flag): also writes everything to `sandbox/out/annotate_commit_{sha}.json`

### Expected output after Phase 4

```python
result = annotate_commit("8e240ad", debug=True)
assert len(result["annotated"]) >= 1
assert len(result["errors"]) == 0

# Check the annotated functions have memory in HelixDB
for func_id in result["annotated"]:
    history = read_function_memory_history(func_id)
    assert len(history) >= 1
    assert history[0]["memory"] is not None
    assert history[0]["edge_type"] in ["REDESIGNED", "FIXED", "EXTENDED", "REFACTORED"]
```

**Eval 4: End-to-end on 3 AMO commits**

Pick 3 commits with different characteristics:
1. `8e240ad` — feature addition (expect EXTENDED)
2. A commit with "fix" in message — expect FIXED
3. A large refactor commit — expect REFACTORED for most functions

Run `annotate_commit()` on each. Check:
- Correct edge types for each commit type
- No hallucinated edge types (only the 4 allowed)
- memory text is factually grounded in the diff (not generic)
- Debug JSON written correctly

**If wrong edge types:** Revisit system prompt decision tree
**If hallucinated edge types:** Add explicit constraint to system prompt: "You MUST use exactly one of: REDESIGNED, FIXED, EXTENDED, REFACTORED. No other values."

---

## Phase 5: `/annotate-commit` Slash Command

### What we're building

A SKILL.md file that registers `/annotate-commit [sha]` as a slash command in the coding agent.

**Format:**
```markdown
# /annotate-commit

Annotates the semantic memory of functions changed in a commit.

Usage: /annotate-commit [sha]
  sha: commit hash (optional, defaults to HEAD)

What it does:
  For each function meaningfully changed in the commit, writes a memory
  note explaining what changed and why, and creates a typed edge from
  the commit to the function state.

When to use:
  After completing a commit that represents a non-trivial change.
  Skip for formatting-only, dependency-update, or CI config commits.
```

The slash command calls `annotate_commit(sha)` via the MCP server.

### Expected output after Phase 5

User types `/annotate-commit abc1234` in coding agent.
Agent calls `POST /call` with `{"name": "annotate_commit", "parameters": {"sha": "abc1234"}}`.
MCP server returns annotated functions list.
Coding agent displays: "Annotated 3 functions: ingest_hook_payload (EXTENDED), normalize_event_payload (REFACTORED), ..."

**Eval 5:** Run the slash command manually in the coding agent on a test commit. Verify the MCP server receives the call and returns correct output.

---

## Phase 6: `query_function_history` Tool

### What we're building

New MCP tool: `query_function_history(func_id, topic)`

- Vector search on `memory_vec` across ALL FunctionState nodes for this function (not just active)
- Returns memories ranked by relevance to `topic`
- Also returns: PREVIOUS_VERSION chain order, edge type per state

### Expected output

```python
results = query_function_history(
    "src/.../ingest.py::ingest_hook_payload",
    "session handling and agent normalization"
)
# Returns list of memories, most relevant first
# Each entry: {memory, edge_type, commit_sha, timestamp}
# If memory is null for a state: excluded from results
```

**Eval 6:** After annotating 3+ commits on the same function, query its history. Verify relevant memories are returned and the timeline is correct.

---

## Phase 7: Enhanced `explain_coupling` and `commit_review`

### `explain_coupling` enhancement

When A and B are a CO_CHANGE pair, also return: the most recent memory from a commit where both were annotated together.

```python
result = explain_coupling(func_a_id, func_b_id)
# Now also returns:
result["recent_co_annotation"] = {
    "commit": "abc1234",
    "memory_a": "...",
    "memory_b": "...",
    "edge_type_a": "EXTENDED",
    "edge_type_b": "REFACTORED"
}
```

### `commit_review` enhancement

For each changed function, show its most recent prior memory:

```python
results = commit_review(["func_id_1", "func_id_2"])
# Each result now also has:
results[0]["prior_memory"] = {
    "memory": "Last time: added cross encoder reranking",
    "edge_type": "EXTENDED",
    "commit": "8e240ad",
    "days_ago": 14
}
```

**Eval 7:** Run `commit_review` on a commit that touches functions with existing memories. Verify prior memories are surfaced correctly.

---

## Iteration Protocol

If any eval fails:

1. **Do not proceed to next phase.** Fix the failing phase first.
2. **Diagnose before fixing.** Read the actual output, compare to expected. Write one sentence stating the root cause.
3. **One change at a time.** Don't fix multiple things simultaneously — you won't know which fix worked.
4. **If the root cause is the system prompt:** Edit the prompt file, re-run Eval 3 first to confirm consistency, then re-run the failing eval.
5. **If the root cause is a schema issue:** Check HelixDB insert order (vector index must exist before nodes with that field are inserted — learned from earlier bugs).
6. **If the root cause is an LLM hallucination:** Add explicit constraints to the system prompt. Tighten the decision tree.

---

## What Success Looks Like (End State)

After all 7 phases:

1. Every AMO FunctionState node has `memory: null` (correct — memories only populated when `/annotate-commit` is run)
2. After running `/annotate-commit` on 5 AMO commits: 10-15 FunctionState nodes have non-null `memory` fields
3. `query_function_history("src/.../ingest.py::ingest_hook_payload", "normalization")` returns relevant memories
4. `explain_coupling(A, B)` for a known CO_CHANGE pair returns the semantic WHY from annotated commits
5. `commit_review` surfaces prior memories, allowing agent to detect potential conflicts

The system doesn't need 100% coverage (not every commit will be annotated). It needs to be useful when populated — 5-10 memories on a well-studied function should give an agent genuine insight before modifying it.

---

## Files That Will Be Created/Modified

| File | Phase | Action |
|------|-------|--------|
| `scalable_ingest.py` | 1 | Add memory/memory_vec to FunctionState; add INTRODUCED edge; add memory_vec vector index |
| `pipeline_api.py` | 2,4 | Add 4 sub-agent tools; add `annotate_commit()` |
| `skills/annotate_commit_prompt.md` | 3 | System prompt (versioned) |
| `tools/graph_tools.py` | 5,6,7 | Add `annotate_commit`, `query_function_history`; enhance `explain_coupling`, `commit_review` |
| `tools/graph_mcp_server.py` | 5,6,7 | Register new tools in manifest + TOOL_MAP |
| `SKILL.md` or `AGENTS.md` | 5 | Register slash command |
| `sandbox/cooccurrence_candidates.jsonl` | 2 | Created by `log_new_cooccurrence` |
| `FUNCTION_MEMORY_LOG.md` | ongoing | Eval results, iteration notes |

**Files that will NOT be modified:**
- `sandbox/cochange_analysis.py` — CO_CHANGE gate stays intact
- `sandbox/amo_cochange_pairs.json` — rebuilt by `amo_ingest.py` on full run
- Existing HelixDB tools (trace_blast_radius, temporal vulnerability trace, etc.)
