# GraphDB Productization Discussion Plan

Status: discussion draft, not an implementation contract

Date: 2026-08-06

Scope: the local GraphDB core, its Docker/runtime shape, indexing, storage,
algorithms, MCP tools, agent workflows, evaluation, and the existing optional
VS Code graph lens. Cloud hosting is intentionally out of scope.

This document treats the current repository as experimental evidence. It does
not assume that the current file layout, APIs, schemas, formulas, or artifacts
are the final product.

## 1. How to read this plan

Each important statement has one of three statuses:

- **Settled by current evidence:** repeated experiments or direct runtime facts
  are strong enough to constrain the product.
- **Recommended default:** the best current engineering choice, but still open
  to a measured challenge.
- **Open decision:** the answer changes architecture or product scope and needs
  explicit agreement or another experiment.

The product should continue to be developed with the same discipline used in
the latest multi-seed experiments:

1. State what a subsystem is expected to add.
2. Record its intermediate output and contribution.
3. Compare it with a simpler control.
4. Keep it only if it improves a named product workflow.
5. When it hurts, return to the first faulty stage instead of fitting another
   global formula around the failure.

## 2. Product thesis

GraphDB should be an agent-native code context runtime.

Its primary promise is:

> Before an agent changes code, GraphDB finds the code that matters, explains
> how it connects, surfaces the history and coupling that ordinary repository
> search cannot show, and returns a compact evidence pack the agent can act on.

Code review, test impact, CI assistance, architecture visualization, and editor
navigation are downstream uses of that context runtime. They are not the core
identity of the product.

### 2.1 What normal repository tools already do

Agents already have strong primitives:

- lexical search with `rg`;
- filename and symbol search;
- reading source and tests;
- language-server references;
- `git log`, `git blame`, and diffs;
- direct caller/callee inspection when the code is simple.

GraphDB should not merely make those operations graphical. It must add evidence
that is expensive, easy to miss, or impossible to obtain from one local read:

- semantic entry points when names do not match the task;
- directed multi-hop relationships among several candidate functions;
- historical co-change and design memory;
- current-source-aware call topology with explicit uncertainty;
- blast radius and architectural chokepoints;
- subsystem/community context;
- structural analogues that implement the same architectural role;
- compact, query-conditioned context under a token budget.

### 2.2 Product non-goals for V1

- Do not build an autonomous code-editing agent inside GraphDB.
- Do not expose every algorithm as a public MCP tool.
- Do not require an online LLM inside the GraphDB query runtime.
- Do not make a visual graph the primary product.
- Do not claim complete dynamic-language call resolution.
- Do not claim exact repository-wide Steiner optimization.
- Do not claim minimum test selection until test-to-code relationships are
  measured and trustworthy.
- Do not build a generic graph database abstraction before the GraphDB product
  contract is stable.
- Do not refactor the experimental repository merely to make it look clean.

### 2.3 Position relative to Graphify-style products

Graphify demonstrates that a static code graph can be useful when it is easy to
build, inspect, query, and explain. GraphDB should keep that clarity but should
not compete as another product whose main result is `graph.json`.

GraphDB's differentiating layer should be:

- semantic entry points when names do not match the task;
- versioned function states and semantic change memory;
- co-change evidence;
- current-source confidence and ambiguity on call edges;
- query-conditioned multi-seed connections;
- structural analogues;
- generation-aware serving and compact agent context.

The `map_codebase` workflow can borrow the useful idea of understandable
communities and explained relationships. The product wedge remains: not only
"what structure exists?", but "what matters for this task, why is it connected,
what changed historically, and what is risky to miss?"

## 3. What the experiments have actually established

### 3.1 Semantic retrieval

**Settled by current evidence:** semantic vector search is the correct first
stage when the agent does not already know the symbol.

Dograh currently has 4,821 active 384-dimensional code vectors. The persisted
4-bit TurboVec index is about 0.99 MB and returned an expected result in the top
five for all eight frozen semantic benchmark queries. Warm query time was below
one millisecond at Dograh scale.

The benchmark is useful backend evidence, not final retrieval-quality proof.
Several ground-truth checks accept broad substrings, and some returned top hits
are tests rather than the desired implementation. Product retrieval still
needs source/test policy, richer relevance judgments, and real agent tasks.

### 3.2 PPR and BFS

**Settled by current evidence:** graph traversal must not rerank semantic search
before anchors are established.

Global query-time PPR was query-blind. It amplified generic high-degree
functions, helped none of five isolated queries, hurt one, and was neutral on
four. BFS did not solve the underlying problem because the problem was not the
choice of traversal algorithm. The error was using topology as a substitute
for semantic intent.

PPR remains potentially useful after an anchor is known, on a bounded local
graph, for questions such as "what territory depends on this function?" It is
not part of the default semantic ranking path.

### 3.3 MMR

**Settled by current evidence:** MMR is not a default function-retrieval stage.

Functions are not interchangeable documents. Penalizing similar results can
remove the exact implementation and its closely related wrapper, which are
often precisely what a coding agent needs together. Display-level file dedup or
result grouping may still be useful, but it should not alter the core candidate
ranking without a measured task-specific reason.

### 3.4 Infomap communities

**Recommended role:** offline execution-flow communities and repository maps.

Infomap can organize the call graph into understandable flow regions, label
subsystems, summarize a large blast radius, and give an agent a boundary around
an already selected anchor. It should be computed outside the hot query path
and persisted with a generation ID.

It should not filter semantic candidates by default. Earlier experiments showed
that hard community boundaries can hide legitimate cross-cutting functions.

### 3.5 Leiden communities

**Recommended challenger, not yet validated in this repository:** use Leiden on
a symmetrized trusted structural graph to capture cohesive modules. Infomap and
Leiden answer different questions:

- Infomap: where directed execution flow tends to move;
- Leiden: which nodes form a densely connected structural module.

For very large graphs, Leiden is also a plausible first partitioner before more
expensive analysis. It must earn this role on Dograh and at least one differently
shaped repository before product adoption.

### 3.6 Betweenness centrality

**Recommended role:** one risk and architecture signal, never a verdict.

Betweenness can identify chokepoints through which many paths pass. It is useful
for change review, architecture maps, and summarizing a large blast radius.
Generic utility functions can also score highly, so the product must return the
score, percentile, graph generation, exclusions, and corroborating evidence.

The product must not reduce risk to `severity = betweenness * another_score` and
then hide the components. Risk is a multi-dimensional evidence set.

### 3.7 GraphSAGE

**Settled by current evidence:** GraphSAGE encodes structural role, not semantic
intent.

The two-layer mean-aggregator experiment achieved roughly 0.81 AUC and 0.82 AP
on held-out CALLS link prediction. More importantly, its nearest neighbors were
often architectural peers with little overlap with semantic neighbors.

Product role:

- find structural analogues;
- find parallel implementations at the same architectural tier;
- detect possible structural drift under one frozen model;
- help an agent compare an unfamiliar function with an established pattern.

Non-role:

- default semantic reranking;
- proof that two functions mean the same thing;
- comparison of embeddings produced by independently trained, unaligned models.

### 3.8 Co-change

**Settled by current evidence:** co-change is one of the strongest product
differentiators, provided the ingestion foundation is corrected.

It answers a question ordinary static search cannot answer cheaply:

> Which functions historically move together, how often, in which commits, and
> is the relationship likely to be a shared dependency or merely a shared
> commit?

Current caveats are substantial:

- code-hash dedup is not correctly suppressing unchanged states;
- mass changes can create noisy pairs;
- current import evidence is incomplete;
- pair accumulation and promoted edges still depend on experiment artifacts.

