/**
 * Interactive WebSocket server for the TS control plane (AC-347 Task 25).
 */

import {
  createServer,
  type IncomingMessage,
  type Server as HttpServer,
  type ServerResponse,
} from "node:http";
import { WebSocketServer, WebSocket } from "ws";
import type { AddressInfo } from "node:net";
import { URL } from "node:url";
import { MissionEventEmitter } from "../mission/events.js";
import { CampaignManager } from "../mission/campaign.js";
import { MissionManager } from "../mission/manager.js";
import { executeAuthCommand } from "./auth-command-workflow.js";
import {
  buildEventStreamEnvelope,
  buildMissionProgressEventEnvelope,
} from "./event-stream-envelope.js";
import {
  buildMissionProgressMessage,
  subscribeToMissionProgressEvents,
} from "./mission-progress-workflow.js";
import { buildCampaignApiRoutes } from "./campaign-api.js";
import { buildClientErrorMessage } from "./client-error-workflow.js";
import { executeChatAgentCommand } from "./chat-agent-command-workflow.js";
import { executeInteractiveControlCommand } from "./interactive-control-command-workflow.js";
import { executeInteractiveScenarioCommand } from "./interactive-scenario-command-workflow.js";
import { buildCockpitApiRoutes } from "./cockpit-api.js";
import { buildHubApiRoutes } from "./hub-api.js";
import { buildKnowledgeApiRoutes } from "./knowledge-api.js";
import { buildMissionApiRoutes } from "./mission-api.js";
import { buildMonitorApiRoutes } from "./monitor-api.js";
import { MonitorEngine } from "./monitor-engine.js";
import { buildNotebookApiRoutes } from "./notebook-api.js";
import { buildOpenClawApiRoutes } from "./openclaw-api.js";
import { buildBackgroundSessionApiRoutes } from "./background-session-api.js";
import { buildRuntimeSessionApiRoutes } from "./runtime-session-api.js";
import { buildSimulationApiRoutes } from "./simulation-api.js";
import { buildTraceGateReviewApiRoutes } from "./trace-gate-review-api.js";
import { buildSessionBootstrapMessages, buildStateMessage } from "./websocket-session-bootstrap.js";
import {
  loadReplayArtifactResponse,
  type RunSimulationReadDeps,
} from "./run-simulation-read-workflow.js";
import type { HttpRouteContext } from "./routes/http-route-context.js";
import { tryRootRoutes } from "./routes/root-routes.js";
import { tryNotebookRoutes } from "./routes/notebook-routes.js";
import { tryMonitorRoutes } from "./routes/monitor-routes.js";
import { tryOpenClawRoutes } from "./routes/openclaw-routes.js";
import { tryCockpitRoutes } from "./routes/cockpit-routes.js";
import { tryHubRoutes } from "./routes/hub-routes.js";
import { tryRunListRoutes } from "./routes/run-list-routes.js";
import { tryKnowledgeRoutes } from "./routes/knowledge-routes.js";
import { tryScenarioSimulationRoutes } from "./routes/scenario-simulation-routes.js";
import { tryCampaignRoutes } from "./routes/campaign-routes.js";
import { tryMissionRoutes } from "./routes/mission-routes.js";
import { parseClientMessage } from "./protocol.js";
import type { ClientMessage, ServerMessage } from "./protocol.js";
import type { RunManager } from "./run-manager.js";
import type { RunManagerState } from "./run-manager.js";
import type { EventCallback } from "../loop/events.js";
import { loadSettings, type AppSettings } from "../config/index.js";
import { RuntimeSessionEventStore } from "../session/runtime-events.js";
import { SQLiteStore } from "../storage/index.js";
import { ArtifactStore } from "../knowledge/artifact-store.js";
import { SolveManager } from "../knowledge/solver.js";
import type { LLMProvider } from "../types/index.js";

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
  #solveManager: SolveManager | null = null;
  #solveStore: SQLiteStore | null = null;
  #solveProvider: LLMProvider | null = null;
  #monitorEngine: MonitorEngine | null = null;
  #monitorStore: SQLiteStore | null = null;
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
      socket.write("HTTP/1.1 404 Not Found\r\n\r\n");
      socket.destroy();
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
    const settings = loadSettings();

    // Shared per-request closures (AC-852): built once, reused across every
    // route builder below instead of repeating `() => this.#openStore()` and
    // `() => new RuntimeSessionEventStore(...)` at each call site.
    const openStore = () => this.#openStore();
    const openRuntimeSessionStore = () =>
      new RuntimeSessionEventStore(this.#runManager.getDbPath());

    const campaignApi = buildCampaignApiRoutes(this.#campaignManager);
    const missionApi = buildMissionApiRoutes(this.#missionManager, this.#runManager.getRunsRoot());
    const artifactStore = new ArtifactStore({
      runsRoot: this.#runManager.getRunsRoot(),
      knowledgeRoot: this.#runManager.getKnowledgeRoot(),
    });
    const knowledgeApi = buildKnowledgeApiRoutes({
      runsRoot: this.#runManager.getRunsRoot(),
      knowledgeRoot: this.#runManager.getKnowledgeRoot(),
      skillsRoot: this.#runManager.getSkillsRoot(),
      openStore,
      getSolveManager: () => this.#getSolveManager(),
    });
    const notebookApi = buildNotebookApiRoutes({
      openStore,
      artifacts: artifactStore,
      emitNotebookEvent: (event, payload) => {
        this.#runManager.events.emit(event, payload, "notebook");
      },
    });
    const cockpitNotebookApi = buildNotebookApiRoutes({
      openStore,
      artifacts: artifactStore,
      emitNotebookEvent: (event, payload) => {
        this.#runManager.events.emit(event, { ...payload, source: "cockpit" }, "cockpit");
      },
    });
    const cockpitApi = buildCockpitApiRoutes({
      openStore,
      openRuntimeSessionStore,
      notebookApi: cockpitNotebookApi,
      settings,
      runsRoot: this.#runManager.getRunsRoot(),
      knowledgeRoot: this.#runManager.getKnowledgeRoot(),
    });
    const backgroundSessionApi = buildBackgroundSessionApiRoutes({
      openStore: openRuntimeSessionStore,
      openSourceStore: openStore,
    });
    const runtimeSessionApi = buildRuntimeSessionApiRoutes({
      openStore: openRuntimeSessionStore,
    });
    const traceGateReviewApi = buildTraceGateReviewApiRoutes({
      runsRoot: this.#runManager.getRunsRoot(),
    });
    const hubApi = buildHubApiRoutes({
      runsRoot: this.#runManager.getRunsRoot(),
      knowledgeRoot: this.#runManager.getKnowledgeRoot(),
      skillsRoot: this.#runManager.getSkillsRoot(),
      openStore,
    });
    const monitorApi = buildMonitorApiRoutes({
      openStore,
      monitorEngine: settings.monitorEnabled ? this.#getMonitorEngine(settings) : null,
      defaultHeartbeatTimeoutSeconds: settings.monitorHeartbeatTimeout,
      maxConditions: settings.monitorMaxConditions,
    });
    const openClawApi = buildOpenClawApiRoutes({
      knowledgeRoot: this.#runManager.getKnowledgeRoot(),
      settings: loadSettings(),
      openStore,
    });
    const simulationApi = buildSimulationApiRoutes(this.#runManager.getKnowledgeRoot());

    // CORS headers for dashboard/API clients. Keep this local by default instead of using '*'.
    res.setHeader(
      "Access-Control-Allow-Origin",
      resolveCorsOrigin(req.headers.origin, this.#host, this.#boundPort || this.#requestedPort),
    );
    res.setHeader("Vary", "Origin");
    res.setHeader("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type");

    if (method === "OPTIONS") {
      res.writeHead(204);
      res.end();
      return;
    }

    const json = (status: number, body: unknown) => {
      if (status === 204) {
        res.writeHead(status);
        res.end();
        return;
      }
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify(body, null, 2));
    };

    const ctx: HttpRouteContext = {
      url,
      method,
      requestUrl,
      res,
      json,
      readJsonBody: () => this.#readJsonBody(req),
    };

    // Shared executeRunSimulationReadRequest deps (AC-852): the playbook
    // route is the one call site with a real readPlaybook implementation;
    // every other run/scenario/simulation route keeps the null-returning
    // stub the original code used (see AC-852 task report for the tracked
    // asymmetry — this refactor does not wire new capability).
    const runSimDeps: RunSimulationReadDeps = {
      openStore,
      readPlaybook: () => null,
      loadReplayArtifactResponse,
    };
    const playbookDeps: RunSimulationReadDeps = {
      openStore,
      readPlaybook: (playbookScenario, roots) => {
        const artifacts = new ArtifactStore(roots);
        return artifacts.readPlaybook(playbookScenario);
      },
      loadReplayArtifactResponse,
    };

    if (tryRootRoutes(ctx)) return;
    if (await tryNotebookRoutes(ctx, notebookApi)) return;
    if (await tryMonitorRoutes(ctx, monitorApi)) return;
    if (await tryOpenClawRoutes(ctx, openClawApi)) return;
    if (
      await tryCockpitRoutes(ctx, {
        cockpitApi,
        backgroundSessionApi,
        runtimeSessionApi,
        traceGateReviewApi,
      })
    )
      return;
    if (await tryHubRoutes(ctx, hubApi)) return;
    if (
      await tryRunListRoutes(ctx, {
        runManager: this.#runManager,
        simulationApi,
        runSimDeps,
        playbookDeps,
      })
    )
      return;
    if (await tryKnowledgeRoutes(ctx, knowledgeApi)) return;
    if (
      await tryScenarioSimulationRoutes(ctx, {
        runManager: this.#runManager,
        simulationApi,
        runSimDeps,
      })
    )
      return;
    if (await tryCampaignRoutes(ctx, { campaignApi, campaignManager: this.#campaignManager }))
      return;
    if (
      await tryMissionRoutes(ctx, {
        missionApi,
        missionManager: this.#missionManager,
        runManager: this.#runManager,
      })
    )
      return;

    // 404 fallback
    json(404, { error: "Not found" });
  }

  #openStore(): SQLiteStore {
    const store = new SQLiteStore(this.#runManager.getDbPath());
    store.migrate(this.#runManager.getMigrationsDir());
    return store;
  }

  #getSolveManager(): SolveManager {
    if (!this.#solveManager) {
      this.#solveStore = this.#openStore();
      this.#solveProvider = this.#runManager.buildProvider();
      this.#solveManager = new SolveManager({
        provider: this.#solveProvider,
        store: this.#solveStore,
        runsRoot: this.#runManager.getRunsRoot(),
        knowledgeRoot: this.#runManager.getKnowledgeRoot(),
      });
    }
    return this.#solveManager;
  }

  #getMonitorEngine(settings: AppSettings): MonitorEngine {
    if (!this.#monitorEngine) {
      this.#monitorStore = this.#openStore();
      this.#monitorEngine = new MonitorEngine({
        store: this.#monitorStore,
        emitter: this.#runManager.events,
        defaultHeartbeatTimeoutSeconds: settings.monitorHeartbeatTimeout,
        maxConditions: settings.monitorMaxConditions,
      });
      this.#monitorEngine.start();
    }
    return this.#monitorEngine;
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

  #buildMissionProgress(
    missionId: string,
    latestStep?: string,
  ): Extract<ServerMessage, { type: "mission_progress" }> | null {
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
    this.#monitorEngine?.stop();
    this.#monitorEngine = null;
    this.#monitorStore?.close();
    this.#monitorStore = null;
    this.#solveStore?.close();
    this.#solveStore = null;
    this.#solveProvider?.close?.();
    this.#solveProvider = null;
    this.#solveManager = null;
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
      buildMissionProgress: (missionId, latestStep) =>
        this.#buildMissionProgress(missionId, latestStep),
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
    let sequence = 0;
    const nextSequence = () => {
      sequence += 1;
      return sequence;
    };

    const eventCallback: EventCallback = (event, payload, record) => {
      if (ws.readyState !== WebSocket.OPEN) {
        return;
      }
      ws.send(
        JSON.stringify(
          buildEventStreamEnvelope({
            channel: record?.channel ?? "generation",
            event,
            payload,
            seq: nextSequence(),
            timestamp: record?.ts,
          }),
        ),
      );
    };

    this.#runManager.subscribeEvents(eventCallback);

    const unsubscribeMissionProgress = subscribeToMissionProgressEvents({
      missionEvents: this.#missionEvents,
      buildMissionProgress: (missionId, latestStep) =>
        this.#buildMissionProgress(missionId, latestStep),
      onProgress: (progress) => {
        if (ws.readyState !== WebSocket.OPEN) {
          return;
        }
        ws.send(JSON.stringify(buildMissionProgressEventEnvelope(progress, nextSequence())));
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
        this.#send(
          ws,
          await executeAuthCommand({
            command: msg,
            runManager: this.#runManager,
          }),
        );
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

function resolveCorsOrigin(
  origin: string | string[] | undefined,
  host: string,
  port: number,
): string {
  const requestedOrigin = Array.isArray(origin) ? origin[0] : origin;
  if (requestedOrigin && isTrustedLocalOrigin(requestedOrigin, host)) {
    return requestedOrigin;
  }
  const displayHost = host === "0.0.0.0" || host === "::" ? "127.0.0.1" : host;
  return `http://${displayHost}:${port}`;
}

function isTrustedLocalOrigin(origin: string, host: string): boolean {
  try {
    const parsed = new URL(origin);
    const allowedHosts = new Set(["localhost", "127.0.0.1", "::1", host]);
    return parsed.protocol === "http:" && allowedHosts.has(parsed.hostname);
  } catch {
    return false;
  }
}
