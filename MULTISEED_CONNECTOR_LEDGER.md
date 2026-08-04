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

### Run 011 - pairwise root-ranking audit

**Script:** `sandbox/_analyze_connector_results.py`

This audit reports the first lexicographic dimension that separates the saved
winner and runner-up. It is not a full semantic ablation because the artifacts
retain only the top rows rather than every candidate.

Across the original seven training and four holdout cases:

| Pairwise deciding dimension | Cases |
|---|---:|
| Query cosine | 5/11 |
| Seed coverage | 2/11 |
| Directional support | 1/11 |
| Trivial single candidate | 3/11 |

`pipeline_builders_common_orchestrator` is direction-decided, not
semantic-decided: `_run_pipeline_impl` has direct outgoing support 4 while the
runner-up has 0. Among the five actual cosine-decided pairs, three margins are
greater than 0.04; recording cache remains the fragile case at roughly 0.011.

Decision: add a full no-semantics ranking to the runner before making a causal
claim that query cosine changes the final root.

### Run 012 - exploratory semantic-root stress cases

**Fixture:** `sandbox/multiseed_adversarial_cases.json`

**Artifacts:**

- `sandbox/out/query_connector_adversarial_run1.json`
- `sandbox/out/query_connector_adversarial_run2.json`

The first run contained a mis-specified telephony case: only
`handle_inbound_telephony` calls `_validate_inbound_request`. The corrected run
uses `_create_inbound_workflow_run`, which is called by both legacy and run-bound
inbound handlers.

The corrected exploratory results are 4/4 expected roots:

| Case | Pairwise factor | Actual runner-up |
|---|---|---|
| shared secret wrapper | coverage 3 vs 1 | `get` |
| low-level masking primitive | coverage 3 vs 1 | `_secret_fields_for_node_type` |
| MPS unreachable result | cosine 0.4196 vs 0.1081 | `get` |
| inbound workflow-run creation | cosine 0.5021 vs 0.3714 | `start_inbound_stream` |

These are in-sample exploratory cases designed after inspecting Dograh; they
are not independent validation. They show two useful equal-coverage semantic
discriminations and two coverage controls. No observed case lets semantics hurt.

Most importantly, these runs do not test union-aware path selection: all cases
use one-hop paths, every combination count is 1/1, no selected path changes, and
shared-edge savings are zero.

Decision: retain the cases as semantic-root stress evidence, but do not count
them as proof of the fixed-root union optimizer. The next experiment must use
real two- or three-hop alternatives for multiple seeds and produce more than one
Cartesian path combination.

### Run 013 - conservative static-call overlay resolution

**Code:** `sandbox/exp_query_conditioned_connectors.py`

**Tests:** `tests/test_current_source_calls.py`

The ephemeral overlay now distinguishes exact static evidence from name-only
evidence. It resolves imported functions, imported classes, module aliases,
same-file class-qualified calls, and `self`/`cls` methods when the owning class
is statically known. Receiver-dependent attribute calls remain unresolved and
are reported rather than guessed.

Observed current-source overlay:

| Measurement | Count |
|---|---:|
| Raw canonical `CALLS` | 6,769 |
| Retained raw current name matches | 4,254 |
| Overlay `CALLS` | 5,087 |
| Direct same-file additions | 299 |
| Imported-function exact additions | 376 |
| Same-file method exact additions | 136 |
| Module-attribute exact additions | 14 |
| Imported-class-method exact additions | 8 |
| Ambiguous raw name edges retained and tagged | 1,067 |
| Functions with unresolved attribute names | 2,435 |
| Unresolved attribute-name occurrences by function | 8,444 |

This removes 120 heuristic same-file additions from the prior 5,207-edge
overlay while adding inspectable exact provenance. The most common unresolved
names are `get`, `execute`, `async_session`, `info`, and `error`, which is
consistent with dynamic receivers and library APIs rather than safe static
targets.

Regression result: 7/7 training cases and 4/4 holdout cases still select the
expected root at rank 1 and retain a complete bounded route. The focused source
tests cover module aliases, imported classes, same-file class qualification,
`self` methods, and unresolved receiver-dependent dispatch.

