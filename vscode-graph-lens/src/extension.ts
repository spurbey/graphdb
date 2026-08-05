import * as vscode from "vscode";
import { GraphClient } from "./backend/client";
import { GraphHoverProvider, clearHoverCache } from "./hover";
import { GraphCodeLensProvider } from "./codeLens";
import { GraphPanel, PanelMode } from "./graphPanel";
import { getSymbolAtPosition, resolveSymbol, deriveRepoName } from "./symbols";
import { ResolvedSymbol } from "./types";

const HOVER_LANGUAGES = [
  "python",
  "javascript",
  "typescript",
  "javascriptreact",
  "typescriptreact",
  "java",
  "go",
  "rust",
  "ruby",
  "php",
  "c",
  "cpp",
  "csharp",
  "dart",
  "kotlin",
  "swift",
  "scala",
  "haskell",
];

export function activate(context: vscode.ExtensionContext): void {
  const client = new GraphClient();
  context.subscriptions.push(client);

  // Hover sub-menu card.
  const hoverProvider = new GraphHoverProvider(client);
  context.subscriptions.push(
    vscode.languages.registerHoverProvider(HOVER_LANGUAGES, hoverProvider)
  );

  // CodeLens stats line.
  const codeLensProvider = new GraphCodeLensProvider();
  const codeLensDisposable = vscode.languages.registerCodeLensProvider(
    HOVER_LANGUAGES,
    codeLensProvider
  );
  context.subscriptions.push(codeLensDisposable);

  // ── Commands ───────────────────────────────────────────────────────────────

  const show = (mode: PanelMode) => async (arg?: unknown): Promise<void> => {
    const resolved = await resolveFromArgOrCursor(client, arg);
    if (!resolved) return;
    GraphPanel.show(client, resolved, mode);
  };

  context.subscriptions.push(
    vscode.commands.registerCommand("doraGraphLens.showSubgraph", show("subgraph")),
    vscode.commands.registerCommand("doraGraphLens.showCallers", show("callers")),
    vscode.commands.registerCommand("doraGraphLens.showHistory", show("history")),
    vscode.commands.registerCommand("doraGraphLens.showDocs", show("docs"))
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("doraGraphLens.status", async () => {
      const ready = await client.ensureReady();
      if (!ready) {
        void vscode.window.showWarningMessage(
          "GraphDB server unreachable. Start it: python tools/graph_mcp_server.py (in graphdb/)"
        );
        return;
      }
      const status = await client.status();
      void vscode.window.showInformationMessage(
        `GraphDB pipeline: ${String(status?.pipeline ?? "unknown")} · ${String(status?.error ?? "")}`.trim()
      );
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("doraGraphLens.refreshCache", () => {
      clearHoverCache();
      void vscode.window.showInformationMessage("Graph Lens hover cache cleared.");
    })
  );
}

/**
 * Commands can be invoked with a `{ resolved }` argument (from the hover card /
 * CodeLens) or without one (command palette / context menu) — in which case we
 * resolve the symbol under the active editor cursor.
 */
async function resolveFromArgOrCursor(
  client: GraphClient,
  arg?: unknown
): Promise<ResolvedSymbol | undefined> {
  if (arg && typeof arg === "object" && "resolved" in (arg as object)) {
    const maybe = (arg as { resolved: ResolvedSymbol }).resolved;
    if (maybe && maybe.symbol && maybe.nodeId) return maybe;
  }

  const editor = vscode.window.activeTextEditor;
  if (!editor) return undefined;
  const symbol = await getSymbolAtPosition(editor.document, editor.selection.active);
  if (!symbol) {
    void vscode.window.setStatusBarMessage("Graph Lens: cursor is not inside a function or class", 3000);
    return undefined;
  }
  if (!(await client.ensureReady())) {
    void vscode.window.showWarningMessage("GraphDB server unreachable.");
    return undefined;
  }
  const repoName = deriveRepoName(
    vscode.workspace.getConfiguration("doraGraphLens").get<string>("repoName")
  );
  return resolveSymbol(client, symbol, repoName);
}

export function deactivate(): void {
  // client.dispose() runs via context.subscriptions.
}
