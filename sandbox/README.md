# Sandbox - Cold Discovery Pipeline Tests

Local igraph prototype for validating the cold discovery pipeline and
bidirectional connector engine against `agent-memory-orchestrator`.

## Files

| File | Purpose |
|------|---------|
| `amo_ingest.py` | Ingest the first 100 AMO commits and regenerate graph/co-change artifacts. |
| `cochange_analysis.py` | Aggregate actual function-code co-change, gate by occurrence/Jaccard, categorize survivors, and build co-change theme artifacts. |
| `igraph_sandbox.py` | Full graph demo: weights, CALLS-only Infomap, CALLS-only PPR, MMR, and validation. |
| `amo_dump_viz.py` | Convert nodes/edges to viz format for `tools/harness_graph_viz.html`. |
| `amo_query_test.py` | Run a single ambiguous query and print results. |
| `amo_nodes.json` | Generated function nodes. Ignored by git. |
| `amo_edges.json` | Generated CALLS, IMPORTS, and cleaned CO_CHANGE edges. Ignored by git. |
| `amo_cochange_pairs.json` | Full evidence for each Stage-2 co-change survivor. Ignored by git. |
| `amo_cochange_themes.json` | Shared-dependency and unexplained co-change theme artifacts. Ignored by git. |
| `amo_cochange_thresholds.json` | Threshold config and sanity histograms. Ignored by git. |
| `amo_embedding_cache.json` | Reusable raw OpenRouter embeddings keyed by model, node id, and summary hash. Ignored by git. |

## Current Co-Change Pass

The current generated AMO 100-commit sandbox artifacts contain:

| Edge type | Count |
|-----------|------:|
| CALLS | 4,239 |
| IMPORTS | 150,299 |
| CO_CHANGE | 437 |

CO_CHANGE category breakdown:

| Category | Count |
|----------|------:|
| shared_dependency | 335 |
| structural_redundant | 89 |
| temporal_burst | 13 |
| shared_commit_only | 0 |

`structural_redundant` edges are intentionally still written as `CO_CHANGE`
edges. The category is metadata, not an exclusion rule.

Every generated `CO_CHANGE` edge can also carry multidimensional theme evidence:

```json
{
  "theme_id": "cochange_theme_0007",
  "theme_counts": {"cochange_theme_0007": 2.0, "cochange_theme_0041": 1.0},
  "theme_proportions": {"cochange_theme_0007": 0.666667, "cochange_theme_0041": 0.333333}
}
```

`theme_id` is only the dominant theme for compatibility. The real query-time
signal is `theme_proportions`. Category still controls consumer policy, while
supporting commit membership controls which themes an edge can boost for.

## Run Order

```bash
cd graphdb

# 1. Re-ingest when the AMO source repo or co-change rules change.
python sandbox/amo_ingest.py

# 2. Run the graph demo and query validation.
python sandbox/igraph_sandbox.py

# 3. Test arbitrary query.
python sandbox/amo_query_test.py

# 4. Regenerate viz.
python sandbox/amo_dump_viz.py
# Open tools/harness_graph_viz.html -> Load JSON -> sandbox/amo_viz.json
```

`igraph_sandbox.py` keeps Infomap and PPR on the CALLS-only subgraph. The
query validation depends on non-zero embeddings in `amo_nodes.json`; co-change
counts and categories do not.

`cochange_analysis.cochange_consumer_policy()` defines the category handling
for general retrieval, why-coupled, risk, and pre-edit/history modes.

If `amo_nodes.json` has zero raw embeddings but `graphsage_minimal/data` still
contains the prior prepared feature matrix, `igraph_sandbox.py` and
`amo_query_test.py` reuse that transformed GraphSAGE feature space for vector
seeding instead of re-embedding all active functions.

Current fallback validation with reused GraphSAGE features: `2/3` old
ground-truth queries pass. The first query has `ingest_hook_payload` as vector
seed rank 3, but the downstream PPR/MMR selection does not keep it in the
final top 10.
