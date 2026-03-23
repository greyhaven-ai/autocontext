/**
 * Interactive WebSocket server for the TS control plane (AC-347 Task 25).
 */

import { createServer, type IncomingMessage, type Server as HttpServer, type ServerResponse } from "node:http";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, extname, join, normalize } from "node:path";
import { WebSocketServer, WebSocket } from "ws";
import type { AddressInfo } from "node:net";
import { fileURLToPath } from "node:url";
import { parseClientMessage } from "./protocol.js";
import type { ClientMessage, ServerMessage } from "./protocol.js";
import { RunManager } from "./run-manager.js";
import type { RunManagerState } from "./run-manager.js";
import type { EventCallback } from "../loop/events.js";
import { SQLiteStore } from "../storage/index.js";
import { ArtifactStore } from "../knowledge/artifact-store.js";

export interface InteractiveServerOpts {
  runManager: RunManager;
  port?: number;
  host?: string;
}

export class InteractiveServer {
  private readonly runManager: RunManager;
  private readonly host: string;
  private readonly requestedPort: number;
  private httpServer: HttpServer | null = null;
  private wsServer: WebSocketServer | null = null;
  private boundPort = 0;

  constructor(opts: InteractiveServerOpts) {
    this.runManager = opts.runManager;
    this.host = opts.host ?? "127.0.0.1";
    this.requestedPort = opts.port ?? 8000;
  }

  get port(): number {
    return this.boundPort;
  }

  get url(): string {
    return `ws://localhost:${this.boundPort}/ws/interactive`;
  }

  async start(): Promise<number> {
    if (this.httpServer) {
      return this.boundPort;
    }

    const httpServer = createServer((req, res) => {
      this.handleHttpRequest(req, res);
    });

    const wsServer = new WebSocketServer({ noServer: true });
    httpServer.on("upgrade", (req, socket, head) => {
      if (req.url === "/ws/interactive") {
        wsServer.handleUpgrade(req, socket, head, (ws: WebSocket) => {
          this.attachClient(ws);
        });
        return;
      }
      if (req.url === "/ws/events") {
        wsServer.handleUpgrade(req, socket, head, (ws: WebSocket) => {
          this.attachEventStreamClient(ws);
        });
        return;
      }
      {
        socket.write("HTTP/1.1 404 Not Found\r\n\r\n");
        socket.destroy();
      }
    });

    await new Promise<void>((resolve, reject) => {
      httpServer.once("error", reject);
      httpServer.listen(this.requestedPort, this.host, () => {
        resolve();
      });
    });

    this.httpServer = httpServer;
    this.wsServer = wsServer;
    this.boundPort = (httpServer.address() as AddressInfo).port;
    return this.boundPort;
  }

  // ---------------------------------------------------------------------------
  // HTTP REST API (AC-364)
  // ---------------------------------------------------------------------------

  private handleHttpRequest(req: IncomingMessage, res: ServerResponse): void {
    const url = req.url ?? "";
    const method = req.method ?? "GET";

    // CORS headers for dashboard
    res.setHeader("Access-Control-Allow-Origin", "*");
    res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type");

    if (method === "OPTIONS") {
      res.writeHead(204);
      res.end();
      return;
    }

    const json = (status: number, body: unknown) => {
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify(body, null, 2));
    };

    if (method === "GET") {
      const served = this.tryServeDashboard(url, res);
      if (served) {
        return;
      }
    }

    // Health
    if (url === "/health") {
      json(200, { ok: true });
      return;
    }

    // GET /api/runs
    if (url === "/api/runs" || url.startsWith("/api/runs?")) {
      this.withStore((store) => {
        json(200, store.listRuns());
      });
      return;
    }

