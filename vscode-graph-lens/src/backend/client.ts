import * as vscode from "vscode";
import { SubgraphPayload, GraphNode, GraphRef } from "../types";

/**
 * Thin HTTP client for the graphdb MCP server (tools/graph_mcp_server.py).
 *
 * The server exposes a plain-JSON HTTP API:
 *   GET  /manifest  -> tool manifest
 *   POST /call      -> {name, parameters} -> {result} | {error}
 *
 * If the server is unreachable and autoStart is enabled, the client tries to
 * spawn `python tools/graph_mcp_server.py` from the detected graphdb dir.
 */
export class GraphClient {
  private baseUrl: string;
  private autoStart: boolean;
  private graphdbDir: string | undefined;
  private serverProc: ChildProcessLike | undefined;
  private ready: boolean | undefined;

  constructor() {
    const cfg = vscode.workspace.getConfiguration("doraGraphLens");
    this.baseUrl = (cfg.get<string>("serverUrl") ?? "http://127.0.0.1:7700").replace(/\/+$/, "");
    this.autoStart = cfg.get<boolean>("autoStartServer") ?? true;
    this.graphdbDir = cfg.get<string>("graphdbDir") || this.detectGraphdbDir();
  }

  private detectGraphdbDir(): string | undefined {
    const workspaceFolders = vscode.workspace.workspaceFolders;
    if (!workspaceFolders) return undefined;
    for (const folder of workspaceFolders) {
      const candidate = vscode.Uri.joinPath(folder.uri, "graphdb");
      if (require("fs").existsSync(candidate.fsPath)) {
        return candidate.fsPath;
      }
    }
    return undefined;
  }

  /** Verify the server is reachable; optionally spawn it. Returns true when ready. */
  async ensureReady(): Promise<boolean> {
    if (this.ready !== undefined) return this.ready;
    this.ready = await this.ping();
    if (!this.ready && this.autoStart && this.graphdbDir) {
      await this.spawnServer();
      this.ready = await this.pingWithRetry(10, 300);
    }
    return this.ready;
  }

  private async ping(): Promise<boolean> {
    try {
      const res = await fetch(`${this.baseUrl}/manifest`, { signal: AbortSignal.timeout(1500) });
      return res.ok;
    } catch {
      return false;
    }
  }

  private async pingWithRetry(times: number, delayMs: number): Promise<boolean> {
    for (let i = 0; i < times; i++) {
      if (await this.ping()) return true;
      await new Promise((r) => setTimeout(r, delayMs));
    }
    return false;
  }

  private async spawnServer(): Promise<void> {
    const serverPath = require("path").join(this.graphdbDir, "tools", "graph_mcp_server.py");
    if (!require("fs").existsSync(serverPath)) return;
    const { spawn } = require("child_process") as typeof import("child_process");
    const python = process.platform === "win32" ? "python" : "python3";
    this.serverProc = spawn(python, [serverPath], {
      cwd: this.graphdbDir,
      detached: false,
      stdio: ["ignore", "pipe", "pipe"],
    }) as ChildProcessLike;
    this.serverProc.stdout?.on("data", () => undefined);
    this.serverProc.stderr?.on("data", () => undefined);
    this.serverProc.on?.("error", () => undefined);
  }

  dispose(): void {
    // Never kill a server we didn't spawn is handled by caller; we only kill if spawned.
    if (this.serverProc) {
      try {
        this.serverProc.kill?.();
      } catch {
        /* ignore */
      }
    }
  }

  /** Generic tool call. Returns the decoded JSON body. */
  async call<T>(name: string, parameters: Record<string, unknown>): Promise<T> {
    const res = await fetch(`${this.baseUrl}/call`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, parameters }),
      signal: AbortSignal.timeout(30000),
    });
    const body = (await res.json()) as { error?: unknown; result?: T };
    if (!res.ok) {
      throw new Error(typeof body.error === "string" ? body.error : `graphdb tool ${name} failed (HTTP ${res.status})`);
    }
    if (body.error !== undefined) {
      throw new Error(String(body.error));
    }
    return body.result as T;
  }

  // ── Tool wrappers ──────────────────────────────────────────────────────────

  async status(): Promise<Record<string, unknown>> {
    return this.call<Record<string, unknown>>("pipeline_status", {});
  }

  async searchSemantics(prompt: string, k = 15, mode = "general_retrieval"): Promise<SubgraphPayload> {
    return this.call<SubgraphPayload>("search_code_semantics", { prompt, k, mode });
  }

  async traceBlastRadius(functionId: string, depth = 3): Promise<GraphRef[]> {
    return this.call<GraphRef[]>("trace_blast_radius", { function_identity_id: functionId, depth });
  }

  async findStructuralSiblings(functionId: string, k = 8): Promise<GraphRef[]> {
    return this.call<GraphRef[]>("find_structural_siblings", { func_id: functionId, k });
  }

  async queryFunctionHistory(functionId: string, topic = ""): Promise<Record<string, unknown>[]> {
    return this.call<Record<string, unknown>[]>("query_function_history", { func_id: functionId, topic });
  }

  async lookupSymbol(file: string, name: string): Promise<GraphNode | undefined> {
    try {
      return await this.call<GraphNode>("lookup_symbol", { file, name });
    } catch {
      // lookup_symbol is not implemented on older servers — caller falls back to search.
      return undefined;
    }
  }
}

interface ChildProcessLike {
  stdout?: { on(event: string, cb: () => void): unknown };
  stderr?: { on(event: string, cb: () => void): unknown };
  kill?(): void;
  on?(event: string, cb: () => void): unknown;
}