Limitation retained deliberately: raw ambiguous edges are still present for
regression safety. This overlay is evidence for experiments, not a sound Python
call graph.

### Run 014 - real shared-prefix case mining

**Script:** `sandbox/mine_shared_prefix_union_cases.py`

**Artifact:** `sandbox/out/shared_prefix_union_candidates.json`

The first topology pass admitted cross-file `raw_current_unique_name` edges and
produced a false SDK `add` connector from unrelated attribute calls. That
failure disproves the assumption that a globally unique function name is
enough to validate an attribute edge.

The final miner excludes both ambiguous and cross-file unique-name edges. Every
retained path edge is statically resolved or has same-file name evidence. It
enumerates real directed Dograh paths up to three hops, requires at least two
paths per seed, evaluates seed triples exactly within the declared bounds, and
compares deterministic independent paths with minimum unique-edge unions.

Final mining counts:

| Direction | Roots with enough multi-path seeds | Seed triples evaluated | Qualifying triples | Saved |
|---|---:|---:|---:|---:|
| Shared callee | 35 | 1,333 | 153 | 25 |
| Common caller | 41 | 868 | 115 | 25 |

No candidate hit the 20,000-combination cap. Manual source review selected two
shared-callee cases: text-chat session normalization and telephony configuration
normalization. A same-file ARI common-caller case was structurally clean, but
its shared intermediate is a better root under the current common-caller ranker,
so it was not frozen as the primary query case.

### Run 015 - query-conditioned shared-prefix union validation

**Fixture:** `sandbox/multiseed_shared_prefix_cases.json`

**Artifact:** `sandbox/out/query_connector_shared_prefix_union.json`

Both source-reviewed cases finally exercise the missing behavior:

| Case | Expected root rank | Combinations | Independent union | Selected union | Changed paths |
|---|---:|---:|---:|---:|---:|
| Text-chat normalization | 1 | 27/27 | 6 edges, cost 5.247296 | 4 edges, cost 3.548949 | 2/3 |
| Telephony config normalization | 1 | 8/8 | 7 edges, cost 6.000251 | 5 edges, cost 4.369353 | 2/3 |

For text chat, append and create routes switch from their separate service
branches to the shared `_execute_pending_turn_response -> _build_response ->
normalize_text_chat_session_data` suffix. For telephony, public and outbound
routes switch from the default-config loader to the shared
`get_telephony_provider_by_id -> load_telephony_config_by_id ->
_normalize_with_phone_numbers` suffix.

This is the first real Dograh evidence that fixed-root union optimization can
select a different multi-step connection than independent shortest weighted
paths. The claim remains bounded: three seeds, at most three hops, exact path
and Cartesian enumeration within the saved case limits.

### Run 016 - sharing versus distinct-path objective probe

The two counterqueries use identical roots and seeds but explicitly ask to
preserve lifecycle-specific or loader-specific branches. Under the default
objective they still select the same shared unions. Query embeddings continue
to select the intended root, but they do not reliably encode whether path
sharing itself is desirable.

The runner now reports the objective family:

`J_alpha = unique_union_weighted_cost + alpha * summed_path_cost`

`alpha=0` is the existing pure-union objective. Increasing `alpha` penalizes
longer per-seed detours taken only to gain shared edges.

Observed switches:

| Topology | Shared union at alpha 0 | First probed independent selection |
|---|---:|---:|
| Text-chat normalization | yes | alpha 1 |
| Telephony normalization | yes | alpha 50 |

The telephony threshold is high because all-by-ID sharing adds very little
summed path cost relative to the independent default/inbound mix. This is not a
single universally correct alpha. A production query API would need an explicit
sharing preference, calibrated task mode, or a separately validated intent
classifier. Natural-language node cosine alone is insufficient evidence for
that control.

Current conclusion: the system can find multi-seed connections and optimize a
real bounded shared subgraph under a declared mathematical objective. It cannot
yet claim the selected union is universally optimal for an arbitrary natural-
language problem statement. Dynamic dispatch, ambiguous retained raw edges,
root-versus-subgraph joint optimization, and query-to-objective calibration
remain open.