The product version must be an incremental durable accumulator keyed by real
changed symbols, not an in-memory or JSON batch result.

### 3.9 Multi-seed connector and union optimization

**Settled by current evidence:** the tested bounded form of the problem is
feasible.

Given a natural-language problem and three or four known function seeds, the
current experiment can:

1. use a current-source-aware directed graph;
2. find candidates with bounded seed coverage;
3. choose a root with a mode-conditioned lexicographic rule;
4. enumerate a small set of simple paths per seed;
5. optimize the union of those paths exactly inside declared bounds;
6. return exactness, truncation, alternatives, and feature contributions.

Real Dograh shared-prefix cases changed the selected route and reduced one union
from six edges to four and another from seven edges to five.

**Settled limitation:** there is no universally correct path-sharing weight.
The experiment uses:

```text
J_alpha = unique_union_weighted_cost + alpha * summed_path_cost
```

One case switched back to independent paths at `alpha=1`; another required a
much larger value. Query cosine alone did not reliably express whether the task
wanted a shared core or preserved branch-specific paths.

Product consequence: the relationship tool should compare objectives or accept
an explicit connection style. It must not silently claim one universal
"optimal" connection.

### 3.10 Current-source call resolution

**Settled by current evidence:** better topology matters more than more clever
path math.

The conservative overlay improved the frozen connector suite by resolving:

- exact same-file calls;
- explicit imported functions;
- module aliases;
- imported class methods;
- statically known `self` and `cls` methods.

Receiver-dependent attribute calls, runtime dispatch, star imports, and many
ambiguous cross-file calls remain unresolved. The experiment found thousands of
unresolved attribute occurrences. Those must be represented as uncertainty,
not guessed into the trusted graph.

### 3.11 Agent-value evaluation

**Open:** the product has not yet been proven to make real coding agents better
across real edit tasks.

The current MCP-vs-baseline report showed fewer tool calls and more correct
answers on five scripted checks, but also higher token output and higher total
latency. The baseline implementation missed files in some tasks, and the first
semantic query paid a model cold-start cost. This is useful harness development,
not a product claim.

No plan should repeat the old "5x token reduction" statement as established
product evidence.

## 4. Product model: one runtime, several owned state classes

The current code mixes Helix nodes, JSON topology, exact-vector JSON, a binary
TurboVec index, co-change JSON, GraphSAGE NPY files, and an in-memory igraph.
There is no shared snapshot contract among them. The live `graph_payload.json`
currently has zero nodes and 11,635 edges, which demonstrates why fallback
artifacts cannot be serving truth.

The product should use three explicit state classes.

### 4.1 Durable canonical state

HelixDB stores:

- repository and snapshot metadata;
- stable internal symbol identities;
- versioned file and function states;
- exact trusted graph edges;
- ambiguous/unresolved edge evidence in a separate confidence class;
- edge provenance and resolver version;
- commit history and temporal relationships;
- memory annotation text and provenance;
- co-change counters or promoted co-change edges;
- derived scalar and categorical analysis results;
- active generation pointers and compatibility metadata.

The stable identity is internal. The agent should normally see a qualified
symbol, relative file, line range, and opaque follow-up handle. It should not be
forced to construct repository-prefixed node IDs.

### 4.2 Managed vector generations

The product owns binary, checksummed vector generations on its persistent data
volume. They are not user-managed loose artifacts.

For each vector family, a generation contains:

- exact normalized float vectors in a memory-mappable binary bundle;
- stable vector IDs and symbol/state references;
- a persisted TurboVec base index;
- a small TurboVec delta index;
- a suppression/tombstone set;
- a mutation journal;
- a manifest with counts, checksums, model information, and graph snapshot.

Vector families remain separate:

- code semantic vectors;
- memory annotation vectors;
- GraphSAGE structural vectors.

This is not the same as the current `.turbovec_*.json` arrangement. JSON exact
vector mirrors should be removed from the serving path.

### 4.3 Ephemeral computation state

Full-graph CSR or igraph representations exist only inside an analysis job.
Bounded query micrographs exist only for algorithms such as local PPR or the
multi-seed union optimizer.

Ephemeral memory may contain:

- loaded TurboVec indexes;
- a memory-mapped exact vector matrix;
- embedding-model state;
- symbol lookup cache;
- bounded graph query cache;
- active-generation manifest;
- temporary CSR for a cold analysis job.

No durable fact exists only in process memory. A process restart may lose cache
warmth, but not graph truth, vectors, memory annotations, or analysis metadata.

## 5. Public agent tool surface

The agent-facing surface should contain a small number of task-oriented tools.
Low-level traversal and algorithm controls remain internal or diagnostic.

Every public tool should accept natural names, file paths, ranges, current diff,
or opaque handles returned by another tool. Raw internal IDs are supported only
as an implementation detail.

Every public response uses a common envelope:

```text
snapshot
freshness and stale components
answer-grade evidence
unresolved or ambiguous evidence
compact algorithm trace
exactness and truncation
token and node budget used
continuation handle if more detail exists
```

### 5.1 `find_code_context`

Primary question:

> I have a problem statement but do not yet know the right code.

Input:

- natural-language task or bug description;
- optional files, directories, languages, or symbol hints;
- optional source/test/generated-code policy;
- token budget and result budget.

Internal strategy:

1. Embed the query with the warm local code-query model.
2. Search the code semantic base and delta indexes.
3. Resolve hits to the active graph snapshot in one batched Helix query.
4. Apply policy filters without changing semantic score provenance.
5. Group wrappers, implementations, tests, and adjacent symbols.
6. Fetch one-hop trusted callers/callees/import context for the strongest seeds.
7. If several strong seeds have a bounded trusted connection, include a compact
   connection preview, not a hidden full optimization.
8. Return snippets and exact file locations, not a giant graph dump.

Algorithms:

- semantic embeddings and TurboVec;
- exact symbol lookup;
- bounded directed traversal;
- optional community labels for grouping;
- no default PPR, MMR, GraphSAGE, or co-change expansion.

Agent effectiveness gain:

- fewer blind searches and file reads;
- better entry points when names differ from the user language;
- immediate distinction among implementation, wrapper, configuration, and test
  candidates.

Failure behavior:

- return low-confidence scores and lexical alternatives when semantic scores
  are weak;
- never manufacture a graph connection from ambiguous edges;
- expose whether test code dominated the candidate set.

### 5.2 `inspect_symbol`

Primary question:

> I know a function, class, file, or returned handle. What do I need to know
> before changing or depending on it?

Input:

- symbol/file/range/handle;
- optional topic;
- requested sections such as callers, callees, history, analogues, or coupling;
- traversal and token budgets.

Output:

- exact current symbol and source location;
- current code or a bounded snippet;
- direct callers, callees, imports, inheritance, and test references;
- community/subsystem labels;
- betweenness percentile and degree evidence;
- strongest co-change partners with commit evidence;
- relevant memory annotations;
- optional GraphSAGE structural analogues;
- unresolved call sites involving this symbol;
- active and stale generation information.

Algorithms:

- exact Helix lookup and bounded traversal;
- co-change lookup;
- memory vector search limited to this symbol history;
- GraphSAGE nearest neighbors only when requested or clearly useful;
- no global repository ranking.

Agent effectiveness gain:

- replaces repeated caller/callee/history commands with one evidence pack;
- reveals hidden historical constraints and parallel patterns before an edit;
- makes uncertainty visible.

### 5.3 `connect_code`

Primary question:

> These two to four functions appear relevant. How are they meaningfully
> connected for this problem?

Input:

- two to four symbols or handles;
- natural-language problem statement;
- direction mode: auto, common-caller, shared-callee, or mixed;
- connection style: compare, shared-core, preserve-branches, or balanced;
- hop, node, path, and combination budgets.

Internal strategy:

1. Resolve every seed exactly.
2. Ask Helix for a bounded directed candidate graph using only declared edge
   confidence classes.
3. Separate feasibility from ranking: seed coverage is a constraint, not a
   giant additive score.
4. Rank root candidates with mode-conditioned lexicographic dimensions.
5. Enumerate a bounded set of simple path alternatives per seed.
6. Compare at least these outputs when `connection_style=compare`:
   independent best paths, pure shared-union paths, and a balanced objective.
7. Optimize exact combinations below the declared cap; otherwise use a bounded
   heuristic and set `exact=false`.
8. Return rejected near-misses and the first deciding dimensions.

Persisted features used by the tool:

- edge direction and relation type;
- static resolution provenance;
- confidence class;
- call-site count and locations;
- current/stale status;
- source/test/generated classification;
- hub and centrality metadata;
- co-change evidence when the requested mode allows it;
- node semantic similarity to the query.

Important product behavior:

- do not hide one universal scalar behind the result;
- do not infer the desirability of path sharing solely from node cosine;
- when the task intent is ambiguous, return the competing connection models so
  the calling agent can choose with its own reasoning;
- always return path witnesses and edge provenance.

Agent effectiveness gain:

- turns the manual "read callers, read callees, compare 2-3 hop paths" process
  into a bounded evidence-backed operation;
- reduces missed intermediate functions and stale-topology mistakes;
- explains why a connection was selected, not merely that a path exists.

### 5.4 `analyze_change`

Primary question:

> Given the current diff or a proposed symbol change, what else should the agent
> inspect, modify, or test?

Input:

- default: current Git worktree diff;
- optional commit, staged diff, patch text, file list, or symbol list;
- optional proposed intent;
- analysis budget.

Internal strategy:

1. Map changed ranges to exact symbol states.
2. Classify added, removed, signature-changed, body-changed, and moved symbols.
3. Traverse reverse trusted CALLS for blast radius.
4. Add co-change partners that were not changed.
5. Add relevant memory invariants and prior fixes.
6. Use betweenness, fanout, community boundaries, public API status, and change
   type as separate risk dimensions.
7. Identify impacted tests from exact graph evidence first, then clearly label
   heuristic suggestions.
8. Return missing-partner warnings and concrete path witnesses.

Output should distinguish:

- must inspect;
- likely inspect;
- historical warning;
- suggested tests;
- unresolved impact;
- low-confidence heuristic.

Algorithms:

- AST diff mapping;
- Helix reverse traversal;
- co-change;
- memory retrieval;
- betweenness and community boundary metadata;
- optional local PPR only for summarizing a very large post-anchor territory.

Agent effectiveness gain:

- catches forgotten wrappers, callers, configuration paths, and historically
  coupled changes;
- helps the agent choose an edit boundary before spending tokens reading every
  candidate;
- makes review and test planning a by-product of the same context engine.

Restriction:

- `edit_code` should not be part of this product toolset. The coding agent
  already owns editing, patching, and validation. GraphDB provides context and
  evidence.

### 5.5 `trace_history`

Primary question:

> Why does this code exist, how did it evolve, and which prior decisions are
> relevant to the current task?

Input:

- symbol/file/handle;
- natural-language historical topic;
- optional time or commit range;
- result budget.

Internal strategy:

1. Resolve the active symbol identity.
2. Traverse versioned states and commits.
3. Search memory annotation vectors for the topic.
4. Return source diffs, commit messages, annotation evidence, typed change
   relations, and co-changed states.
5. Mark annotations as machine-generated, human-confirmed, superseded, or
   disputed.

Agent effectiveness gain:

- avoids reintroducing previously fixed bugs or violating a design rationale;
- provides semantic history without requiring the agent to read a long git log;
- distinguishes source-derived fact from LLM-authored interpretation.

### 5.6 `map_codebase`

Primary question:

> How is this repository or subsystem organized, where are the entry points,
> boundaries, and risky connectors?

Input:

- repository, directory, file, community, symbol, or natural-language scope;
- map depth and token budget;
- view: execution flow, structural cohesion, change coupling, or public API.

Internal strategy:

- use persisted Infomap and/or Leiden community labels;
- summarize entry points, boundary nodes, central connectors, public APIs, and
  dominant dependencies;
- use semantic retrieval only to select a natural-language scope;
- return a hierarchy and small representative paths rather than every node.

Agent effectiveness gain:

- helps an agent orient before searching individual functions;
- makes architectural communities useful in the same way graph-oriented code
  products do, while adding semantic and historical evidence;
- gives large blast-radius results a compact subsystem summary.

### 5.7 Migration from the current MCP surface

| Current tool | Product destination |
|---|---|
| `search_code_semantics` | Replace with `find_code_context`; remove igraph dependency and stale PPR language. |
| `search_code_semantics_helix` | Internal backend diagnostic only. |
| `explain_coupling` | Evidence source for `inspect_symbol`, `connect_code`, and `analyze_change`. |
| `find_structural_siblings` | Optional section of `inspect_symbol`. |
| `trace_blast_radius` | Internal traversal used by `inspect_symbol` and `analyze_change`. |
| `commit_review` | Rebuilt as `analyze_change` with diff mapping and exposed evidence dimensions. |
| `select_tests` | Advisory section of `analyze_change`; do not claim minimum coverage. |
| `query_function_history` | Topic-ranking stage inside `trace_history`. |
| `trace_semantic_evolution` | Consolidated into `trace_history`. |
| `get_code_time_travel_diff` | Evidence primitive inside `trace_history`. |
| `get_temporal_vulnerability_trace` | Optional historical-impact mode inside `trace_history` or `analyze_change`. |
| `annotate_commit` | Lifecycle command/workflow, not a normal read-only context tool. |
| `pipeline_status` | `graphdb status`/`doctor` plus a small health resource. |
| `edit_code` | Remove from GraphDB; the coding agent owns edits and tests. |

## 6. Internal services and diagnostic tools

These capabilities should exist, but they should not compete for the coding
agent's attention in the normal MCP manifest.

### 6.1 Internal query primitives

- exact symbol lookup;
- batch vector-ID resolution;
- semantic base/delta search;
- bounded outgoing/incoming traversal;
- shortest path and path enumeration;
- local PPR;
- community lookup;
- co-change lookup;
- structural-neighbor lookup;
- state/history traversal;
- score and objective explanation.

### 6.2 Lifecycle CLI

Recommended commands:

- `graphdb init`: initialize configuration, data volume, schema, and repository.
- `graphdb index`: one-time historical bootstrap or explicit full rebuild.
- `graphdb sync`: parse and index current Git/worktree deltas.
- `graphdb watch`: keep the worktree overlay current.
- `graphdb serve`: start the daemon and standard MCP transport.
- `graphdb status`: compact human-readable health and generation state.
- `graphdb doctor`: verify Docker, Helix, schema, index manifests, checksums,
  model availability, and MCP connectivity.
- `graphdb compact`: merge vector base/delta and optionally refresh analytics.
- `graphdb rebuild --family <name>`: rebuild only a damaged or incompatible
  vector or analysis family.
- `graphdb export --format json`: debug or visualization export only.
- `graphdb annotate <commit>`: generate candidate semantic memory for review.

### 6.3 Diagnostic APIs