    // GET /api/runs/:id/replay/:gen
    const replayMatch = url.match(/^\/api\/runs\/([^/]+)\/replay\/(\d+)$/);
    if (replayMatch) {
      const [, runId, genStr] = replayMatch;
      const gen = parseInt(genStr!, 10);
      const replayDir = join(
        this.runManager["opts"].runsRoot,
        runId!,
        "generations",
        `gen_${gen}`,
        "replays",
      );
      if (!existsSync(replayDir)) {
        json(404, { error: `No replay files found under ${replayDir}` });
        return;
      }
      const replayFiles = readdirSync(replayDir)
        .filter((name) => name.endsWith(".json"))
        .sort();
      if (replayFiles.length === 0) {
        json(404, { error: `No replay files found under ${replayDir}` });
        return;
      }
      const payload = JSON.parse(readFileSync(join(replayDir, replayFiles[0]), "utf-8"));
      if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
        json(500, { error: "Replay payload is not a JSON object" });
        return;
      }
      json(200, payload);
      return;
    }

    // GET /api/runs/:id/status
    const statusMatch = url.match(/^\/api\/runs\/([^/]+)\/status$/);
    if (statusMatch) {
      const [, runId] = statusMatch;
      this.withStore((store) => {
        const run = store.getRun(runId!);
        if (!run) {
          json(404, { error: `Run '${runId}' not found` });
          return;
        }
        json(200, store.getGenerations(runId!));
      });
      return;
    }

    // GET /api/knowledge/playbook/:scenario
    const playbookMatch = url.match(/^\/api\/knowledge\/playbook\/([^/]+)$/);
    if (playbookMatch) {
      const [, scenario] = playbookMatch;
      const artifacts = new ArtifactStore({
        runsRoot: this.runManager["opts"].runsRoot,
        knowledgeRoot: this.runManager["opts"].knowledgeRoot,
      });
      json(200, { scenario, content: artifacts.readPlaybook(scenario!) });
      return;
    }

    // GET /api/scenarios
    if (url === "/api/scenarios") {
      json(200, this.runManager.getEnvironmentInfo().scenarios);
      return;
    }

    // 404 fallback
    json(404, { error: "Not found" });
  }

  private tryServeDashboard(url: string, res: ServerResponse): boolean {
    const dashboardDir = this.dashboardDir();
    if (!existsSync(dashboardDir)) {
      return false;
    }

    const sendFile = (path: string): boolean => {
      if (!existsSync(path)) {
        return false;
      }
      const ext = extname(path).toLowerCase();
      const contentType = ({
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml",
      } as Record<string, string>)[ext] ?? "application/octet-stream";
      res.writeHead(200, { "Content-Type": contentType });
      res.end(readFileSync(path));
      return true;
    };

    if (url === "/" || url === "/dashboard" || url === "/dashboard/" || url === "/dashboard/index.html") {
      return sendFile(join(dashboardDir, "index.html"));
    }

    if (!url.startsWith("/dashboard/")) {
      return false;
    }

    const relativePath = normalize(url.slice("/dashboard/".length)).replace(/^(\.\.(\/|\\|$))+/, "");
    if (!relativePath || relativePath.startsWith("..")) {
      return false;
    }
    return sendFile(join(dashboardDir, relativePath));
  }

  private dashboardDir(): string {
    return join(
      dirname(fileURLToPath(import.meta.url)),
      "..",
      "..",
      "..",
      "autocontext",
      "dashboard",
    );
  }

  private withStore(fn: (store: SQLiteStore) => void): void {
    const store = new SQLiteStore(this.runManager["opts"].dbPath);
    store.migrate(this.runManager["opts"].migrationsDir);
    try {
      fn(store);
    } finally {
      store.close();
    }
  }

  async stop(): Promise<void> {
    const wsServer = this.wsServer;
    const httpServer = this.httpServer;
    this.wsServer = null;
    this.httpServer = null;
    this.boundPort = 0;

    if (wsServer) {
      for (const client of wsServer.clients) {
        try {
          client.terminate();
        } catch {
          // Best-effort shutdown for interactive clients.
        }
      }
      await new Promise<void>((resolve) => {
        wsServer.close(() => resolve());
      });
    }

    if (httpServer) {
      await new Promise<void>((resolve, reject) => {
        httpServer.close((err) => {
          if (err) {
            reject(err);
            return;
          }
          resolve();
        });
      });
    }
  }

  private attachClient(ws: WebSocket): void {
    const env = this.runManager.getEnvironmentInfo();
    const eventCallback: EventCallback = (event, payload) => {
      this.send(ws, { type: "event", event, payload });
    };
    const stateCallback = (state: RunManagerState) => {
      this.sendState(ws, state);
    };

    this.runManager.subscribeEvents(eventCallback);
    this.runManager.subscribeState(stateCallback);

    this.send(ws, { type: "hello", protocol_version: 1 });
    this.send(ws, {
      type: "environments",
      scenarios: env.scenarios,
      executors: env.executors,
      current_executor: env.currentExecutor,
      agent_provider: env.agentProvider,
    });
    this.sendState(ws, this.runManager.getState());

    ws.on("message", async (data: WebSocket.RawData) => {
      let parsedMessage: ClientMessage | null = null;
      try {
        parsedMessage = this.parseMessage(data.toString());
        await this.handleClientMessage(ws, parsedMessage);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        if (
          parsedMessage
          && (
            parsedMessage.type === "create_scenario"
            || parsedMessage.type === "confirm_scenario"
            || parsedMessage.type === "revise_scenario"
            || parsedMessage.type === "cancel_scenario"
          )
        ) {
          this.send(ws, {
            type: "scenario_error",
            message,
            stage: "server",
          });
        } else {
          this.send(ws, {
            type: "error",
            message,
          });
        }
      }
    });

    ws.on("close", () => {
      this.runManager.unsubscribeEvents(eventCallback);
      this.runManager.unsubscribeState(stateCallback);
    });
  }

  private attachEventStreamClient(ws: WebSocket): void {
    const eventCallback: EventCallback = (event, payload) => {
      if (ws.readyState !== WebSocket.OPEN) {
        return;
      }
      ws.send(JSON.stringify({
        channel: "generation",
        event,
        payload,
        ts: new Date().toISOString(),
        v: 1,
      }));
    };

    this.runManager.subscribeEvents(eventCallback);

    ws.on("close", () => {
      this.runManager.unsubscribeEvents(eventCallback);
    });
  }

  private async handleClientMessage(ws: WebSocket, msg: ClientMessage): Promise<void> {
    switch (msg.type) {
      case "pause":
        this.runManager.pause();
        this.send(ws, { type: "ack", action: "pause" });
        return;
      case "resume":
        this.runManager.resume();
        this.send(ws, { type: "ack", action: "resume" });
        return;
      case "inject_hint":
        this.runManager.injectHint(msg.text);
        this.send(ws, { type: "ack", action: "inject_hint" });
        return;
      case "override_gate":
        this.runManager.overrideGate(msg.decision);
        this.send(ws, { type: "ack", action: "override_gate", decision: msg.decision });
        return;
      case "chat_agent": {
        const text = await this.runManager.chatAgent(msg.role, msg.message);
        this.send(ws, { type: "chat_response", role: msg.role, text });
        return;
      }
      case "start_run": {
        const runId = await this.runManager.startRun(msg.scenario, msg.generations);
        this.send(ws, {
          type: "run_accepted",
          run_id: runId,
          scenario: msg.scenario,
          generations: msg.generations,
        });
        return;
      }
      case "list_scenarios": {
        const env = this.runManager.getEnvironmentInfo();
        this.send(ws, {
          type: "environments",
          scenarios: env.scenarios,
          executors: env.executors,
          current_executor: env.currentExecutor,
          agent_provider: env.agentProvider,
        });
        return;
      }
      case "create_scenario": {
        this.send(ws, {
          type: "scenario_generating",
          name: "custom_scenario",
        });
        const preview = await this.runManager.createScenario(msg.description);
        this.send(ws, {
          type: "scenario_preview",
          name: preview.name,
          display_name: preview.displayName,
          description: preview.description,
          strategy_params: preview.strategyParams,
          scoring_components: preview.scoringComponents,
          constraints: preview.constraints,
          win_threshold: preview.winThreshold,
        });
        return;
      }
      case "confirm_scenario": {
        this.send(ws, { type: "ack", action: "confirm_scenario" });
        const ready = await this.runManager.confirmScenario();
        this.send(ws, {
          type: "scenario_ready",
          name: ready.name,
          test_scores: ready.testScores,
        });
        return;
      }
      case "revise_scenario": {
        this.send(ws, {
          type: "scenario_generating",
          name: "custom_scenario",
        });
        const preview = await this.runManager.reviseScenario(msg.feedback);
        this.send(ws, {
          type: "scenario_preview",
          name: preview.name,
          display_name: preview.displayName,
          description: preview.description,
          strategy_params: preview.strategyParams,
          scoring_components: preview.scoringComponents,
          constraints: preview.constraints,
          win_threshold: preview.winThreshold,
        });
        return;
      }
      case "cancel_scenario":
        this.runManager.cancelScenario();
        this.send(ws, { type: "ack", action: "cancel_scenario" });
        return;
    }
  }

  private sendState(ws: WebSocket, state: RunManagerState): void {
    this.send(ws, {
      type: "state",
      paused: state.paused,
      generation: state.generation ?? undefined,
      phase: state.phase ?? undefined,
    });
  }

  private send(ws: WebSocket, msg: ServerMessage): void {
    if (ws.readyState !== WebSocket.OPEN) {
      return;
    }
    ws.send(JSON.stringify(msg));
  }

  private parseMessage(raw: string): ClientMessage {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    return parseClientMessage(parsed);
  }
}
