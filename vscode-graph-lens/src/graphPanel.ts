import * as vscode from "vscode";
import { GraphClient } from "./backend/client";
import { ResolvedSymbol, GraphRef } from "./types";

/**
 * The "Beside" panel. Docked in the same tab group as the current file, so the
 * code never leaves the screen. Renders subgraph (force-directed), callers,
 * history, or docs — switchable in-place from the panel header.
 */
export class GraphPanel {
  private static current: GraphPanel | undefined;
  private panel: vscode.WebviewPanel;
  private resolved: ResolvedSymbol;
  private busy = false;

  private constructor(
    private client: GraphClient,
    panel: vscode.WebviewPanel,
    resolved: ResolvedSymbol,
    mode: PanelMode
  ) {
    this.panel = panel;
    this.resolved = resolved;
    this.panel.webview.html = this.htmlFor(resolved);
    this.panel.webview.onDidReceiveMessage((msg) => void this.onMessage(msg));
    this.loadMode(mode);
  }

  static show(client: GraphClient, resolved: ResolvedSymbol, mode: PanelMode = "subgraph"): void {
    if (GraphPanel.current) {
      GraphPanel.current.setResolved(resolved);
      GraphPanel.current.panel.reveal(vscode.ViewColumn.Beside, true);
      GraphPanel.current.loadMode(mode);
      return;
    }
    const panel = vscode.window.createWebviewPanel(
      "doraGraphLens",
      `Graph: ${resolved.symbol.name}`,
      vscode.ViewColumn.Beside,
      { enableScripts: true, retainContextWhenHidden: true, localResourceRoots: [] }
    );
    panel.onDidDispose(() => {
      GraphPanel.current = undefined;
    });
    GraphPanel.current = new GraphPanel(client, panel, resolved, mode);
  }

  private setResolved(resolved: ResolvedSymbol): void {
    this.resolved = resolved;
    this.panel.title = `Graph: ${resolved.symbol.name}`;
  }

  // ── Data loading ───────────────────────────────────────────────────────────

  private async loadMode(mode: PanelMode): Promise<void> {
    if (this.busy) return;
    this.busy = true;
    try {
      this.post({ type: "status", text: `loading ${mode}…` });
      switch (mode) {
        case "subgraph":
          await this.loadSubgraph();
          break;
        case "callers":
          await this.loadCallers();
          break;
        case "history":
          await this.loadHistory();
          break;
        case "docs":
          await this.loadDocs();
          break;
      }
    } catch (err) {
      this.post({
        type: "error",
        text: err instanceof Error ? err.message : String(err),
      });
    } finally {
      this.busy = false;
    }
  }

  private async loadSubgraph(): Promise<void> {
    const { resolved, client } = this;
    const payload = await client.searchSemantics(
      `${resolved.symbol.name} in ${resolved.symbol.file}`,
      15
    );
    if (payload.error) throw new Error(payload.error);

    const fallback = Array.isArray(payload.helix_fallback)
      ? (payload.helix_fallback as Array<{ function_id?: string; name?: string; file?: string }>)
      : [];
    const nodes = payload.nodes ?? fallback.map((h) => ({
      id: h.function_id ?? "",
      name: h.name ?? "",
      file: h.file ?? "",
      summary: "",
    }));

    this.post({
      type: "subgraph",
      focus: {
        id: resolved.nodeId,
        name: resolved.symbol.name,
        file: resolved.symbol.file,
        summary: resolved.summary ?? "",
        inGraph: resolved.matched,
      },
      nodes,
      edges: payload.edges ?? [],
    });
  }

  private async loadCallers(): Promise<void> {
    const { resolved, client } = this;
    const depth = vscode.workspace.getConfiguration("doraGraphLens").get<number>("defaultDepth", 2);
    const callers = (await client.traceBlastRadius(resolved.nodeId, depth)).map(toRow);
    const siblings = (await client.findStructuralSiblings(resolved.nodeId, 8).catch(() => [])).map(toRow);
    this.post({ type: "list", mode: "callers", title: `Callers of ${resolved.symbol.name}`, rows: callers });
    if (siblings.length > 0) {
      this.post({ type: "list", mode: "siblings", title: `Structural siblings`, rows: siblings });
    }
  }

  private async loadHistory(): Promise<void> {
    const { resolved, client } = this;
    const history = await client.queryFunctionHistory(resolved.nodeId, resolved.symbol.name);
    const rows = Array.isArray(history)
      ? history.map((h) => ({
          id: String(h?.commit_sha ?? ""),
          name: String(h?.commit_sha ?? "").slice(0, 8),
          file: String(h?.edge_type ?? ""),
          summary: String(h?.memory ?? ""),
        }))
      : [];
    this.post({ type: "list", mode: "history", title: `History of ${resolved.symbol.name}`, rows });
  }

