import * as vscode from "vscode";
import { GraphClient } from "./backend/client";
import { SymbolInfo, ResolvedSymbol } from "./types";

const SYMBOL_KIND_LABEL: Record<number, string> = {
  [vscode.SymbolKind.Function]: "function",
  [vscode.SymbolKind.Method]: "method",
  [vscode.SymbolKind.Class]: "class",
  [vscode.SymbolKind.Constructor]: "constructor",
  [vscode.SymbolKind.Interface]: "interface",
  [vscode.SymbolKind.Struct]: "struct",
  [vscode.SymbolKind.Object]: "object",
  [vscode.SymbolKind.Module]: "module",
  [vscode.SymbolKind.Enum]: "enum",
  [vscode.SymbolKind.Namespace]: "namespace",
};

/**
 * Find the innermost named symbol (function/method/class/...) that contains the
 * given document position, using VS Code's built-in document symbol provider.
 * Returns null when the cursor is not inside a named symbol.
 */
export async function getSymbolAtPosition(
  document: vscode.TextDocument,
  position: vscode.Position
): Promise<SymbolInfo | null> {
  const symbols = (await vscode.commands.executeCommand<vscode.DocumentSymbol[]>(
    "vscode.executeDocumentSymbolProvider",
    document.uri
  )) ?? [];
  if (symbols.length === 0) return null;

  const wanted = new Set<number>([
    vscode.SymbolKind.Function,
    vscode.SymbolKind.Method,
    vscode.SymbolKind.Class,
    vscode.SymbolKind.Constructor,
    vscode.SymbolKind.Interface,
    vscode.SymbolKind.Struct,
    vscode.SymbolKind.Object,
    vscode.SymbolKind.Module,
    vscode.SymbolKind.Enum,
    vscode.SymbolKind.Namespace,
  ]);

  const walk = (symbols: vscode.DocumentSymbol[]): vscode.DocumentSymbol | null => {
    for (const s of symbols) {
      if (s.range.contains(position)) {
        const inner = walk(s.children ?? []);
        if (inner) return inner;
        if (wanted.has(s.kind)) return s;
      }
    }
    return null;
  };

  const found = walk(symbols);
  if (!found) return null;

  const kind = SYMBOL_KIND_LABEL[found.kind] ?? "symbol";
  return {
    name: found.name,
    kind,
    file: toForwardSlashes(vscode.workspace.asRelativePath(document.uri, false)),
    range: {
      startLine: found.range.start.line + 1,
      endLine: found.range.end.line + 1,
    },
  };
}

/**
 * Map a source symbol to a graph node id.
 *
 * Strategy:
 *  1. Try the dedicated lookup_symbol tool (added to graph_mcp_server later).
 *  2. Fall back to semantic search for the symbol name and pick the node that
 *     matches name + file. This gives us a real summary and community id.
 *  3. If no match, synthesize an id from repo + file + name so that callers
 *     (trace_blast_radius etc.) still have a deterministic identity.
 */
export async function resolveSymbol(
  client: GraphClient,
  symbol: SymbolInfo,
  repoName: string
): Promise<ResolvedSymbol> {
  const node = await client.lookupSymbol(symbol.file, symbol.name);

  let matched = false;
  let nodeId: string | undefined;
  let summary: string | undefined;

  if (node?.id) {
    nodeId = node.id;
    matched = true;
    summary = node.summary;
  } else {
    try {
      const sub = await client.searchSemantics(symbol.name, 30);
      const fallback = Array.isArray(sub.helix_fallback)
        ? (sub.helix_fallback as Array<{ function_id?: string; name?: string; file?: string }>)
        : [];
      const candidates = (sub.nodes ?? []).concat(
        fallback.map((h) => ({
          id: h.function_id ?? "",
          name: h.name ?? "",
          file: h.file ?? "",
        }))
      );
      const exact = candidates.find(
        (n) => n.name === symbol.name && fileMatches(n.file, symbol.file)
      );
      const nameOnly = candidates.find(
        (n) => n.name === symbol.name && fileBasenameMatches(n.file, symbol.file)
      );
      const best = exact ?? nameOnly;
      if (best) {
        nodeId = best.id;
        matched = true;
        summary = best.summary;
      }
    } catch {
      /* search failed — fall through to synthetic id */
    }
  }

  if (!nodeId) {
    nodeId = `${repoName}:${symbol.file}::${symbol.name}`;
  }

  return { symbol, nodeId, repoName, matched, summary };
}

/** Derive a repo name for node-id construction. */
export function deriveRepoName(override?: string): string {
  if (override && override.trim()) return override.trim();
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (folder) return folder.name;
  return "repo";
}

function fileMatches(gotFile: string, wantFile: string): boolean {
  const a = toForwardSlashes(gotFile);
  const b = toForwardSlashes(wantFile);
  return a === b || a.endsWith("/" + b) || b.endsWith("/" + a);
}

function fileBasenameMatches(gotFile: string, wantFile: string): boolean {
  const a = toForwardSlashes(gotFile).split("/").pop() ?? "";
  const b = toForwardSlashes(wantFile).split("/").pop() ?? "";
  return a === b;
}

function toForwardSlashes(p: string): string {
  return p.replace(/\\/g, "/");
}
