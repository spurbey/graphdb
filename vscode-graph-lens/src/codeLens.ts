import * as vscode from "vscode";
import { SymbolInfo } from "./types";

/**
 * CodeLens — a clickable line above each function/class that opens the
 * subgraph panel. Kept synchronous (no per-symbol network calls) so it never
 * stalls the editor; graph data is fetched when the panel opens.
 */
export class GraphCodeLensProvider implements vscode.CodeLensProvider {
  async provideCodeLenses(document: vscode.TextDocument): Promise<vscode.CodeLens[]> {
    if (!vscode.workspace.getConfiguration("doraGraphLens").get<boolean>("codeLensEnabled", true)) {
      return [];
    }
    const symbols = (await vscode.commands.executeCommand<vscode.DocumentSymbol[]>(
      "vscode.executeDocumentSymbolProvider",
      document.uri
    )) ?? [];
    if (symbols.length === 0) return [];

    const wanted = new Set<number>([
      vscode.SymbolKind.Function,
      vscode.SymbolKind.Method,
      vscode.SymbolKind.Class,
      vscode.SymbolKind.Constructor,
      vscode.SymbolKind.Interface,
      vscode.SymbolKind.Struct,
    ]);

    const lenses: vscode.CodeLens[] = [];
    const collect = (list: vscode.DocumentSymbol[]) => {
      for (const s of list) {
        if (wanted.has(s.kind)) {
          const symbol: SymbolInfo = {
            name: s.name,
            kind: labelOf(s.kind),
            file: vscode.workspace.asRelativePath(document.uri, false).replace(/\\/g, "/"),
            range: { startLine: s.range.start.line + 1, endLine: s.range.end.line + 1 },
          };
          lenses.push(
            new vscode.CodeLens(new vscode.Range(s.range.start, s.range.start), {
              title: `✦ ${symbol.kind} ${s.name} — subgraph`,
              command: "doraGraphLens.showSubgraph",
              tooltip: "Open graph subgraph for this symbol",
              arguments: [{ resolved: { symbol, repoName: "" } }],
            })
          );
        }
        collect(s.children ?? []);
      }
    };
    collect(symbols);
    return lenses;
  }

  resolveCodeLens?(codeLens: vscode.CodeLens, _token: vscode.CancellationToken): vscode.CodeLens {
    return codeLens;
  }
}

function labelOf(kind: vscode.SymbolKind): string {
  switch (kind) {
    case vscode.SymbolKind.Function:
      return "function";
    case vscode.SymbolKind.Method:
      return "method";
    case vscode.SymbolKind.Class:
      return "class";
    case vscode.SymbolKind.Constructor:
      return "constructor";
    case vscode.SymbolKind.Interface:
      return "interface";
    case vscode.SymbolKind.Struct:
      return "struct";
    default:
      return "symbol";
  }
}