- inspect active generation and stale components;
- inspect one edge and its provenance;
- explain one result score or lexicographic decision;
- compare raw, trusted, and ambiguous topology;
- inspect vector base/delta counts and suppression rate;
- run bounded algorithm ablations;
- validate graph/vector checksums and identity coverage.

These are for development, evaluation, and support. They are not default public
agent tools.

## 7. Algorithm organization

Algorithms should be organized by execution lifecycle, not by placing every
formula in one query pipeline.

### 7.1 Algorithm decision matrix

| Algorithm or subsystem | Execution location | Persisted result | Product role | Current decision |
|---|---|---|---|---|
| Tree-sitter and language resolver | Incremental indexer | Symbols, states, trusted/ambiguous edges, provenance | Foundation for every graph workflow | Core; current simple-name resolution must be replaced. |
| Code embeddings + TurboVec | Indexer plus hot runtime | Exact vector bundle, base/delta indexes, model metadata | Semantic entry points | Core. |
| Directed bounded traversal | Helix hot query | Exact graph edges and traversal indexes | Callers, callees, blast radius, path candidates | Core. |
| Distance center and seed coverage | Bounded hot query | Optional cache only | Structural connector/root baseline | Keep as control and candidate stage, not a final answer alone. |
| Multi-seed path-union optimizer | Bounded hot query | Optional bounded-result cache | Explain 2-4 function relationships | Promote after persisted-topology and held-out agent evaluation. |
| Co-change | Incremental history accumulator | Counts, Jaccard/category, commit evidence | Historical coupling and missing-partner warnings | Core differentiator after dedup/noise fixes. |
| Temporal state graph | Indexer and Helix traversal | Function states, commits, version edges | Time travel and evidence for history | Core. |
| Semantic memory | Async annotation plus memory vector runtime | Versioned annotations, evidence, memory vectors | Explain rationale, invariants, and prior fixes | Core differentiator after annotation quality controls. |
| Infomap | Background analysis worker | Flow community IDs, hierarchy, boundary metadata | Execution-flow maps and summaries | Keep offline; do not use as default retrieval filter. |
| Leiden | Background challenger/partitioner | Cohesion community IDs | Structural module maps and possible large-graph partitioning | New experiment; not yet adopted. |
| Betweenness | Background analysis worker | Score, percentile, method, generation | Chokepoint/risk evidence | Keep as one exposed dimension. |
| GraphSAGE | Background training/inference, TurboVec hot lookup | Model generation and structural vectors | Structural analogues and drift | Keep separate from semantic retrieval. |
| PPR | Bounded query micrograph | Short-lived cache only | Post-anchor territory ranking/summarization | Optional and task-specific; rejected as semantic reranker. |
| MMR | None by default | None | Possible display-only diversification | Rejected for function ranking. |

The matrix is deliberately asymmetric. "We have an implementation" is not a
reason to place an algorithm in the hot path. Its location is chosen by the
question it answers, its cost, and the evidence required to keep it trustworthy.

### 7.2 Incremental indexer algorithms

Run immediately when source or Git state changes:

- tree-sitter or language-native parsing;
- symbol and state identity resolution;
- code hash and AST fingerprinting;
- current-source call resolution;
- import and inheritance resolution;
- changed-range to symbol mapping;
- semantic embedding of changed content;
- commit touch and co-change counter updates;
- active working-tree overlay updates.

These outputs must be fresh before a query is considered fully ready.

### 7.3 Background analysis algorithms

Run on a committed/staging graph generation:

- Infomap;
- Leiden experiments;
- exact or sampled betweenness;
- hub and degree statistics;
- GraphSAGE training and full-generation inference;
- community boundary summaries;
- co-change promotion and theme classification;
- repository map materialization.

These algorithms may lag behind an edit. The query response must state their
generation and staleness.

### 7.4 Hot query algorithms

Run under strict time, node, edge, path, and token budgets:

- TurboVec base/delta semantic search;
- exact symbol resolution;
- bounded Helix traversal;
- small local PPR when explicitly appropriate;
- multi-seed root selection and path-union optimization;
- response grouping and evidence packing.

The hot path never loads the whole graph or rebuilds a global algorithm.

## 8. Directed multi-dimensional edge design

The earlier intuition that directed, multi-dimensional weighted edges will help
is correct, with one correction: persist evidence dimensions, not one universal
"optimal weight."

### 8.1 CALLS edge fields

Recommended fields:

- source and target stable handles;
- relation subtype: function call, method call, constructor call, callback
  registration, decorator relation, or other language-specific kind;
- direction: caller to callee;
- provenance class;
- confidence class and optional calibrated probability;
- resolver and parser versions;
- graph generation and worktree overlay sequence;
- call-site count;
- bounded list of source locations or a separate call-site relation;
- first-seen and last-seen commits;
- current, deleted, or historical status;
- source/test/generated/vendor classifications;
- dynamic-observation support when future runtime traces exist;
- unresolved receiver/type evidence when no exact target is known.

### 8.2 Provenance classes

At minimum:

1. exact same-file lexical target;
2. exact explicit imported function;
3. exact module-alias target;
4. exact imported class method;
5. exact statically known `self` or `cls` method;
6. type-inferred target;
7. runtime-observed target;
8. unique-name heuristic;
9. ambiguous name-only candidate;
10. unresolved attribute call.

Trusted product traversals should default to classes 1-7. Heuristic and
ambiguous classes are returned separately unless a tool explicitly requests an
exploratory mode.

### 8.3 Why one precomputed scalar is wrong

Different tools optimize different facts:

- semantic discovery prioritizes query similarity;
- blast radius prioritizes reverse reachability and confidence;
- a common-caller relationship prioritizes outgoing seed support;
- a shared-callee relationship prioritizes semantic relevance after coverage;
- change review prioritizes public boundary, fanout, history, and missing
  co-change partners;
- path explanation may prefer compactness;
- branch-specific debugging may explicitly reject shared paths.

The latest multi-seed experiment already showed that an additive formula can
rank incidental one-hop utilities above a semantically correct two-hop core.
The product therefore uses:

- hard feasibility constraints;
- lexicographic decisions for dimensions with different meanings;
- a declared objective only within a bounded path-selection stage;
- an explanation ledger for every selected result.

## 9. Dynamic dispatch and ambiguous calls

Dynamic dispatch is not one missing algorithm. It is a language-analysis
program with several confidence levels.

### 9.1 V1 requirement

- preserve the current conservative static resolver work;
- persist exact provenance;
- never promote unresolved attribute names into trusted CALLS merely because a
  function name is globally unique;
- return unresolved calls as first-class evidence;
- measure how often unresolved edges block real agent tasks.

### 9.2 V1.5 language-aware resolution

For Python, investigate type and points-to evidence from a maintained language
engine such as Pyright or Jedi rather than extending ad hoc AST name matching
indefinitely. For TypeScript, Java, Rust, and other languages, use compiler or
language-server symbol resolution where available.

The language adapter produces the same edge provenance contract. The graph
query layer does not need to know which compiler produced the evidence.

### 9.3 Future runtime evidence

Optional test or development traces can add `runtime-observed` edges and counts.
They complement static edges but do not replace them because runtime coverage
is incomplete.

## 10. Versioned graph and generation contract

The product must be able to answer: "Which exact graph, vectors, and analytics
produced this result?"

### 10.1 Generation dimensions

Track separately:

- `repo_snapshot_id`: committed source snapshot;
- `worktree_overlay_seq`: uncommitted changes layered on the snapshot;
- `parser_version`;
- `resolver_version`;
- `graph_generation`;
- `code_vector_generation`;
- `memory_vector_generation`;
- `structural_vector_generation`;
- `community_generation`;
- `centrality_generation`;
- `cochange_generation`.

These are not required to advance simultaneously. Compatibility rules decide
which combinations may be served.

### 10.2 Atomic promotion

Recommended flow:

1. Create a staging repository snapshot record.
2. Parse and write staging symbols, states, and trusted edges.
3. Build staging code-vector base/delta artifacts.
4. Validate counts, identity coverage, checksums, dimensions, and model IDs.
5. Export one trusted snapshot to the analysis worker.
6. Compute communities, centrality, GraphSAGE, and other derived artifacts.
7. Validate their coverage and metadata.
8. Atomically move the active-generation pointer.
9. Retain the previous valid generation for rollback.

Working-tree changes should use a lightweight overlay over the last committed
snapshot. A global analysis may remain one generation behind, but the result
must report that fact.

### 10.3 Query response freshness

Example:

```json
{
  "snapshot": "commit:abc123",
  "worktree_overlay_seq": 18,
  "fresh": ["symbols", "calls", "code_vectors"],
  "stale": {
    "communities": {"by_generations": 1},
    "graphsage": {"by_generations": 1}
  },
  "exact_within_bounds": true,
  "truncated": false
}
```

## 11. Vector architecture

### 11.1 What TurboVec is

The installed TurboVec 0.8.0 backend is a quantized exhaustive SIMD scan, not
an HNSW or IVF candidate-pruning ANN. Its score is approximate because vectors
are quantized, but it scans every unsuppressed vector.

Measured 384-dimensional 4-bit p50 query times were approximately:

- 5,000 vectors: 0.47 ms;
- 20,000 vectors: 1.42 ms;
- 80,000 vectors: 5.75 ms.

This is excellent for local and medium repository scale. Because cost remains
linear, sharding, community partitioning, or another ANN backend should be
introduced only when measured corpus size and p95 require it.

### 11.2 Reject rebuild-on-start

Warm startup must load the persisted vector generation. It must not bulk-read
Helix and rebuild every index.

Rebuilding on every start would:

- discard the purpose of persistence;
- transfer every exact vector through Helix and Python;
- allocate a full float matrix during startup;
- scale startup with repository size;
- make the MCP server unavailable while doing unrelated recovery work.

### 11.3 Immutable base plus mutable delta

For each vector family:

- immutable base TurboVec index;
- small mutable delta TurboVec index;
- suppression set for base IDs that were updated or deleted;
- durable mutation journal;
- exact vector generation bundle;
- atomic compaction into a new base.

Query:

1. Search base and delta concurrently.
2. Remove suppressed base IDs.
3. Merge by stable vector ID.
4. Adaptively over-fetch if suppression removed too many base hits.
5. Resolve the final IDs through one batched Helix lookup.

Update:

1. Canonically write the new state and embedding metadata.
2. Append a durable mutation record.
3. Add the vector to delta.
4. Suppress or remove the old vector.
5. Persist the small delta safely.

Compaction triggers should use measured conditions, not a fixed "100 changed
functions" rule:

- delta/base ratio;
- tombstone and suppression ratio;
- over-fetch rate;
- mutation journal size or age;
- p95 search degradation;
- model, dimension, or normalization change.

A provisional starting trigger is a delta around five percent of the base, but
that number must be benchmarked.

### 11.4 Current adapter defect

The existing adapter loads a native index without repopulating its `_vectors`
dictionary. A later insert marks the index dirty, and its rebuild path can then
construct an index from only the newly buffered vectors. The product adapter
must mutate the native index correctly or implement the base/delta design before
incremental serving is claimed.

### 11.5 Placement of vector families

Code semantic embedding:

- belongs to immutable function content, model revision, and preprocessing;
- attach to `FunctionState` or a separate `CodeEmbedding` artifact;
- the active `FunctionIdentity` points to the current state/vector.

Memory embedding:

- belongs to a versioned `MemoryAnnotation`, not a mutable singleton property on
  `FunctionState`;
- supports multiple annotations and annotation revisions.

GraphSAGE embedding:

- belongs to stable symbol identity plus complete graph generation and model;
- cannot be treated as a timeless property of a function.

Every embedding records:

- family;
- model ID and exact revision;
- preprocessing version;
- dimension;
- metric and normalization;
- source/content hash;
- repository snapshot or graph generation;
- creation time;
- vector ID and index generation.

### 11.6 Exact vector ownership

**Recommended default:** keep exact vectors in the managed generation bundle and
store their references and metadata in Helix. This avoids slow bulk object-store
reads during normal operation and provides a fast, checksummed rebuild source.

**Open decision:** duplicate exact vectors into Helix as a recovery copy. The
benefit is database-contained recovery. The cost is substantial write and bulk
read overhead, more storage, and another path that must be validated. This is a
real product choice, not a query-time requirement.

## 12. Semantic memory architecture

The word "memory" must be separated into persistent product memory and
disposable process memory.

### 12.1 Persistent function memory

Current source and Git history provide facts. LLM-generated annotations provide
interpretation. They must not be collapsed into one mutable string.

Recommended nodes:

- `FunctionState`: immutable source state at a commit/content hash;
- `MemoryAnnotation`: one claim or explanation linked to one or more states,
  commits, and evidence locations;
- `MemoryEmbedding`: vector materialization for a specific annotation version;
- optional `MemoryReview`: human or agent acceptance, rejection, correction, or
  supersession.

Annotation fields:

- type: rationale, invariant, bug fix, migration, behavior change, warning, or
  rejected approach;
- text;
- evidence commits, files, ranges, and diffs;
- author kind and model revision;
- confidence;
- creation time;
- status: candidate, accepted, superseded, disputed;
- content hash and annotation version.

Multiple annotations per state should be allowed. A security fix, a performance
tradeoff, and an API compatibility warning are different memories.

### 12.2 Annotation lifecycle

1. A commit or explicit request creates candidate annotations.
2. The annotator receives the exact diff, existing accepted memories, and
   relevant neighboring changes.
3. Candidate text and evidence are stored separately from accepted memory.
4. Automated validation checks referenced symbols and commits.
5. Human or agent review can accept, correct, supersede, or reject.
6. `trace_history` ranks accepted annotations first and clearly labels the rest.

### 12.3 Memory query

Memory semantic search uses its own base/delta index. It should search within a
symbol timeline by default and expand to related symbols only when explicitly
requested. Results include source evidence and temporal ordering.

### 12.4 Disposable process memory

Use bounded caches with generation-aware keys:

- query embeddings;
- symbol resolution;
- graph neighborhoods;
- community summaries;
- connection results;
- model state.

Every cache entry includes the graph/vector generation. A generation change
invalidates or namespaces the cache. No JSON file is used as an implicit cache
truth.

## 13. Incremental indexing

### 13.1 Historical bootstrap

One-time bootstrap:

1. Discover repository languages and ignore rules.
2. Walk Git history with an explicit depth/range policy.
3. Create stable symbol identities and immutable states.
4. Use code hashes to avoid generating unchanged states.
5. Build current and historical graph relations.
6. Accumulate genuine commit touches for co-change.
7. Embed only required families.
8. Commit the first complete generation.

The product should default to current code plus versioned semantic memory. Full
historical code-vector search is an optional later feature because it multiplies
vector volume without proving a core workflow yet.

### 13.2 Worktree updates

On a changed file:

