import * as vscode from "vscode";
import { GraphClient } from "./backend/client";
import { getSymbolAtPosition, resolveSymbol, deriveRepoName } from "./symbols";
import { ResolvedSymbol } from "./types";

const cache = new Map<string, CachedStats>();

interface CachedStats {
  callers: number;
  siblings: number;
  at: number;
}

const CACHE_TTL_MS = 30_000;

/** Clears the hover stats cache (exposed for the refreshCache command). */
export function clearHoverCache(): void {
  cache.clear();
}

/** Builds a command URI that passes an argument to a command handler. */
export function commandUri(command: string, arg: unknown): string {
  return vscode.Uri.parse(
    `command:${command}?${encodeURIComponent(JSON.stringify(arg))}`
  ).toString();
}

/**
 * Hover provider — the inline sub-menu card.
 *
 * Hovering a function/class name renders a compact card with graph stats and
 * action buttons. Clicking a button opens the corresponding view in a panel
 * docked beside the current file (the file never leaves the screen).
 */
export class GraphHoverProvider implements vscode.HoverProvider {
  constructor(private client: GraphClient) {}

  async provideHover(
    document: vscode.TextDocument,
    position: vscode.Position
  ): Promise<vscode.Hover | undefined> {
    if (!vscode.workspace.getConfiguration("doraGraphLens").get<boolean>("hoverEnabled", true)) {
      return undefined;
    }
    if (position.character === 0 && position.line === 0) return undefined;

    const symbol = await getSymbolAtPosition(document, position);
    if (!symbol) return undefined;

    if (!(await this.client.ensureReady())) {
      return this.degradedHover(symbol);
    }

    const repoName = deriveRepoName(
      vscode.workspace.getConfiguration("doraGraphLens").get<string>("repoName")
    );
    const resolved = await resolveSymbol(this.client, symbol, repoName);
    const stats = await this.fetchStats(resolved);

    const md = new vscode.MarkdownString(undefined, true);
    md.isTrusted = true;
    md.supportHtml = true;
    md.appendMarkdown(this.renderCard(resolved, stats));
    return new vscode.Hover(md, symbol.range ? rangeFor(symbol) : undefined);
  }

  private async fetchStats(resolved: ResolvedSymbol): Promise<CachedStats> {
    const key = `${resolved.symbol.file}:${resolved.symbol.name}`;
    const hit = cache.get(key);
    if (hit && Date.now() - hit.at < CACHE_TTL_MS) return hit;

    let callers = 0;
    let siblings = 0;
    try {
      const callersList = await this.client.traceBlastRadius(resolved.nodeId, 1);
      callers = Array.isArray(callersList) ? callersList.length : 0;
    } catch {
      /* caller trace unavailable */
    }
    try {
      const siblingsList = await this.client.findStructuralSiblings(resolved.nodeId, 8);
      siblings = Array.isArray(siblingsList) ? siblingsList.length : 0;
    } catch {
      /* siblings unavailable */
    }

    const stats: CachedStats = { callers, siblings, at: Date.now() };
    cache.set(key, stats);
    return stats;
  }

  private renderCard(resolved: ResolvedSymbol, stats: CachedStats): string {
    const sym = resolved.symbol;
    const matched = resolved.matched;

    const kindBadge = `<span style="color:#7ee787">${sym.kind}</span>`;
    const name = `<span style="color:#58a6ff;font-weight:600">${escapeHtml(sym.name)}</span>`;
    const id = `<span style="color:#6e7681;font-size:10px">${escapeHtml(resolved.nodeId)}</span>`;

    const statsLine = [
      `⇈ <b>${stats.callers}</b> caller${stats.callers === 1 ? "" : "s"}`,
      `⇊ <b>${stats.siblings}</b> sibling${stats.siblings === 1 ? "" : "s"}`,
    ].join(" &nbsp;·&nbsp; ");

    const status = matched
      ? "in graph"
      : '<span style="color:#e3b341">not in graph — heuristic id</span>';

    const summary = resolved.summary
      ? `<div style="margin-top:4px;color:#c9d1d9;font-size:12px">${escapeHtml(
          truncate(resolved.summary, 220)
        )}</div>`
      : "";

    const arg = JSON.stringify({ resolved });
    const btn = (title: string, cmd: string) =>
      `<a style="margin-right:6px" href="${commandUri(cmd, arg)}">${title}</a>`;

    const buttons = [
      btn("⟢ Subgraph", "doraGraphLens.showSubgraph"),
      btn("⇈ Callers", "doraGraphLens.showCallers"),
      btn("⌛ History", "doraGraphLens.showHistory"),
      btn("▤ Docs", "doraGraphLens.showDocs"),
    ].join("");

    return [
      `### ${kindBadge} ${name}`,
      `<div style="color:#8b949e;font-size:11px">${escapeHtml(sym.file)}:${sym.range.startLine} · ${status}</div>`,
      summary,
      `<div style="margin-top:4px">${statsLine}</div>`,
      `<div style="margin-top:6px">${buttons}</div>`,
      `<div style="margin-top:4px">${id}</div>`,
    ].join("\n\n");
  }

  private degradedHover(symbol: { name: string }): vscode.Hover {
    const md = new vscode.MarkdownString(undefined, true);
    md.appendMarkdown(
      `**${escapeHtml(symbol.name)}** — <span style="color:#e3b341">graph server unreachable.</span>\n\n` +
        `Start it with \`python tools/graph_mcp_server.py\` in the graphdb repo.`
    );
    return new vscode.Hover(md);
  }
}

function rangeFor(symbol: { range: { startLine: number; endLine: number } }): vscode.Range {
  return new vscode.Range(
    new vscode.Position(symbol.range.startLine - 1, 0),
    new vscode.Position(symbol.range.endLine, 0)
  );
}

function escapeHtml(s: string): string {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function truncate(s: string, n: number): string {
  return s && s.length > n ? s.slice(0, n) + "…" : s;
}
