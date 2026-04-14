/**
 * Interactive WebSocket server for the TS control plane (AC-347 Task 25).
 */

import { createServer, type IncomingMessage, type Server as HttpServer, type ServerResponse } from "node:http";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { WebSocketServer, WebSocket } from "ws";
import type { AddressInfo } from "node:net";
import { URL, fileURLToPath } from "node:url";
import { MissionEventEmitter } from "../mission/events.js";
import { CampaignManager } from "../mission/campaign.js";
import { MissionManager } from "../mission/manager.js";
import { executeAuthCommand } from "./auth-command-workflow.js";
import {
  buildGenerationEventEnvelope,
  buildMissionProgressEventEnvelope,
} from "./event-stream-envelope.js";
import {
  buildMissionProgressMessage,
  subscribeToMissionProgressEvents,
} from "./mission-progress-workflow.js";
import { executeMissionActionRequest } from "./mission-action-workflow.js";
import { executeMissionReadRequest } from "./mission-read-workflow.js";
import { executeRunSimulationReadRequest, loadReplayArtifactResponse } from "./run-simulation-read-workflow.js";
import { buildCampaignApiRoutes } from "./campaign-api.js";
import { executeCampaignRouteRequest } from "./campaign-route-workflow.js";
import { buildClientErrorMessage } from "./client-error-workflow.js";
import { executeChatAgentCommand } from "./chat-agent-command-workflow.js";
import { executeInteractiveControlCommand } from "./interactive-control-command-workflow.js";
import { executeInteractiveScenarioCommand } from "./interactive-scenario-command-workflow.js";
import { buildMissionApiRoutes } from "./mission-api.js";
import { buildSimulationApiRoutes } from "./simulation-api.js";
import { renderDashboardHtml } from "./simulation-dashboard.js";
import { buildSessionBootstrapMessages, buildStateMessage } from "./websocket-session-bootstrap.js";
import { MissionProgressMsgSchema, parseClientMessage } from "./protocol.js";
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

export class PortInUseError extends Error {
  readonly port: number;

  constructor(port: number) {
    super(
      `Port ${port} is already in use. ` +
      `Try a different port with --port <N>, or use port 0 for auto-assignment.`,
    );
    this.name = "PortInUseError";
    this.port = port;
  }
}

export class InteractiveServer {
  readonly #runManager: RunManager;
  readonly #missionManager: MissionManager;
  readonly #campaignManager: CampaignManager;
  readonly #missionEvents: MissionEventEmitter;
  readonly #host: string;
  readonly #requestedPort: number;
  // Dashboard removed (AC-467) — server is API-only
  #httpServer: HttpServer | null = null;
  #wsServer: WebSocketServer | null = null;
  #boundPort = 0;

