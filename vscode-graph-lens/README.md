# Dora Graph Lens

Inline knowledge-graph view for functions and classes, backed by the `graphdb` code-intelligence pipeline.

Hover a function/class name → a compact card shows graph stats (callers, structural siblings) plus a sub-menu of actions. Picking one opens a panel **docked beside your file** (the code never leaves the screen) showing the subgraph, callers, history, or docs for that symbol.

## Features

- **Hover sub-menu card** — `⇈ callers · ⇊ siblings` + one-line summary + action buttons.
- **Subgraph view** — force-directed graph (d3) centered on the focused symbol; click a neighbor to jump to its file.
- **Callers view** — blast-radius list (configurable depth), click to open.
- **History view** — semantic-memory timeline from `query_function_history`.
- **Docs view** — summary + node id for the symbol.
- **CodeLens** — `✦ function name — subgraph` line above every function/class.

## How it works

1. Cursor / hover position → innermost symbol via VS Code's document-symbol provider.
2. Symbol mapped to a graph node id:
   - tries the `lookup_symbol` tool, then
   - falls back to `search_code_semantics(name)` matching name + file, then
   - synthesizes `repo:path::name`.
3. The extension talks to the graphdb HTTP server on `:7700` (POST `/call`).

The extension auto-starts `python tools/graph_mcp_server.py` from a `graphdb/` folder inside the workspace if the port is unreachable.

## Development

```
npm install
npm run compile        # tsc -> out/
```

Press **F5** (launch.json included) to run the Extension Host.

## Settings

| Key | Default | Purpose |
|-----|---------|---------|
| `doraGraphLens.serverUrl` | `http://127.0.0.1:7700` | graphdb server base URL |
| `doraGraphLens.repoName` | `""` | repo name for node ids (auto = workspace folder name) |
| `doraGraphLens.repoRoot` | `""` | where the ingested repo lives inside the workspace, e.g. `dograh` (auto = workspace root) |
| `doraGraphLens.autoStartServer` | `true` | spawn `graph_mcp_server.py` if port unreachable |
| `doraGraphLens.graphdbDir` | `""` | absolute path to graphdb repo (auto-detected) |
| `doraGraphLens.hoverEnabled` | `true` | show hover card |
| `doraGraphLens.codeLensEnabled` | `true` | show CodeLens stats line |
| `doraGraphLens.defaultDepth` | `2` | blast-radius depth |

## Backend dependency

Requires the graphdb server running:

```
python tools/graph_mcp_server.py     # in the graphdb repo — serves :7700
```

Planned backend addition: a `lookup_symbol(file, name)` tool so symbol → node id is an exact lookup instead of a search fallback.