  private async loadDocs(): Promise<void> {
    const { resolved } = this;
    this.post({
      type: "docs",
      name: resolved.symbol.name,
      id: resolved.nodeId,
      file: resolved.symbol.file,
      summary: resolved.summary ?? "",
      inGraph: resolved.matched,
    });
  }

  // ── Messaging ──────────────────────────────────────────────────────────────

  private post(msg: unknown): void {
    void this.panel.webview.postMessage(msg);
  }

  private async onMessage(msg: any): Promise<void> {
    if (msg.type === "setMode") {
      await this.loadMode(msg.mode as PanelMode);
    } else if (msg.type === "openNode") {
      await revealFile(msg.name, msg.file);
    }
  }

  private htmlFor(resolved: ResolvedSymbol): string {
    const script = this.panel.webview.cspSource;
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'unsafe-inline'; font-src 'none'; img-src 'none' https: data:; connect-src ${script};" />
<title>Graph: ${esc(resolved.symbol.name)}</title>
<style>
${CSS}
</style>
</head>
<body>
  <header>
    <span id="mode">
      <button data-mode="subgraph" class="active">Subgraph</button>
      <button data-mode="callers">Callers</button>
      <button data-mode="history">History</button>
      <button data-mode="docs">Docs</button>
    </span>
    <span id="title"></span>
    <span id="status"></span>
  </header>
  <div id="focusbar"><span id="focusname"></span><span id="focusid"></span></div>
  <main id="main">
    <div id="graph-wrap" style="display:none"><svg id="svg"></svg></div>
    <div id="list-wrap" style="display:none"><div id="list"></div></div>
    <div id="docs-wrap" style="display:none"></div>
    <div id="empty">Select a view above, or hover a symbol in the editor and pick an action.</div>
  </main>
<script src="https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"></script>
<script>${SCRIPT}</script>
</body>
</html>`;
  }
}

export type PanelMode = "subgraph" | "callers" | "history" | "docs";

async function revealFile(name: string, file: string): Promise<void> {
  const uri = resolveRepoFile(file);
  if (!uri) {
    void vscode.window.showErrorMessage(`Graph node file not in workspace: ${file}`);
    return;
  }
  try {
    await vscode.workspace.fs.stat(uri);
  } catch {
    void vscode.window.showErrorMessage(`Graph node file not in workspace: ${file}`);
    return;
  }
  const doc = await vscode.workspace.openTextDocument(uri);
  const editor = await vscode.window.showTextDocument(doc, vscode.ViewColumn.Beside);
  const range = await findSymbolRange(doc, name);
  if (range) {
    editor.revealRange(range, vscode.TextEditorRevealType.InCenterIfOutsideViewport);
    const deco = vscode.window.createTextEditorDecorationType({
      backgroundColor: new vscode.ThemeColor("editor.findMatchHighlightBackground"),
      isWholeLine: true,
    });
    editor.setDecorations(deco, [range]);
    setTimeout(() => editor.setDecorations(deco, []), 2500);
  }
}

/**
 * Resolve a repo-relative file path to a real URI.
 *
 * The ingested repo may be the workspace root itself, or a subfolder (e.g.
 * `dograh/`). Try: workspaceRoot/repoRoot/file, workspaceRoot/repoName/file,
 * then workspaceRoot/file.
 */
function resolveRepoFile(file: string): vscode.Uri | undefined {
  const folders = vscode.workspace.workspaceFolders ?? [];
  if (folders.length === 0) return undefined;
  const repoName = vscode.workspace.getConfiguration("doraGraphLens").get<string>("repoName");
  const repoRoot = vscode.workspace.getConfiguration("doraGraphLens").get<string>("repoRoot") ?? "";
  for (const folder of folders) {
    const candidates: vscode.Uri[] = [];
    if (repoRoot.trim()) {
      candidates.push(vscode.Uri.joinPath(folder.uri, repoRoot, file));
    }
    if (repoName && repoName.trim()) {
      candidates.push(vscode.Uri.joinPath(folder.uri, repoName, file));
    }
    candidates.push(vscode.Uri.joinPath(folder.uri, file));
    for (const c of candidates) {
      try {
        if (require("fs").existsSync(c.fsPath)) return c;
      } catch {
        /* ignore */
      }
    }
  }
  return undefined;
}

function toRow(r: GraphRef): { id: string; name: string; file: string } {
  return { id: r.node_id ?? "", name: r.name ?? "", file: r.file ?? "" };
}

async function findSymbolRange(doc: vscode.TextDocument, name: string): Promise<vscode.Range | undefined> {
  const symbols = (await vscode.commands.executeCommand<vscode.DocumentSymbol[]>(
    "vscode.executeDocumentSymbolProvider",
    doc.uri
  )) ?? [];
  const walk = (list: vscode.DocumentSymbol[]): vscode.DocumentSymbol | undefined => {
    for (const s of list) {
      if (s.name === name) return s;
      const inner = walk(s.children ?? []);
      if (inner) return inner;
    }
    return undefined;
  };
  return walk(symbols)?.range;
}

function esc(s: string): string {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const CSS = String.raw`
  :root { color-scheme: dark; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; }
  body { font-family: ui-monospace, "Cascadia Code", "Segoe UI", monospace; background: #0d1117; color: #c9d1d9; display: flex; flex-direction: column; overflow: hidden; font-size: 12px; }
  header { flex: 0 0 auto; display: flex; align-items: center; gap: 10px; padding: 6px 10px; background: #161b22; border-bottom: 1px solid #30363d; }
  #mode { display: flex; gap: 4px; }
  button { background: #21262d; border: 1px solid #30363d; color: #c9d1d9; padding: 3px 8px; border-radius: 6px; font-size: 11px; cursor: pointer; }
  button:hover { background: #30363d; }
  button.active { background: #1f6feb; border-color: #388bfd; color: #fff; }
  #title { color: #58a6ff; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  #status { margin-left: auto; color: #8b949e; font-size: 11px; white-space: nowrap; }
  #focusbar { flex: 0 0 auto; display: flex; align-items: baseline; gap: 10px; padding: 4px 10px; background: #0d1117; border-bottom: 1px solid #21262d; }
  #focusname { color: #e6edf3; font-weight: 600; }
  #focusid { color: #6e7681; font-size: 10px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  main { flex: 1 1 0; display: flex; overflow: hidden; position: relative; }
  #graph-wrap { flex: 1; display: flex; }
  svg { width: 100%; height: 100%; }
  #list-wrap { flex: 1; overflow-y: auto; padding: 10px; }
  .row { display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-radius: 6px; cursor: pointer; }
  .row:hover { background: #21262d; }
  .row .dot { width: 8px; height: 8px; border-radius: 50%; background: #4493f8; flex-shrink: 0; }
  .row .name { color: #e6edf3; font-weight: 600; }
  .row .sub { color: #8b949e; font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row .sum { color: #c9d1d9; font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
  .listhead { color: #8b949e; text-transform: uppercase; letter-spacing: .06em; font-size: 10px; margin: 10px 0 6px; }
  #docs-wrap { flex: 1; overflow-y: auto; padding: 12px; }
  #docs-wrap .k { color: #8b949e; font-size: 10px; text-transform: uppercase; letter-spacing: .06em; margin: 12px 0 4px; }
  #docs-wrap .v { color: #c9d1d9; white-space: pre-wrap; }
  #empty { margin: auto; color: #484f58; text-align: center; line-height: 1.6; }
  .badge { display: inline-block; padding: 1px 6px; border-radius: 10px; font-size: 10px; }
  .badge.ok { background: #1a7f37; color: #fff; }
  .badge.ghost { background: #6e7681; color: #fff; }
`;

const SCRIPT = String.raw`
  const vscode = acquireVsCodeApi();
  const modeButtons = document.querySelectorAll("#mode button");
  let state = { mode: "subgraph" };

  modeButtons.forEach(b => b.addEventListener("click", () => {
    state.mode = b.dataset.mode;
    modeButtons.forEach(x => x.classList.toggle("active", x === b));
    vscode.postMessage({ type: "setMode", mode: state.mode });
  }));

  window.addEventListener("message", ev => {
    const m = ev.data;
    if (m.type === "status") setStatus(m.text);
    else if (m.type === "error") { setStatus("⚠ " + m.text); showEmpty(m.text); }
    else if (m.type === "subgraph") renderSubgraph(m);
    else if (m.type === "list") renderList(m);
    else if (m.type === "docs") renderDocs(m);
  });

  function setStatus(t) { document.getElementById("status").textContent = t; }
  function show(id) {
    ["graph-wrap","list-wrap","docs-wrap","empty"].forEach(x =>
      document.getElementById(x).style.display = (x === id) ? "flex" : "none");
    if (id === "empty") document.getElementById("empty").style.display = "block";
  }
  function showEmpty(text) {
    show("empty");
    document.getElementById("empty").textContent = text;
  }

  // ── Subgraph ──
  function renderSubgraph(m) {
    show("graph-wrap");
    setStatus(m.nodes.length + " nodes · " + m.edges.length + " edges");
    setFocus(m.focus);
    const nodes = m.nodes.map(n => ({ id: n.id, name: n.name, file: n.file, summary: n.summary || "", kind: n.community_id }));
    const focusPresent = nodes.find(n => n.id === m.focus.id);
    if (!focusPresent) {
      nodes.push({ id: m.focus.id, name: m.focus.name, file: m.focus.file, summary: m.focus.summary || "", isFocus: true });
    }
    const edges = m.edges.map(e => ({ source: e.source, target: e.target, type: e.type, w: (e.co_change_count || 1) }));
    drawForce(nodes, edges, m.focus.id);
  }

  function drawForce(nodes, edges, focusId) {
    const el = document.getElementById("svg");
    el.innerHTML = "";
    const w = el.clientWidth, h = el.clientHeight;
    const svg = d3.select(el);
    const color = d => d.id === focusId ? "#e3b341" : "#4493f8";
    const r = d => d.id === focusId ? 9 : 6;
    const sim = d3.forceSimulation(nodes)
      .force("link", d3.forceLink(edges).id(d => d.id).distance(90).strength(0.4))
      .force("charge", d3.forceManyBody().strength(-220))
      .force("center", d3.forceCenter(w/2, h/2))
      .force("collision", d3.forceCollide().radius(12));
    const link = svg.append("g").selectAll("line").data(edges).enter().append("line")
      .attr("stroke", d => d.type === "CO_CHANGE" ? "#f0883e" : "#a371f7")
      .attr("stroke-opacity", 0.5)
      .attr("stroke-width", d => Math.max(1, d.w));
    const node = svg.append("g").selectAll("circle").data(nodes).enter().append("circle")
      .attr("r", r).attr("fill", color).attr("stroke", "#fff").attr("stroke-width", d => d.id === focusId ? 2 : 0.5)
      .style("cursor", "pointer")
      .append("title").text(d => d.name + "\n" + d.file + (d.summary ? "\n\n" + d.summary : ""));
    node.on("click", function(ev, d) {
      if (d.id !== focusId) vscode.postMessage({ type: "openNode", id: d.id, name: d.name, file: d.file });
    });
    const label = svg.append("g").selectAll("text").data(nodes).enter().append("text")
      .text(d => d.name.length > 22 ? d.name.slice(0, 21) + "…" : d.name)
      .attr("font-size", 9).attr("fill", "#8b949e").attr("dy", d => r(d) + 11)
      .attr("text-anchor", "middle").style("pointer-events", "none");
    sim.on("tick", () => {
      link.attr("x1", d => d.source.x).attr("y1", d => d.source.y)
          .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
      node.attr("cx", d => d.x).attr("cy", d => d.y);
      label.attr("x", d => d.x).attr("y", d => d.y);
    });
  }

  // ── List views ──
  function renderList(m) {
    show("list-wrap");
    setStatus(m.rows.length + " entries");
    const el = document.getElementById("list");
    el.innerHTML = "";
    const head = document.createElement("div");
    head.className = "listhead";
    head.textContent = m.title;
    el.appendChild(head);
    if (!m.rows.length) { el.innerHTML += '<div style="color:#484f58;padding:8px">No results.</div>'; return; }
    m.rows.forEach(row => {
      const div = document.createElement("div");
      div.className = "row";
      div.innerHTML = '<span class="dot"></span><span class="name"></span><span class="sub"></span><span class="sum"></span>';
      div.querySelector(".name").textContent = row.name;
      div.querySelector(".sub").textContent = row.file;
      div.querySelector(".sum").textContent = row.summary || "";
      div.addEventListener("click", () => {
        if (row.id && row.file) vscode.postMessage({ type: "openNode", id: row.id, name: row.name, file: row.file });
      });
      el.appendChild(div);
    });
  }

  // ── Docs ──
  function renderDocs(m) {
    show("docs-wrap");
    setStatus("docs");
    const el = document.getElementById("docs-wrap");
    el.innerHTML = "";
    const row = (k, v) => '<div class="k">' + k + "</div><div class=\"v\">" + esc(v) + "</div>";
    el.innerHTML = row("symbol", m.name + "  <span class=\"badge " + (m.inGraph ? "ok" : "ghost") + "\">" + (m.inGraph ? "in graph" : "heuristic id") + "</span>");
    el.innerHTML += row("file", m.file);
    el.innerHTML += row("node id", m.id);
    if (m.summary) el.innerHTML += row("summary", m.summary);
  }

  function setFocus(f) {
    document.getElementById("focusname").textContent = f.name;
    document.getElementById("focusid").textContent = f.id + (f.inGraph ? "" : "  (not in graph)");
  }

  function esc(s) { return String(s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }
`;