  constructor(opts: InteractiveServerOpts) {
    this.#runManager = opts.runManager;
    this.#missionEvents = new MissionEventEmitter();
    this.#missionManager = new MissionManager(this.#runManager.getDbPath(), {
      events: this.#missionEvents,
    });
    this.#campaignManager = new CampaignManager(this.#missionManager);
    this.#host = opts.host ?? "127.0.0.1";
    this.#requestedPort = opts.port ?? 8000;
    // Dashboard removed (AC-467)
  }

  get port(): number {
    return this.#boundPort;
  }

  get url(): string {
    return `ws://localhost:${this.#boundPort}/ws/interactive`;
  }

  async start(): Promise<number> {
    if (this.#httpServer) {
      return this.#boundPort;
    }

    const httpServer = createServer((req, res) => {
      void this.#handleHttpRequest(req, res).catch((err) => {
        const message = err instanceof Error ? err.message : String(err);
        if (!res.headersSent) {
          res.writeHead(500, { "Content-Type": "application/json" });
        }
        res.end(JSON.stringify({ error: message }, null, 2));
      });
    });

    const wsServer = new WebSocketServer({ noServer: true });
    httpServer.on("upgrade", (req, socket, head) => {
      if (req.url === "/ws/interactive") {
        wsServer.handleUpgrade(req, socket, head, (ws: WebSocket) => {
          this.#attachClient(ws);
        });
        return;
      }
      if (req.url === "/ws/events") {
        wsServer.handleUpgrade(req, socket, head, (ws: WebSocket) => {
          this.#attachEventStreamClient(ws);
        });
        return;
      }
      {
        socket.write("HTTP/1.1 404 Not Found\r\n\r\n");
        socket.destroy();
      }
    });

    await new Promise<void>((resolve, reject) => {
      httpServer.once("error", (err: NodeJS.ErrnoException) => {
        if (err.code === "EADDRINUSE") {
          reject(new PortInUseError(this.#requestedPort));
        } else {
          reject(err);
        }
      });
      httpServer.listen(this.#requestedPort, this.#host, () => {
        resolve();
      });
    });

    this.#httpServer = httpServer;
    this.#wsServer = wsServer;
    this.#boundPort = (httpServer.address() as AddressInfo).port;
    return this.#boundPort;
  }

  // ---------------------------------------------------------------------------
  // HTTP REST API (AC-364)
  // ---------------------------------------------------------------------------

  async #handleHttpRequest(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const requestUrl = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`);
    const url = requestUrl.pathname;
    const method = req.method ?? "GET";
    const campaignApi = buildCampaignApiRoutes(this.#campaignManager);
    const missionApi = buildMissionApiRoutes(this.#missionManager, this.#runManager.getRunsRoot());
    const simulationApi = buildSimulationApiRoutes(this.#runManager.getKnowledgeRoot());

    // CORS headers for dashboard
    res.setHeader("Access-Control-Allow-Origin", "*");
    res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
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

    // Root endpoint — API info.
    if (url === "/") {
      json(200, {
        service: "autocontext",
        version: "0.2.4",
        endpoints: {
          health: "/health",
          dashboard: "/dashboard",
          runs: "/api/runs",
          simulations: "/api/simulations",
          scenarios: "/api/scenarios",
          knowledge: "/api/knowledge/playbook/:scenario",
          campaigns: "/api/campaigns",
          missions: "/api/missions",
          websocket: "/ws/interactive",
          events: "/ws/events",
        },
      });
      return;
    }

    // Simulation dashboard HTML
    if (url === "/dashboard" || url === "/dashboard/") {
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(renderDashboardHtml());
      return;
    }

    // Health
    if (url === "/health") {
      json(200, { status: "ok" });
      return;
    }

    // GET /api/runs
    if (url === "/api/runs" || url.startsWith("/api/runs?")) {
      const response = executeRunSimulationReadRequest({
        route: "runs_list",
        runManager: this.#runManager,
        simulationApi,
        deps: {
          openStore: () => this.#openStore(),
          readPlaybook: () => null,
          loadReplayArtifactResponse,
        },
      });
      json(response.status, response.body);
      return;
    }

    // GET /api/runs/:id/replay/:gen
    const replayMatch = url.match(/^\/api\/runs\/([^/]+)\/replay\/(\d+)$/);
    if (replayMatch) {
      const [, runId, genStr] = replayMatch;
      const response = executeRunSimulationReadRequest({
        route: "run_replay",
        runId: runId!,
        generation: parseInt(genStr!, 10),
        runManager: this.#runManager,
        simulationApi,
        deps: {
          openStore: () => this.#openStore(),
          readPlaybook: () => null,
          loadReplayArtifactResponse,
        },
      });
      json(response.status, response.body);
      return;
    }

    // GET /api/runs/:id/status
    const statusMatch = url.match(/^\/api\/runs\/([^/]+)\/status$/);
    if (statusMatch) {
      const [, runId] = statusMatch;
      const response = executeRunSimulationReadRequest({
        route: "run_status",
        runId: runId!,
        runManager: this.#runManager,
        simulationApi,
        deps: {
          openStore: () => this.#openStore(),
          readPlaybook: () => null,
          loadReplayArtifactResponse,
        },
      });
      json(response.status, response.body);
      return;
    }

    // GET /api/knowledge/playbook/:scenario
    const playbookMatch = url.match(/^\/api\/knowledge\/playbook\/([^/]+)$/);
    if (playbookMatch) {
      const [, scenario] = playbookMatch;
      const response = executeRunSimulationReadRequest({
        route: "playbook",
        scenario: scenario!,
        runManager: this.#runManager,
        simulationApi,
        deps: {
          openStore: () => this.#openStore(),
          readPlaybook: (playbookScenario, roots) => {
            const artifacts = new ArtifactStore(roots);
            return artifacts.readPlaybook(playbookScenario);
          },
          loadReplayArtifactResponse,
        },
      });
      json(response.status, response.body);
      return;
    }

    // GET /api/scenarios
    if (url === "/api/scenarios") {
      const response = executeRunSimulationReadRequest({
        route: "scenarios",
        runManager: this.#runManager,
        simulationApi,
        deps: {
          openStore: () => this.#openStore(),
          readPlaybook: () => null,
          loadReplayArtifactResponse,
        },
      });
      json(response.status, response.body);
      return;
    }

    // GET /api/simulations
    if (method === "GET" && url === "/api/simulations") {
      const response = executeRunSimulationReadRequest({
        route: "simulations_list",
        runManager: this.#runManager,
        simulationApi,
        deps: {
          openStore: () => this.#openStore(),
          readPlaybook: () => null,
          loadReplayArtifactResponse,
        },
      });
      json(response.status, response.body);
      return;
    }

    // GET /api/simulations/:name
    const simulationMatch = url.match(/^\/api\/simulations\/([^/]+)$/);
    if (method === "GET" && simulationMatch) {
      const [, rawName] = simulationMatch;
      const response = executeRunSimulationReadRequest({
        route: "simulation_detail",
        simulationName: decodeURIComponent(rawName!),
        rawSimulationName: rawName!,
        runManager: this.#runManager,
        simulationApi,
        deps: {
          openStore: () => this.#openStore(),
          readPlaybook: () => null,
          loadReplayArtifactResponse,
        },
      });
      json(response.status, response.body);
      return;
    }

    // GET /api/simulations/:name/dashboard
    const simulationDashboardMatch = url.match(
      /^\/api\/simulations\/([^/]+)\/dashboard$/,
    );
    if (method === "GET" && simulationDashboardMatch) {
      const [, rawName] = simulationDashboardMatch;
      const response = executeRunSimulationReadRequest({
        route: "simulation_dashboard",
        simulationName: decodeURIComponent(rawName!),
        rawSimulationName: rawName!,
        runManager: this.#runManager,
        simulationApi,
        deps: {
          openStore: () => this.#openStore(),
          readPlaybook: () => null,
          loadReplayArtifactResponse,
        },
      });
      json(response.status, response.body);
      return;
    }

    // GET /api/campaigns
    if (method === "GET" && url === "/api/campaigns") {
      const response = executeCampaignRouteRequest({
        route: "list",
        queryStatus: requestUrl.searchParams.get("status") ?? undefined,
        body: {},
        campaignApi,
        campaignManager: this.#campaignManager,
      });
      json(response.status, response.body);
      return;
    }

    // POST /api/campaigns
    if (method === "POST" && url === "/api/campaigns") {
      const response = executeCampaignRouteRequest({
        route: "create",
        body: await this.#readJsonBody(req),
        campaignApi,
        campaignManager: this.#campaignManager,
      });
      json(response.status, response.body);
      return;
    }

    // GET /api/campaigns/:id
    const campaignMatch = url.match(/^\/api\/campaigns\/([^/]+)$/);
    if (method === "GET" && campaignMatch) {
      const [, campaignId] = campaignMatch;
      const response = executeCampaignRouteRequest({
        route: "detail",
        campaignId: campaignId!,
        body: {},
        campaignApi,
        campaignManager: this.#campaignManager,
      });
      json(response.status, response.body);
      return;
    }

    // GET /api/campaigns/:id/progress
    const campaignProgressMatch = url.match(/^\/api\/campaigns\/([^/]+)\/progress$/);
    if (method === "GET" && campaignProgressMatch) {
      const [, campaignId] = campaignProgressMatch;
      const response = executeCampaignRouteRequest({
        route: "progress",
        campaignId: campaignId!,
        body: {},
        campaignApi,
        campaignManager: this.#campaignManager,
      });
      json(response.status, response.body);
      return;
    }

    // POST /api/campaigns/:id/missions
    const campaignMissionMatch = url.match(/^\/api\/campaigns\/([^/]+)\/missions$/);
    if (method === "POST" && campaignMissionMatch) {
      const [, campaignId] = campaignMissionMatch;
      const response = executeCampaignRouteRequest({
        route: "add_mission",
        campaignId: campaignId!,
        body: await this.#readJsonBody(req),
        campaignApi,
        campaignManager: this.#campaignManager,
      });
      json(response.status, response.body);
      return;
    }

    // POST /api/campaigns/:id/(pause|resume|cancel)
    const campaignActionMatch = url.match(/^\/api\/campaigns\/([^/]+)\/(pause|resume|cancel)$/);
    if (method === "POST" && campaignActionMatch) {
      const [, campaignId, action] = campaignActionMatch;
      const response = executeCampaignRouteRequest({
        route: "status",
        campaignId: campaignId!,
        action: action as "pause" | "resume" | "cancel",
        body: {},
        campaignApi,
        campaignManager: this.#campaignManager,
      });
      json(response.status, response.body);
      return;
    }

    // GET /api/missions
    if (method === "GET" && url === "/api/missions") {
      json(200, missionApi.listMissions(requestUrl.searchParams.get("status") ?? undefined));
      return;
    }

    // GET /api/missions/:id
    const missionMatch = url.match(/^\/api\/missions\/([^/]+)$/);
    if (method === "GET" && missionMatch) {
      const [, missionId] = missionMatch;
      const response = executeMissionReadRequest({
        missionId: missionId!,
        resource: "detail",
        missionManager: this.#missionManager,
        missionApi,
      });
      json(response.status, response.body);
      return;
    }

    // GET /api/missions/:id/steps
    const missionStepsMatch = url.match(/^\/api\/missions\/([^/]+)\/steps$/);
    if (method === "GET" && missionStepsMatch) {
      const [, missionId] = missionStepsMatch;
      const response = executeMissionReadRequest({
        missionId: missionId!,
        resource: "steps",
        missionManager: this.#missionManager,
        missionApi,
      });
      json(response.status, response.body);
      return;
    }

    // GET /api/missions/:id/subgoals
    const missionSubgoalsMatch = url.match(/^\/api\/missions\/([^/]+)\/subgoals$/);
    if (method === "GET" && missionSubgoalsMatch) {
      const [, missionId] = missionSubgoalsMatch;
      const response = executeMissionReadRequest({
        missionId: missionId!,
        resource: "subgoals",
        missionManager: this.#missionManager,
        missionApi,
      });
      json(response.status, response.body);
      return;
    }

    // GET /api/missions/:id/budget
    const missionBudgetMatch = url.match(/^\/api\/missions\/([^/]+)\/budget$/);
    if (method === "GET" && missionBudgetMatch) {
      const [, missionId] = missionBudgetMatch;
      const response = executeMissionReadRequest({
        missionId: missionId!,
        resource: "budget",
        missionManager: this.#missionManager,
        missionApi,
      });
      json(response.status, response.body);
      return;
    }

    // GET /api/missions/:id/artifacts
    const missionArtifactsMatch = url.match(/^\/api\/missions\/([^/]+)\/artifacts$/);
    if (method === "GET" && missionArtifactsMatch) {
      const [, missionId] = missionArtifactsMatch;
      const response = executeMissionReadRequest({
        missionId: missionId!,
        resource: "artifacts",
        missionManager: this.#missionManager,
        missionApi,
      });
      json(response.status, response.body);
      return;
    }

    // POST /api/missions/:id/(run|pause|resume|cancel)
    const missionActionMatch = url.match(/^\/api\/missions\/([^/]+)\/(run|pause|resume|cancel)$/);
    if (method === "POST" && missionActionMatch) {
      const [, missionId, action] = missionActionMatch;
      const body = action === "run" ? await this.#readJsonBody(req) : {};
      const response = await executeMissionActionRequest({
        action: action as "run" | "pause" | "resume" | "cancel",
        missionId: missionId!,
        body,
        missionManager: this.#missionManager,
        runManager: this.#runManager,
      });
      json(response.status, response.body);
      return;
    }

    // 404 fallback
    json(404, { error: "Not found" });
  }

  #openStore(): SQLiteStore {
    const store = new SQLiteStore(this.#runManager.getDbPath());
    store.migrate(this.#runManager.getMigrationsDir());
    return store;
  }

  #withStore(fn: (store: SQLiteStore) => void): void {
    const store = this.#openStore();
    try {
      fn(store);
    } finally {
      store.close();
    }
  }

  async #readJsonBody(req: IncomingMessage): Promise<Record<string, unknown>> {
    const chunks: Buffer[] = [];
    for await (const chunk of req) {
      chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    }
    if (chunks.length === 0) {
      return {};
    }
    return JSON.parse(Buffer.concat(chunks).toString("utf-8")) as Record<string, unknown>;
  }

  #buildMissionProgress(missionId: string, latestStep?: string): Extract<ServerMessage, { type: "mission_progress" }> | null {
    return buildMissionProgressMessage({
      missionId,
      latestStep,
      missionManager: this.#missionManager,
    });
  }

  async stop(): Promise<void> {
    const wsServer = this.#wsServer;
    const httpServer = this.#httpServer;
    this.#wsServer = null;
    this.#httpServer = null;
    this.#boundPort = 0;

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

    this.#campaignManager.close();
    this.#missionManager.close();
  }

  #attachClient(ws: WebSocket): void {
    const env = this.#runManager.getEnvironmentInfo();
    const eventCallback: EventCallback = (event, payload) => {
      this.#send(ws, { type: "event", event, payload });
    };
    const stateCallback = (state: RunManagerState) => {
      this.#sendState(ws, state);
    };

    this.#runManager.subscribeEvents(eventCallback);
    this.#runManager.subscribeState(stateCallback);

    const unsubscribeMissionProgress = subscribeToMissionProgressEvents({
      missionEvents: this.#missionEvents,
      buildMissionProgress: (missionId, latestStep) => this.#buildMissionProgress(missionId, latestStep),
      onProgress: (progress) => {
        this.#send(ws, progress);
      },
    });

    for (const message of buildSessionBootstrapMessages(env, this.#runManager.getState())) {
      this.#send(ws, message);
    }

    ws.on("message", async (data: WebSocket.RawData) => {
      let parsedMessage: ClientMessage | null = null;
      try {
        parsedMessage = this.#parseMessage(data.toString());
        await this.#handleClientMessage(ws, parsedMessage);
      } catch (err) {
        this.#send(ws, buildClientErrorMessage(err, parsedMessage));
      }
    });

    ws.on("close", () => {
      this.#runManager.unsubscribeEvents(eventCallback);
      this.#runManager.unsubscribeState(stateCallback);
      unsubscribeMissionProgress();
    });
  }

  #attachEventStreamClient(ws: WebSocket): void {
    const eventCallback: EventCallback = (event, payload) => {
      if (ws.readyState !== WebSocket.OPEN) {
        return;
      }
      ws.send(JSON.stringify(buildGenerationEventEnvelope(event, payload)));
    };

    this.#runManager.subscribeEvents(eventCallback);

    const unsubscribeMissionProgress = subscribeToMissionProgressEvents({
      missionEvents: this.#missionEvents,
      buildMissionProgress: (missionId, latestStep) => this.#buildMissionProgress(missionId, latestStep),
      onProgress: (progress) => {
        if (ws.readyState !== WebSocket.OPEN) {
          return;
        }
        ws.send(JSON.stringify(buildMissionProgressEventEnvelope(progress)));
      },
    });

    ws.on("close", () => {
      this.#runManager.unsubscribeEvents(eventCallback);
      unsubscribeMissionProgress();
    });
  }

  async #handleClientMessage(ws: WebSocket, msg: ClientMessage): Promise<void> {
    switch (msg.type) {
      case "pause":
      case "resume":
      case "inject_hint":
      case "override_gate":
      case "start_run":
      case "list_scenarios": {
        for (const response of await executeInteractiveControlCommand({
          command: msg,
          runManager: this.#runManager,
        })) {
          this.#send(ws, response);
        }
        return;
      }
      case "chat_agent": {
        for (const response of await executeChatAgentCommand({
          command: msg,
          runManager: this.#runManager,
        })) {
          this.#send(ws, response);
        }
        return;
      }
      case "create_scenario":
      case "confirm_scenario":
      case "revise_scenario":
      case "cancel_scenario": {
        for (const response of await executeInteractiveScenarioCommand({
          command: msg,
          runManager: this.#runManager,
        })) {
          this.#send(ws, response);
        }
        return;
      }
      case "login":
      case "logout":
      case "switch_provider":
      case "whoami": {
        this.#send(ws, await executeAuthCommand({
          command: msg,
          runManager: this.#runManager,
        }));
        return;
      }
    }
  }

  #sendState(ws: WebSocket, state: RunManagerState): void {
    this.#send(ws, buildStateMessage(state));
  }

  #send(ws: WebSocket, msg: ServerMessage): void {
    if (ws.readyState !== WebSocket.OPEN) {
      return;
    }
    ws.send(JSON.stringify(msg));
  }

  #parseMessage(raw: string): ClientMessage {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    return parseClientMessage(parsed);
  }
}