1. Parse only the changed file and directly affected resolution units.
2. Diff old and new symbols by qualified identity, content hash, and signature.
3. Update added, changed, moved, and deleted symbol states.
4. Re-resolve outgoing calls and affected incoming ambiguous candidates.
5. Embed only changed active content hashes.
6. Update vector delta and suppression set.
7. Update the worktree graph overlay atomically.
8. Invalidate bounded caches.
9. Mark global analytics dirty and schedule a background refresh.

Fresh symbols, edges, and code vectors should be queryable immediately. The
agent should not wait for Infomap, betweenness, or GraphSAGE retraining.

### 13.3 Commit updates

When the user commits:

- convert the working-tree overlay into a committed snapshot;
- update commit/state history;
- update durable co-change counters;
- generate candidate memory annotations asynchronously;
- compact vectors only if a measured trigger is reached;
- schedule derived analysis for the new graph generation.

### 13.4 History rewrite and configuration changes

Force an explicit rebuild or new generation when:

- Git history is rewritten across the indexed range;
- ignore rules materially change;
- parser or resolver semantics change;
- embedding model/dimension/normalization changes;
- identity rules change;
- a checksum or count invariant fails.

## 14. Offline analysis worker and in-memory graph policy

The product will still use memory for algorithms that require a graph matrix or
CSR. The critical distinction is when, why, and who owns the result.

### 14.1 Acceptable full-graph memory use

- initial generation analysis;
- scheduled community or centrality refresh;
- GraphSAGE data preparation/training;
- offline evaluation and algorithm comparison;
- corruption validation.

The worker reads one declared Helix snapshot, builds CSR once, computes derived
results, writes them back with metadata, and exits or releases the graph.

### 14.2 Unacceptable full-graph memory use

- every MCP startup;
- every semantic search;
- every coding-agent session;
- as a fallback when Helix or a manifest is inconsistent;
- to combine topology from JSON with nodes from a different generation.

### 14.3 Scale strategy

Provisional engineering tiers:

- small: below 50,000 active symbols and 500,000 trusted edges;
- medium: 50,000-500,000 symbols or up to roughly 5 million edges;
- large: above 500,000 symbols or 5 million edges.

Initial policies:

- small: exact communities and exact betweenness if budgets pass;
- medium: exact communities, sampled betweenness, mini-batch GraphSAGE;
- large: partitioned/hierarchical analysis, sampled centrality, bounded hot
  traversal, and a benchmark-driven vector backend decision.

These are starting budgets, not measured Helix limits.

## 15. Docker and local runtime

### 15.1 Current facts

The currently running stack is not a distributable product definition:

- Helix image: `ghcr.io/helixdb/enterprise-dev:latest`;
- image license label: proprietary;
- image version: unpinned `latest`;
- separate MinIO container;
- current memory use is approximately 954 MiB for Helix and 574 MiB for MinIO;
- the Helix container has no direct host mount, while MinIO uses a Docker volume;
- this repository currently has no product Dockerfile or Compose file.

Helix licensing, redistribution rights, version pinning, and the acceptable
local memory floor are release blockers, not documentation details.

### 15.2 Recommended process topology

```text
coding agent / IDE
        |
   standard MCP
        |
    graphdbd daemon
      |        |
      |        +-- managed vector generations and bounded caches
      |
      +-- HelixDB service
              |
              +-- its required durable object/storage service

optional background analysis worker
```

`graphdbd` owns:

- repository watcher and incremental indexer;
- embedding model lifetime;
- vector base/delta lifecycle;
- MCP tool orchestration;
- generation validation and health;
- analysis job scheduling;
- compact structured logs.

Only one daemon should own a repository data directory. MCP stdio bridges and
the VS Code extension connect to that daemon rather than launching independent
full pipelines.

### 15.3 Warm startup

1. Start or connect to the pinned Helix service.
2. Read the tiny active-generation record.
3. Validate manifests and checksums.
4. Load and prepare persisted vector base/delta indexes.
5. Warm the local query embedding model.
6. Start MCP readiness.
7. Schedule non-blocking repair for stale derived analytics.

No full graph export and no vector rebuild occur on a healthy warm start.

### 15.4 Degraded startup

- If a derived analysis is stale, serve fresh graph/vector results and report
  the stale algorithm.
- If one vector family is corrupt, disable only tools that require it and
  rebuild that family in the background.
- If the active graph generation is unavailable, serve the previous valid
  generation only when it is explicitly marked stale.
- Never silently load `graph_payload.json` as a replacement.

### 15.5 Packaging gate

Before a public Docker-first claim, decide one of these:

1. Helix provides a redistributable, pinned local image with acceptable terms
   and footprint.
2. The user supplies/manages Helix as an external prerequisite.
3. GraphDB adopts a different embedded/distributable graph backend for the local
   product.

The code should keep Helix-specific query code behind a narrow repository-owned
storage boundary, but should not build a speculative universal database plugin
system until this gate is answered.

## 16. Agent effectiveness and efficiency design

### 16.1 Let the agent choose workflows, not algorithms

The MCP model already supplies the intelligence layer. GraphDB should give it
clear tools whose names match the situations a coding agent encounters:

- unknown problem -> `find_code_context`;
- known symbol -> `inspect_symbol`;
- several candidates -> `connect_code`;
- current diff -> `analyze_change`;
- historical why -> `trace_history`;
- architecture orientation -> `map_codebase`.

The agent should not choose among PPR, Infomap, GraphSAGE, betweenness, or a
Steiner variant. Those are internal strategies with explicit product roles.

### 16.2 Compact context packs

Returning more graph is not automatically better. Each response should spend a
declared token budget on:

1. exact answer-grade symbols;
2. path or historical evidence;
3. short source snippets;
4. uncertainty and stale-state warnings;
5. optional continuation handles.

Prefer:

- five high-quality functions with evidence;
- a four-node path with exact call sites;
- three relevant memories with commits;

over:

- a 100-node subgraph;
- raw centrality tables;
- every co-change pair;
- opaque scalar scores.

### 16.3 Stable handles and follow-ups

Tool responses should return opaque handles that remain valid for the active
generation. The agent can pass them to follow-up tools without copying internal
IDs or re-resolving ambiguous names.

### 16.4 Evidence classes

Every finding should be typed:

- exact source fact;
- exact graph traversal;
- static inferred relation;
- historical statistical relation;
- learned structural similarity;
- LLM-authored memory;
- heuristic suggestion.

This prevents the agent from treating a GraphSAGE neighbor or co-change pair as
the same kind of truth as an exact call edge.

### 16.5 Latency targets

Provisional warm targets, to be benchmarked:

- exact symbol lookup p95: below 10 ms;
- semantic vector search p95 at the V1 scale target: below 15 ms;
- ordinary one-to-three-hop Helix traversal p95: below 50 ms;
- `find_code_context` server work excluding model cold start: below 300 ms;
- `inspect_symbol`: below 300 ms for the default evidence pack;
- bounded `connect_code`: below 1 second at normal caps;
- `analyze_change`: below 2 seconds for a small diff;
- healthy daemon warm readiness after Helix health: below 5 seconds;
- no normal edit or query blocks on a global algorithm refresh.

### 16.6 Token-efficiency targets

Do not optimize token count by withholding necessary evidence. Measure:

- total agent input/output tokens to a correct repository decision;
- number of source files read before the first correct edit;
- irrelevant functions returned;
- repeated tool calls caused by missing evidence;
- missed dependency and hallucinated symbol rates.

The product wins only if total task cost and decision quality improve, not if
one MCP response is shorter in isolation.

## 17. Product evaluation program

### 17.1 Real agent A/B harness

