# Sandbox — Cold Discovery Pipeline Tests

Local igraph prototype for validating the Cold Discovery Pipeline and
Bidirectional Connector Engine against `agent-memory-orchestrator`.

## Files

| File | Purpose |
|------|---------|
| `amo_ingest.py` | Ingest first 100 commits of AMO repo → `amo_nodes.json` + `amo_edges.json` |
| `igraph_sandbox.py` | Full pipeline: weights → Infomap → PPR → MMR → validation |
| `amo_dump_viz.py` | Convert nodes/edges to viz format for `tools/harness_graph_viz.html` |
| `amo_query_test.py` | Run a single ambiguous query and print results |
| `amo_nodes.json` | 2120 function nodes (1380 active with embeddings) |
| `amo_edges.json` | 494k edges (CALLS + CO_CHANGE + IMPORTS) |
| `amo_viz.json` | Trimmed edge set for D3 visualizer |

## Run order

```bash
cd graphdb

# 1. Re-ingest (only needed if AMO repo changes)
python sandbox/amo_ingest.py

# 2. Run full validation (3/3 ground-truth queries)
python sandbox/igraph_sandbox.py

# 3. Test arbitrary query
python sandbox/amo_query_test.py

# 4. Regenerate viz
python sandbox/amo_dump_viz.py
# Open tools/harness_graph_viz.html -> Load JSON -> sandbox/amo_viz.json
```

## Validation results

| Query | Target | Rank |
|-------|--------|------|
| memory ingestion pipeline hook processing | `ingest_hook_payload` | 1 |
| retrieve context from memory for agent | `memory_context_pack` | 1 |
| store and save session memory snapshot | `export_snapshot` | 7 |
