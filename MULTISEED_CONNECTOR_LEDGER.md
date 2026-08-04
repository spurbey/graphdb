# Multi-seed Query Connector Research Ledger

Started: 2026-08-04

## Problem

Given a natural-language problem and three or four known function seeds, find a
small directed path or connected subgraph that explains the meaningful
relationship between the seeds. The result must prefer query-relevant product
code over generic utilities, respect caller/callee direction, and stay within a
small node and token budget.

This is an experiment, not a settled product design. A formula is retained only
when its intermediate output explains what it adds and its ablation shows that
it does not silently damage another subsystem.

## Non-negotiable boundaries

- HelixDB is the live graph source.
- `graph_payload.json` is not serving truth.
- igraph may be used as an ephemeral offline algorithm laboratory.
- Query semantics come from stored node embeddings; GraphSAGE is structural.
- Global PPR, Infomap, betweenness, and CO_CHANGE are features or controls, not
  assumed final rankers.
- Runtime traversal must be directed, bounded, cycle-controlled, and observable.
- No production/MCP integration until the query-conditioned evaluation succeeds.
- Existing unrelated worktree changes remain untouched.

## Commit map

Use these commits as the stable navigation sequence for this experiment:

| Commit | Scope |
|---|---|
| `a1b226e` | structural multi-seed baseline and controls |
| `9bf2f7f` | frozen training and holdout query cases |
| `8394b51` | query-conditioned runner, current-source overlay, and union optimizer |
| `03731b7` | Helix topology, feature, Pareto, and mode-ranking audits |
| `4703064` | bounded traversal and path-alternative evidence |
| `10c03fd` | same-file and import-aware overlay iterations |
| `d11f304` | first exact fixed-root union probes |
| `e4affff` | matched raw-versus-overlay comparison runs |
| `37b19e5` | rejected nested-call scoping regression evidence |
| `afe2d78` | definitive overlay matrix and forced truncation probe |
| `ea675cc` | full research ledger through Run 010 |

## Current live-data concerns to verify

1. `CALLS` direction is caller to callee.
2. Current `CALLS` edges appear to have no useful properties.
3. Call resolution is largely name-based, so incorrect or ambiguous edges may
   exist.
4. FunctionIdentity data may include duplicate or stale identities.
5. Node code embeddings should be available either inline or through TurboVec.
6. GraphSAGE role vectors may not be available for Dograh.
7. Unrestricted depth-two and depth-three traversals may expand too quickly for
   an agent-facing query.

## Frozen expectations before query-conditioned work

### Structural baseline

- Multi-seed distance center should remain a strong hidden-connector baseline.
- PPR should be vulnerable to generic high-degree utilities.
- Betweenness should not identify local query-relevant connectors reliably.
- A hub penalty should help some Steiner paths but can also reject real
  orchestrators, so its raw contribution must be shown.

### Query semantics

- Semantic-only node ranking may find functions relevant to the problem but is
  not required to connect all seeds.
- Adding semantic relevance should help choose between multiple structurally
  valid paths.
- Semantic relevance must not overpower seed coverage, direction, or compactness.

### Directed optimization

- Caller/orchestrator cases and shared-dependency cases require different graph
  orientations.
- With at most four seeds, a seed-coverage bitmask search should be feasible on
  a bounded candidate graph.
- Exactness is claimed only inside that bounded graph and declared cost model,
  never for the entire repository.

## Evaluation discipline

Every run should persist or print:

- query, seeds, expected result, and acceptable alternatives;
- candidate graph size by direction and depth;
- truncation, cycle, duplicate, and unresolved-edge counts;
- every feature before normalization;
- every normalized feature and weighted contribution;
- selected paths, combined subgraph, seed coverage, and total cost;
- rejected near-misses and why they lost;
- comparison with structural and semantic controls;
- wall time and output node budget.

For each added subsystem, run an ablation with that subsystem removed. If a
component hurts, first locate the exact cases and feature contributions, adjust
only the faulty stage, and rerun earlier checkpoints.

## Experiment stages