Use the same coding model, prompt, repository snapshot, time limit, and tool
budget in two conditions:

- baseline: normal shell/search/read/git/language-server tools;
- treatment: baseline tools plus GraphDB public MCP tools.

The agent must be allowed to solve the task naturally. Do not script baseline
steps that fail to locate files.

### 17.2 Task families

Build at least 30-50 real tasks across Dograh and two additional repositories:

- cold semantic discovery;
- known-symbol comprehension;
- two-to-four-function relationship explanation;
- bug fix with 2-3 hop dependencies;
- cross-module change planning;
- historical invariant recovery;
- co-change omission detection;
- blast-radius review;
- structural analogue discovery;
- architecture/subsystem explanation;
- test-impact suggestion;
- dynamic-dispatch adversaries;
- stale-topology and history-rewrite cases.

Use real past bugs and commits when possible, with held-out tasks that were not
used to tune the algorithms.

### 17.3 Primary product metrics

- task success and test pass rate;
- correctness of the chosen edit boundary;
- missed dependent functions;
- unnecessary changed functions;
- hallucinated files/symbols/relationships;
- regressions introduced;
- total tool calls;
- total tokens;
- wall time, separated into cold and warm;
- graph tool p50/p95;
- user corrections required;
- confidence calibration and stale-result handling.

### 17.4 Algorithm ablations

For each relevant task family compare:

- semantic only;
- semantic plus exact traversal;
- plus co-change;
- plus memory;
- plus communities;
- plus centrality;
- plus GraphSAGE;
- independent paths versus union optimization;
- trusted edges versus trusted plus ambiguous edges.

An algorithm is promoted only when it improves a named workflow without
silently degrading another important one.

### 17.5 Promotion gates

Examples:

- `find_code_context`: improves task success or reduces total task cost on held
  out discovery tasks, with bounded irrelevant-context rate.
- `connect_code`: returns complete trusted connections on held-out 2-3 hop cases
  and accurately reports no connection when topology is insufficient.
- co-change warnings: useful precision at a declared top-k, not merely high
  recall from noisy commit pairs.
- GraphSAGE: structural analogue judgments by humans/agents beat semantic and
  degree baselines.
- test suggestions: measured precision/recall against real changed-code test
  outcomes before any "minimum" language is used.

## 18. Optional human surface: VS Code Graph Lens

The existing extension is useful as a secondary surface, not the product core.
It can consume the same stable public API for:

- hover summary;
- callers/callees;
- history timeline;
- focused subgraph;
- relationship comparison;
- change warnings.

The extension should not synthesize node IDs or auto-launch a separate Python
pipeline per workspace. It should connect to the managed daemon, use exact
symbol lookup, show freshness/confidence, and keep visualization bounded.

The extension is valuable for human trust and debugging because it exposes the
evidence the agent sees. It should be developed after the runtime and tool
contracts stabilize.

## 19. Implementation roadmap

This roadmap avoids a broad refactor until the product path is measured.

### Phase 0: Freeze the truth contract

Deliverables:

- generation manifest specification;
- internal stable symbol/state identity rules including qualified class scope;
- code-hash dedup tests;
- trusted/ambiguous/unresolved edge provenance schema;
- graph/vector compatibility checks;
- explicit removal of JSON fallback from the new serving experiment;
- benchmark corpus and real-agent task definitions.

Exit criteria:

- one Dograh snapshot has internally consistent node, edge, state, and vector
  counts;
- every served result identifies its generation;
- a deliberately mismatched artifact is rejected rather than silently loaded.

### Phase 1: Build the managed vector runtime

Deliverables:

- new base/delta TurboVec adapter;
- exact vector binary generation bundle;
- mutation journal, suppression set, and atomic compaction;
- batched Helix resolution;
- startup checksum and recovery behavior;
- semantic and memory family separation.

Exit criteria:

- warm startup loads without rebuilding;
- add, update, delete, restart, and compaction preserve the complete corpus;
- corruption disables or rebuilds only the affected family;
- p95 meets the chosen V1 scale budget.

### Phase 2: Persist the current-source graph

Deliverables:

- move the conservative overlay logic into the incremental indexer;
- persist resolver provenance and unresolved evidence;
- remove name-only ambiguous edges from the trusted default traversal;
- changed-file and changed-symbol diffing;
- immediate working-tree overlay updates;
- history-aware committed snapshots.

Exit criteria:

- the frozen training, holdout, dynamic/attribute, and shared-prefix cases pass
  against persisted topology;
- no query spends 7-12 seconds rebuilding an overlay;
- current-source results no longer depend on `graph_payload.json`.

### Phase 3: Build the first public MCP workflows

Initial tools:

- `find_code_context`;
- `inspect_symbol`;
- `connect_code`;
- `analyze_change`;
- `trace_history`;
- `map_codebase` may begin as an experimental tool until communities are ready.

Deliverables:

- standard MCP JSON-RPC transport;
- common response envelope;
- token budgets and continuations;
- exact symbol resolution and opaque handles;
- evidence typing and freshness;
- no `edit_code` tool.

Exit criteria:

- a coding agent can use natural inputs without constructing internal IDs;
- each tool has held-out task evidence and latency measurements;
- low-level experiment tools are hidden from the normal manifest.

### Phase 4: Productize historical intelligence

Deliverables:

- durable incremental co-change accumulator;
- mass-change filtering and genuine-change dedup;
- versioned `MemoryAnnotation` model;
- candidate/accepted/superseded memory lifecycle;
- memory base/delta vector index;
- `trace_history` and history sections in `inspect_symbol`/`analyze_change`.

Exit criteria:

- annotations never overwrite unrelated memories;
- every memory result has evidence and provenance;
- co-change warnings meet precision gates on held-out commits.

### Phase 5: Productize derived graph intelligence

Deliverables:

- analysis worker snapshot export;
- persisted Infomap communities;
- Leiden comparison;
- exact/sampled betweenness policy;
- GraphSAGE model and generation registry;
- structural vector serving index;
- local PPR only where a task-specific ablation justifies it.

Exit criteria:

- daemon queries never load the full graph;
- derived signals report generation and staleness;
- each promoted signal improves its named tool workflow.

### Phase 6: Package and dogfood

Deliverables:

- Helix licensing/distribution decision;
- pinned Compose stack or documented external-Helix mode;
- named volumes, health checks, migrations, resource limits, and logs;
- `graphdb init/index/sync/watch/serve/status/doctor`;
- one managed daemon shared by MCP and the VS Code extension;
- install/uninstall and backup/restore tests.

Exit criteria:

- a new repository reaches a usable warm MCP state through the documented user
  flow;
- restart preserves data and does not rebuild healthy indexes;
- resource use fits the agreed local-product envelope;
- a real coding-agent A/B shows a product-level gain.

### Phase 7: Refactor after stability

Only after the new path passes product gates:

- extract modules around the proven lifecycle boundaries;
- remove dead experiment scripts from the product package while preserving the
  research ledger and reproducible fixtures;
- formalize storage, algorithm, and MCP interfaces;
- package the VS Code extension against stable APIs;
- preserve experiment runners as a separate lab/evaluation package.

## 20. Recommended first implementation experiment

Do not begin with a repository-wide refactor. Build one narrow product slice on
Dograh:

1. Define one `RepoSnapshot` and generation manifest.
2. Persist the conservative current-source CALLS overlay into a staging graph
   generation with provenance.
3. Build a new code-vector immutable base plus mutable delta from Dograh.
4. Start a tiny daemon that loads those two generations without JSON or full
   igraph.
