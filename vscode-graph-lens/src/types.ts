// Shared types mirroring the graphdb backend payloads
// (see sandbox/igraph_sandbox.py build_subgraph_output and tools/graph_tools.py).

/** A single graph node returned by the pipeline. */
export interface GraphNode {
  id: string; // e.g. "src/agent_memory_orchestrator/memory/ingest.py::ingest_hook_payload"
  name: string;
  file: string;
  summary?: string;
  code?: string;
  ppr_score?: number | null;
  vector_score?: number | null;
  community_id?: number | null;
}

/** An edge between two nodes in a returned subgraph. */
export interface GraphEdge {
  source: string; // node id
  target: string; // node id
  type: string; // CALLS | IMPORTS | CO_CHANGE | ...
  co_change_category?: string;
  co_change_count?: number;
}

/** Top-level payload of search_code_semantics. */
export interface SubgraphPayload {
  pipeline_mode?: string;
  query?: string;
  consumer_mode?: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  error?: string;
  helix_fallback?: unknown;
}

/** Blast-radius / sibling result rows. */
export interface GraphRef {
  node_id: string;
  name: string;
  file: string;
}

/** The symbol the user hovered / clicked. */
export interface SymbolInfo {
  name: string;
  kind: string; // "function" | "method" | "class" | ...
  file: string; // workspace-relative path, forward slashes
  range: { startLine: number; endLine: number };
}

/** Resolved graph identity for a symbol. */
export interface ResolvedSymbol {
  symbol: SymbolInfo;
  nodeId: string; // graph node id
  repoName: string;
  matched: boolean; // true if found in graph, false if heuristic/synthetic
  summary?: string;
}