| Stage | Subsystem | Expected evidence | Status |
|---|---|---|---|
| 0 | Live Helix audit | trustworthy node, edge, vector, direction facts | complete |
| 1 | Existing structural baseline | reproduce manual and holdout results | complete |
| 2 | Directed bounded candidate generation | complete expected paths without explosion | complete for 1-2 hops |
| 3 | Node semantic scoring | relevant candidates score above generic ones | complete on frozen suite |
| 4 | Stable structural/historical features | interpretable dimensions, no final formula | audited; CO_CHANGE and hub penalty not decisive |
| 5 | Query-conditioned scalar cost | contribution ledger per edge and node | additive formula rejected; mode-conditioned selector retained |
| 6 | Multi-seed optimizer | connected directed result with declared optimum | complete for fixed selected root and bounded paths |
| 7 | Ablations and regression repair | evidence for retained/removed components | complete on 7 training and 4 holdout cases |
| 8 | Helix/Rust feasibility | measured runtime boundary and storage proposal | pending |

## Run log

### Run 000 - pre-experiment checkpoint

Status: recorded before new runtime inspection.

Known evidence from the previous structural experiment:

- Live Helix snapshot had 4,880 FunctionIdentity rows, 4,704 canonical IDs,
  6,826 CALLS edges, and 6,769 canonical unique CALLS edges.
- Distance center ranked the expected connector first in two hand-checked Dograh
  cases.
- On 100 synthetic structural holdouts, distance center achieved 55 rank-one
  and 89 top-five hits; PPR and betweenness were substantially worse.
- These numbers validate only structural connector discovery. They do not show
  query-conditioned optimal path selection.

Next action: audit the current live database and rerun the baseline without
changing its formula.

### Run 001 - baseline reproduction

Command:

```powershell
python sandbox\exp_multiseed_connectors.py --holdouts 100
```

Observed:

- The live counts exactly reproduced Run 000: 4,880 FunctionIdentity rows,
  4,704 canonical nodes, 6,826 raw CALLS edges, and 6,769 canonical CALLS.
- The two manual expected connectors remained rank 1.
- The 100-case structural holdout summary exactly reproduced Run 000.
- `_run_pipeline` has degree 93 and is nevertheless the correct orchestrator.

Decision:

- Baseline is stable enough to use as a control.
- Do not use a hard high-degree rejection. Real orchestration functions can be
  hubs; any hub feature must be a weak, inspectable penalty.

### Run 002 - live storage audit

Observed:

- All 4,880 live FunctionIdentity rows have `code_vector_id` and
  `code_vector_source_node_id`.
- Live Helix rows have zero inline 384-dimensional vectors.
- TurboVec code index contains 4,821 vectors.
- TurboVec GraphSAGE index contains zero vectors.
- The legacy exact-vector cache contains those 4,821 code vectors and covers all
  4,704 canonical FunctionIdentity IDs after duplicate collapse.
- Live CALLS edges expose only Helix identifiers/context, not call-site count,
  resolution confidence, or routing features.

Decision:

- Use exact cached code vectors only for offline feature introspection. Product
  candidate retrieval remains TurboVec-backed.
- Treat GraphSAGE as unavailable in this Dograh phase; do not synthesize it.
- Do not claim edge-quality weighting until call resolution confidence and
  call-site evidence actually exist.

### Run 003 - first query-conditioned feature ablation

Case: `pipeline_entrypoints_shared_core`

Expected before run:

- `_run_pipeline_impl` should connect all three entrypoints within two hops.
- Query semantics should distinguish the execution core from shared incidental
  utilities.

Observed:

- 199 product candidates were reached by at least one seed; 14 had full seed
  coverage.
- `_run_pipeline_impl` distances were `[2, 2, 1]`; raw query cosine was
  `0.31512237`.
- It ranked 1 by semantic score inside the full-coverage pool.
- It ranked 8 by distance lexicographic ordering, 9 by the first structural
  formula, and 7 after adding semantic, hub, and CO_CHANGE terms.
- Direct one-hop shared nodes such as `register_active_call`,
  `unregister_active_call`, and workflow/configuration helpers outranked the
  execution core because the additive distance reward was larger than the
  semantic contribution.

Diagnosis:

- Seed coverage belongs as a feasibility constraint, not a giant additive
  number.
- Among equally feasible candidates, compactness cannot automatically dominate
  query relevance. A direct incidental utility is not necessarily the answer.
- The current formula is rejected, but will first be run unchanged across all
  frozen cases to determine whether the failure is systematic.