5. Implement exact symbol lookup and one `connect_code` facade over the existing
   bounded connector experiment.
6. Return independent, shared-core, and balanced alternatives with exactness.
7. Run the frozen multi-seed suite plus ten real agent tasks.
8. Inspect intermediate output and revise the first faulty subsystem.

This slice directly tests the hardest architectural claims:

- no JSON serving truth;
- no startup rebuild;
- Helix plus managed vector generations;
- current-source trusted topology;
- bounded in-memory optimization only;
- agent-usable evidence instead of raw algorithm output.

## 21. Decision register for discussion

### Decision A: V1 scale envelope

Recommended default:

- engineer and measure V1 for up to roughly 100,000 active symbols and 1-2
  million current trusted edges in one workspace;
- keep generation IDs capable of representing multiple repositories later;
- do not pay the complexity cost of million-symbol hosted workspaces now.

Question: should V1 instead target multi-repository workspaces from day one?

### Decision B: exact vector recovery

Recommended default:

- exact checksummed binary generation bundle is the normal durable recovery
  source;
- Helix stores references and metadata, not a mandatory duplicate float array.

Question: must exact vectors also survive entirely inside the graph database if
the local bundle is lost or the embedding model later becomes unavailable?

### Decision C: source history search

Recommended default:

- current code semantic search;
- complete versioned graph history;
- semantic search over historical memory annotations;
- no full historical code-vector index in V1.

Question: is "find old source implementation by meaning" a core initial use
case, or can it wait until current-code and rationale history are proven?

### Decision D: memory multiplicity

Recommended default:

- allow multiple typed annotations per function state;
- support review and supersession;
- never overwrite a single `memory` field.

Question: should candidate memories require explicit human/agent acceptance
before they influence default context results?

### Decision E: Helix local product

Open blocker:

- current image is proprietary `enterprise-dev:latest` and the observed Helix
  plus MinIO footprint is about 1.5 GiB before GraphDB and model memory.

Question: what local memory footprint is acceptable, and do we have a Helix
distribution path that can actually be shipped?

### Decision F: relationship objective

Recommended default:

- `connect_code` returns comparison mode by default;
- the agent may request shared-core or preserved-branch behavior explicitly;
- no hidden classifier chooses a universal alpha in V1.

Question: should the default return all alternatives, or should "balanced" be
the default with alternatives available on continuation?

### Decision G: language scope

Recommended default:

- make the product contract language-neutral;
- make the first production-quality resolver Python-specific because Dograh is
  the current real corpus;
- add language adapters only with compiler/LSP-backed evidence.

Question: which second language is required to prove that the architecture is
not accidentally Python-only?

### Decision H: Graph Lens priority

Recommended default:

- keep it as a dogfooding and trust surface;
- do not let extension work delay the daemon, generations, or MCP tools.

Question: is a human editor experience part of the first public product, or a
later consumer of the same stable runtime?

## 22. Current recommended default architecture

Unless the decision register changes it, the current recommendation is:

```text
Git history + current worktree
        |
incremental language-aware indexer
        |
versioned Helix graph with trusted edge provenance
        |
managed vector generations
  - exact binary bundles
  - immutable TurboVec base
  - mutable delta + suppressions + journal
        |
background analysis worker
  - Infomap / Leiden evaluation
  - betweenness
  - co-change promotion
  - GraphSAGE
        |
graphdbd query orchestrator
  - semantic anchors
  - bounded Helix traversals
  - bounded local optimizers
  - compact evidence packs
        |
standard MCP tools + optional VS Code Graph Lens
```

The central mental model is:

```text
semantic anchor
  -> trusted directed graph evidence
  -> historical and structural signals where appropriate
  -> bounded query-specific optimization
  -> compact context for the coding agent
```

That is a product. The algorithms remain replaceable strategies inside the
workflow, and every strategy must continue to justify its place with measured
agent outcomes.

## 23. Evidence index

This is a navigation index, not a substitute for rerunning benchmarks.

### Retrieval and algorithm roles

- `ARCHITECTURE_REPORT.md`: PPR failure mechanism, BFS comparison, MMR result,
  GraphSAGE structural role, product tool separation, and missing real-agent
  evaluation.
- `RETRIEVAL_ARCH_EXPERIMENTS.md`: experiment hypotheses, controls, result log,
  and the distinction between pre-anchor retrieval and post-anchor traversal.
- `PIPELINE_SPEC.md`: foundation failures, evaluation discipline, and the warning
  that algorithm metrics are not product-value metrics.
- `graphsage_minimal/README.md` and `graphsage_minimal/train_graphsage.py`:
  GraphSAGE implementation and held-out link-prediction metrics.
- `COMMIT_REVIEW_LOG.md`: betweenness observations and review-tool iterations.

### Multi-seed and call-resolution work

- `MULTISEED_CONNECTOR_LEDGER.md`: complete run-by-run problem definition,
  ablations, current-source overlay, exactness boundaries, shared-prefix cases,
  `J_alpha`, and remaining limitations.
- `sandbox/exp_multiseed_connectors.py`: structural distance-center baseline.
- `sandbox/exp_query_conditioned_connectors.py`: mode-conditioned root ranking,
  bounded paths, and fixed-root union optimization.
- `sandbox/mine_shared_prefix_union_cases.py`: adversarial real-path mining.
- `tests/test_current_source_calls.py`: conservative static call-resolution
  fixtures.
- Commit sequence `a1b226e` through `629d18b`: stable chronological record of
  the multi-seed work. The most important later commits are `0ca1f8d` for
  conservative static resolution, `dbf5235` for shared-prefix mining,
  `2067198` for the objective probe, and `4d1285b` for saved validation.

### Storage, vectors, and runtime

- `turbovec_adapter.py`: current persisted-index wrapper and incremental-update
  defect.
- `eval/vector_backend_benchmark.py`: backend benchmark procedure.
- `eval/report/vector_backend_benchmark_8q_steady_state.json`: Dograh semantic
  benchmark and warm latency results.
- `data/vectors/code_384_b4.manifest.json`: current 4,821-vector persisted code
  index metadata.
- `scalable_ingest.py`: current node/state schema, embedding writes, identity
  construction, code-hash computation, and simple-name call resolution.
- `sandbox/igraph_sandbox.py` and `helix_to_igraph.py`: current full-graph loading
  and mixed-artifact serving path that the product architecture replaces.
- `graph_payload.json`: current debug artifact; live inspection on 2026-08-06
  found zero nodes and 11,635 edges.

### Agent surface and evaluation

- `tools/graph_mcp_server.py`: current custom HTTP manifest with 14 public tools.
- `tools/graph_tools.py`: wrappers and Rust-side Helix traversal primitives.
- `pipeline_api.py`: current retrieval, review, memory, and annotation monolith.
- `eval/report/latest_report.txt`: scripted five-task comparison; useful harness
  evidence, not a valid token-reduction product claim.
- `vscode-graph-lens/README.md`: current optional human/editor surface.

### Live Docker observation on 2026-08-06

- `ghcr.io/helixdb/enterprise-dev:latest` was running on host port 6969.
- Its OCI license label was `LicenseRef-Proprietary`.
- `minio/minio:latest` was running as its storage companion.
- `docker stats --no-stream` reported approximately 954 MiB for Helix and
  574 MiB for MinIO at the time of inspection.
- The Helix container exposed no direct mount in `docker inspect`; MinIO used a
  named Docker volume.
- No product Dockerfile or Compose file existed in the `graphdb` repository
  root.

These observations can drift and must be rechecked before implementation or
distribution decisions.