### Run 004 - frozen-case formula sweep and mode conditioning

Artifacts:

- `sandbox/out/query_connector_stage_4_mode_training.json`
- `sandbox/out/query_connector_stage_4_mode_holdout.json`

Observed:

- The additive formula failed systematically when a semantically correct
  two-hop connector competed with incidental one-hop utilities.
- A lexicographic selector ranked the expected connector first on all seven
  inspected training cases and all three reachable untouched holdouts.
- The fourth holdout had no usable raw edges and was classified as an ingestion
  failure rather than a ranking failure.
- Caller/orchestrator cases needed direct outgoing support before query cosine.
  Shared-callee and mixed cases worked with query cosine before direction
  support.

Decision:

- Retain mode-conditioned root selection:

```text
common caller:
  coverage -> direct outgoing seed support -> query cosine -> compactness

shared callee / mixed:
  coverage -> query cosine -> directional support -> compactness
```

- Do not fit a single scalar across root feasibility, semantics, and path
  compactness. These stages answer different questions.

### Run 005 - root-conditioned bounded path enumeration

Artifacts:

- `sandbox/out/query_connector_stage_5_routes_pipeline.json`
- `sandbox/out/query_connector_stage_5_bounded_training.json`
- `sandbox/out/query_connector_stage_5_bounded_holdout.json`

Observed:

- The selected root was fixed before path enumeration.
- Each seed used directed simple paths within the case's declared one- or
  two-hop budget.
- Test intermediates were rejected and product-path counts, alternatives,
  costs, union nodes, union edges, and truncation were emitted.
- No inspected enumeration truncated. Per-seed alternatives were between one
  and three.
- The pipeline entrypoint case produced the intended six-node, five-edge union:

```text
telephony -> _run_pipeline_telephony_impl -> _run_pipeline_impl
SmallWebRTC -> _run_pipeline_smallwebrtc_impl -> _run_pipeline_impl
_run_pipeline -> _run_pipeline_impl
```

Decision:

- Bounded routing is operational, but a path is not accepted merely because it
  is shorter. Semantic root selection remains upstream.

### Run 006 - stale topology diagnosis and first current-source overlay

Artifacts:

- `sandbox/out/query_connector_stage_6_overlay_pipeline_v2.json`
- `sandbox/out/query_connector_stage_6_overlay_training.json`

Observed:

- Raw Helix preferred historical edges such as
  `run_pipeline_smallwebrtc -> _run_pipeline`, although current source calls
  `_run_pipeline_smallwebrtc_impl`.
- The first ephemeral overlay kept Helix identities, removed edges whose source
  or target function was unavailable, validated raw callee names against
  current source, and added exact same-file calls.
- That overlay reduced 6,769 raw canonical CALLS edges to 4,831 and added 577
  current same-file edges.
- Six training cases succeeded. The pipeline-builder case had zero candidates
  because its old expected connector `_run_pipeline` no longer calls the four
  builder functions; current source places those calls in `_run_pipeline_impl`.

Decision:

- Correct the stale ground truth to `_run_pipeline_impl`.
- Do not change the ranker to compensate for missing current cross-file calls.

### Run 007 - import-aware current-source topology repair

Artifacts:

- `sandbox/out/query_connector_stage_7_overlay_training.json`
- `sandbox/out/query_connector_stage_7_overlay_holdout.json`

Observed:

- Direct calls to functions imported with `from module import function` were
  resolved to exact current module/function identities.
- The final overlay contains 5,207 edges:

```text
raw current name matches:      4,254
added current same-file:         577
added current imported:          376
unresolved imported targets:      18
```

- All seven training cases and all four untouched holdouts selected the
  expected root at rank 1.
- The earlier user-configuration holdout ingestion failure was repaired by
  three added current same-file edges.
- The pipeline-builder case was repaired by four added imported edges from
  `_run_pipeline_impl` to the builder functions.

Boundary:

- The overlay is still experimental. It resolves direct function-name calls
  from explicit imports, not attribute calls, runtime dispatch, star imports,
  or ambiguous cross-file methods.
- Calls inside nested functions remain attributed to the indexed outer
  FunctionIdentity. This preserves the current graph identity model and is
  required for `create_recording_audio_fetcher`, whose returned `fetch` closure
  calls `_download_and_convert`.

### Run 008 - exhaustive union optimization under the selected root

Artifacts:

- `sandbox/out/query_connector_stage_11_union_overlay_training.json`
- `sandbox/out/query_connector_stage_11_union_overlay_holdout.json`
- `sandbox/out/query_connector_stage_11_union_cap_probe.json`

Method:

1. Select the root with the retained mode-conditioned rule.
2. Enumerate each seed's product-code simple paths within the declared hop cap.
3. Evaluate the Cartesian product of those path alternatives.
4. Count each union edge once and minimize union weighted edge cost.
5. Break ties with union edge count, union node count, total hops, connector
   semantics, connector hub sum, and deterministic path IDs.

Observed:

- All seven training and four holdout cases produced complete bounded routes.
- Every normal run was exact within the declared fixed-root path space. The
  largest real combination sets were 4/4 for recording cache and 3/3 for the QA
  holdout; the other cases had 1/1 combinations.
- No path selection changed relative to choosing each seed's cheapest path
  independently. There were no shared-edge savings in this suite.
- A forced `--max-union-combinations 1` probe on the 4-combination recording
  case correctly emitted `exact_within_declared_bounds=false` and
  `combination_truncated=true`.

Decision:

- The optimizer is retained as exactness and observability machinery, not yet
  as evidence that union-aware selection improves quality.
- Add adversarial cases with longer alternative paths that share an expensive
  prefix before claiming benefit over independent shortest paths.

### Run 009 - raw Helix versus current-source overlay

Raw artifacts:

- `sandbox/out/query_connector_stage_9_union_raw_training.json`
- `sandbox/out/query_connector_stage_9_union_raw_holdout.json`

Overlay comparison artifacts:

- `sandbox/out/query_connector_stage_11_union_overlay_training.json`
- `sandbox/out/query_connector_stage_11_union_overlay_holdout.json`

Observed:

| View | Expected roots | Complete bounded routes | Important failure |
|---|---:|---:|---|
| Raw training | 6/7 | 5/7 | selected stale `_run_pipeline`; missing current service-factory and masking paths |
| Overlay training | 7/7 | 7/7 | none in frozen suite |
| Raw holdout | 3/4 | 3/4 | user-configuration case had zero coverage |
| Overlay holdout | 4/4 | 4/4 | none in frozen suite |

The raw graph also routed current SmallWebRTC through stale `_run_pipeline` in
one alternative, whereas the overlay used `_run_pipeline_smallwebrtc_impl`.

Decision:

- Current-source edge confidence is a prerequisite for meaningful optimization.
  Better path math cannot repair stale or missing topology.
- Do not mutate Helix during this experiment. The overlay remains ephemeral and
  every selected union edge carries its origin status.

### Run 010 - runtime boundary and parser iteration

Observed from the final full overlay runs:

- Current-source overlay construction took about 12.2 seconds while three
  concurrent experiment processes were active; an isolated probe took about
  7.0-7.7 seconds after indexing functions and imports once per file.
- Loading the exact vector cache took roughly one second.
- Query embedding model startup dominated wall time, ranging from about 21 to
  63 seconds depending on concurrent model loading.
- Scoring, root selection, bounded traversal, and exhaustive union evaluation
  took roughly 0.2-0.5 seconds for four to seven cases.

Decision:

- Precompute or incrementally maintain the current-source overlay before any
  runtime integration.
- Keep the query embedding model warm or use the existing retrieval runtime;
  repeated process startup is not a meaningful query-latency architecture.
- The graph algorithms themselves are not the present latency bottleneck.

## Current conclusion

Within this frozen Dograh suite, the problem is feasible with the components we
already have, but only as a coordinated pipeline:

```text
current-source edge confidence
  -> directed bounded coverage
  -> mode-conditioned semantic root selection
  -> exhaustive fixed-root path-union optimization
  -> observable evidence and exactness flags
```

This solves the tested form of "given several functions, find their meaningful
query-conditioned connection and a compact bounded explanation." It does not
yet solve arbitrary repository-wide Steiner search, dynamic dispatch, or
semantic interpretation without a query embedding. The current evidence is
strong enough to continue experimentation, not strong enough for product/MCP
integration.
