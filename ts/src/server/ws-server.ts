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
import { join } from "node:path";
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
import {
  buildEnvironmentMessage,
  buildHelloMessage,
  buildSessionBootstrapMessages,
  buildStateMessage,
} from "./websocket-session-bootstrap.js";
import {
  loadReplayArtifactResponse,
  type RunSimulationReadDeps,
  type RunSimulationReadDepsWithPlaybook,
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
import {
  AGENT_PROGRESS_NOTE_EVENT_NAME,
  parseClientMessage,
  TRANSCRIPT_PROTOCOL_QUERY_PARAM,
  TRANSCRIPT_PROTOCOL_QUERY_VALUE,
} from "./protocol.js";
import type { ClientMessage, ServerMessage } from "./protocol.js";
import { RunTranscriptStore, type RetainedRunFrame } from "./run-transcript-store.js";
import type { RunManager } from "./run-manager.js";
import type { RunManagerState } from "./run-manager.js";
import type { EventCallback } from "../loop/events.js";
import { loadSettings, type AppSettings } from "../config/index.js";
import { RuntimeSessionEventStore } from "../session/runtime-events.js";
import { SQLiteStore } from "../storage/index.js";
import { asDbPath, asScenarioName } from "../domain/ids.js";
import { ArtifactStore } from "../knowledge/artifact-store.js";
import { SolveManager } from "../knowledge/solver.js";
import type { LLMProvider } from "../types/index.js";
import { MAX_IMAGE_AGGREGATE_ENCODED_BYTES } from "../types/image-attachments.js";

export const MAX_INTERACTIVE_WEBSOCKET_PAYLOAD_BYTES =
  MAX_IMAGE_AGGREGATE_ENCODED_BYTES + 1024 * 1024;
export const MAX_GLOBAL_INTERACTIVE_MESSAGE_BYTES =
  MAX_INTERACTIVE_WEBSOCKET_PAYLOAD_BYTES;
export const MAX_CONCURRENT_INTERACTIVE_MESSAGE_HANDLERS = 2;
export const MAX_INTERACTIVE_WEBSOCKET_CONNECTIONS = 4;
export const MAX_INTERACTIVE_WEBSOCKET_BUFFERED_BYTES =
  MAX_INTERACTIVE_WEBSOCKET_PAYLOAD_BYTES;
const MAX_PENDING_INTERACTIVE_MESSAGES = 32;
const MAX_PENDING_PRIORITY_CONTROL_MESSAGES = 8;
const MAX_GLOBAL_PRIORITY_CONTROL_BYTES = 64 * 1024;

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
  readonly #runTranscripts: RunTranscriptStore;
  readonly #interactiveClients = new Set<WebSocket>();
  readonly #eventStreamClients = new Set<WebSocket>();
  readonly #transcriptClients = new Set<WebSocket>();
  readonly #clientRunScopes = new Map<WebSocket, string>();
  readonly #interactiveMessageWaiters: Array<() => void> = [];
  readonly #onRunEvent: EventCallback = (event, payload, record) => {
    this.#broadcastRunEvent(event, payload, record?.ts);
  };
  readonly #onRunState = (state: RunManagerState) => {
    this.#broadcastRunState(state);
  };
  #pendingStart: {
    clientRunId: string | null;
    runTranscript: boolean;
    ws: WebSocket;
    queuedState: RunManagerState | null;
  } | null = null;
  #pendingInteractiveMessageBytes = 0;
  #pendingInteractiveMessageCount = 0;
  #pendingPriorityControlBytes = 0;
  #pendingPriorityControlCount = 0;
  #activeInteractiveMessageHandlers = 0;
  #runSubscriptionsActive = false;
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
    this.#runTranscripts = new RunTranscriptStore(
      join(this.#runManager.getRunsRoot(), "_interactive", "run-transcript.ndjson"),
    );
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

    const wsServer = new WebSocketServer({
      noServer: true,
      maxPayload: MAX_INTERACTIVE_WEBSOCKET_PAYLOAD_BYTES,
    });
    httpServer.on("upgrade", (req, socket, head) => {
      const requestUrl = new URL(req.url ?? "/", "http://localhost");
      if (!isTrustedWebSocketOrigin(req.headers.origin, this.#host, this.#boundPort)) {
        socket.write("HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n");
        socket.destroy();
        return;
      }
      if (
        this.#interactiveClients.size + this.#eventStreamClients.size >=
        MAX_INTERACTIVE_WEBSOCKET_CONNECTIONS
      ) {
        socket.write("HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n\r\n");
        socket.destroy();
        return;
      }
      if (requestUrl.pathname === "/ws/interactive") {
        const runTranscript =
          requestUrl.searchParams.get(TRANSCRIPT_PROTOCOL_QUERY_PARAM) ===
          TRANSCRIPT_PROTOCOL_QUERY_VALUE;
        wsServer.handleUpgrade(req, socket, head, (ws: WebSocket) => {
          this.#attachClient(ws, runTranscript);
        });
        return;
      }
      if (requestUrl.pathname === "/ws/events") {
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
    this.#subscribeToRunUpdates();
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

    // Shared executeRunSimulationReadRequest deps (AC-852). readPlaybook is
    // optional (AC-862): only the "playbook" route reads it, and every other
    // run/scenario/simulation route dispatches a fixed route literal that
    // never reaches that case, so readPlaybook is dropped here as a dead
    // parameter (see AC-862 task report for the investigation).
    const runSimDeps: RunSimulationReadDeps = {
      openStore,
      loadReplayArtifactResponse,
    };
    const playbookDeps: RunSimulationReadDepsWithPlaybook = {
      openStore,
      readPlaybook: (playbookScenario, roots) => {
        const artifacts = new ArtifactStore(roots);
        return artifacts.readPlaybook(asScenarioName(playbookScenario));
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
    const store = new SQLiteStore(asDbPath(this.#runManager.getDbPath()));
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
    this.#unsubscribeFromRunUpdates();

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
    this.#interactiveClients.clear();
    this.#eventStreamClients.clear();
    this.#transcriptClients.clear();
    this.#clientRunScopes.clear();
    this.#pendingStart = null;
  }

  #attachClient(ws: WebSocket, runTranscript: boolean): void {
    const env = this.#runManager.getEnvironmentInfo();
    this.#interactiveClients.add(ws);
    if (runTranscript) this.#transcriptClients.add(ws);

    const unsubscribeMissionProgress = subscribeToMissionProgressEvents({
      missionEvents: this.#missionEvents,
      buildMissionProgress: (missionId, latestStep) =>
        this.#buildMissionProgress(missionId, latestStep),
      onProgress: (progress) => {
        this.#send(ws, progress);
      },
    });

    const state = this.#runManager.getState();
    for (const message of buildSessionBootstrapMessages(env, state, {
      runTranscript,
      capabilities: this.#runManager.getInteractiveCapabilities(),
    })) {
      if (message.type !== "state") {
        this.#send(ws, message);
        continue;
      }
      const clientRunId = state.runId ? this.#runTranscripts.resolveClientRunId(state.runId) : null;
      if (runTranscript && clientRunId) {
        this.#send(ws, {
          ...message,
          client_run_id: clientRunId,
          ...(state.runId ? { run_id: state.runId } : {}),
        });
        continue;
      }
      this.#send(ws, message);
    }

    let pendingMessageBytes = 0;
    let pendingPriorityBytes = 0;
    let messageTail = Promise.resolve();
    ws.on("message", (data: WebSocket.RawData) => {
      const messageBytes = rawDataByteLength(data);
      const preReservedNormalLane = messageBytes > MAX_GLOBAL_PRIORITY_CONTROL_BYTES;
      if (preReservedNormalLane) {
        if (
          pendingMessageBytes + messageBytes > MAX_INTERACTIVE_WEBSOCKET_PAYLOAD_BYTES
        ) {
          ws.close(1009, "interactive message queue exceeds the payload limit");
          return;
        }
        if (!this.#reserveInteractiveMessage(messageBytes, false)) {
          ws.close(1013, "interactive server message budget is busy");
          return;
        }
        pendingMessageBytes += messageBytes;
      }
      let parsedMessage: ClientMessage | null = null;
      try {
        parsedMessage = this.#parseMessage(data.toString());
      } catch {
        if (preReservedNormalLane) {
          pendingMessageBytes -= messageBytes;
          this.#releaseInteractiveMessage(messageBytes, false);
        }
        this.#sendLegacyOrCurrent(ws, { type: "error", message: "invalid interactive message" });
        ws.close(1008, "invalid interactive message");
        return;
      }
      const message = parsedMessage;
      // Large frames are admitted to the normal lane before parsing. Even a
      // syntactically valid priority command can exceed the priority envelope
      // through insignificant JSON whitespace, so settlement must follow the
      // lane that actually owns the reservation.
      const priority = !preReservedNormalLane && isPriorityRunControlMessage(message);
      if (!preReservedNormalLane) {
        if (
          (priority ? pendingPriorityBytes : pendingMessageBytes) + messageBytes >
          (priority ? MAX_GLOBAL_PRIORITY_CONTROL_BYTES : MAX_INTERACTIVE_WEBSOCKET_PAYLOAD_BYTES)
        ) {
          ws.close(1009, "interactive message queue exceeds the payload limit");
          return;
        }
        if (!this.#reserveInteractiveMessage(messageBytes, priority)) {
          ws.close(1013, "interactive server message budget is busy");
          return;
        }
        if (priority) pendingPriorityBytes += messageBytes;
        else pendingMessageBytes += messageBytes;
      }

      const handleMessage = async () => {
        const usesExpensiveSlot = interactiveMessageUsesExpensiveSlot(message);
        if (usesExpensiveSlot) await this.#acquireInteractiveMessageHandler();
        try {
          if (ws.readyState !== WebSocket.OPEN) return;
          await this.#handleClientMessage(ws, message);
        } catch (err) {
          const response = buildClientErrorMessage(err, message);
          const clientRunId = readClientRunId(message);
          const commandId = readCommandId(message);
          if (
            clientRunId &&
            commandId &&
            this.#runTranscripts.canCompleteCommand({
              clientRunId,
              commandId,
              command: message,
            })
          ) {
            this.#sendRunResponse(ws, response, clientRunId, message);
          } else {
            this.#sendLegacyOrCurrent(ws, response);
          }
        } finally {
          if (usesExpensiveSlot) this.#releaseInteractiveMessageHandler();
        }
      };
      const settleMessage = () => {
        if (priority) pendingPriorityBytes -= messageBytes;
        else pendingMessageBytes -= messageBytes;
        this.#releaseInteractiveMessage(messageBytes, priority);
      };
      if (priority) {
        void handleMessage().finally(settleMessage);
        return;
      }
      messageTail = messageTail.then(handleMessage).finally(settleMessage);
    });

    // Payload-limit and peer/network failures are connection-local and must not
    // become uncaught EventEmitter errors for the server process.
    ws.on("error", () => undefined);

    ws.on("close", () => {
      this.#interactiveClients.delete(ws);
      this.#transcriptClients.delete(ws);
      this.#clientRunScopes.delete(ws);
      unsubscribeMissionProgress();
    });
  }

  #attachEventStreamClient(ws: WebSocket): void {
    this.#eventStreamClients.add(ws);
    let sequence = 0;
    const nextSequence = () => {
      sequence += 1;
      return sequence;
    };

    const eventCallback: EventCallback = (event, payload, record) => {
      if (event === AGENT_PROGRESS_NOTE_EVENT_NAME) {
        return;
      }
      if (ws.readyState !== WebSocket.OPEN) {
        return;
      }
      this.#sendWire(ws, JSON.stringify(buildEventStreamEnvelope({
        channel: record?.channel ?? "generation",
        event,
        payload,
        seq: nextSequence(),
        timestamp: record?.ts,
      })));
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
        this.#sendWire(ws, JSON.stringify(buildMissionProgressEventEnvelope(progress, nextSequence())));
      },
    });

    ws.on("message", () => {
      ws.close(1008, "event stream sockets are read-only");
    });
    ws.on("error", () => undefined);
    ws.on("close", () => {
      this.#eventStreamClients.delete(ws);
      this.#runManager.unsubscribeEvents(eventCallback);
      unsubscribeMissionProgress();
    });
  }

  async #handleClientMessage(ws: WebSocket, msg: ClientMessage): Promise<void> {
    if (
      !this.#transcriptClients.has(ws) &&
      (msg.type === "resume_run" || readClientRunId(msg) || readCommandId(msg))
    ) {
      throw new Error("run transcript commands require ?transcript_protocol_version=1");
    }
    switch (msg.type) {
      case "resume_run": {
        this.#resumeRunTranscript(ws, msg);
        return;
      }
      case "start_run": {
        await this.#startRun(ws, msg);
        return;
      }
      case "stop": {
        const clientRunId = msg.client_run_id;
        const existingCommand = this.#runTranscripts.inspectCommand({
          clientRunId,
          commandId: msg.command_id,
          command: msg,
        });
        if (existingCommand) {
          const state = this.#runManager.getState();
          const activeClientRunId =
            state.active && state.runId
              ? this.#runTranscripts.resolveClientRunId(state.runId)
              : null;
          if (existingCommand.outcome === "completed" && activeClientRunId === clientRunId) {
            this.#bindClientScope(ws, clientRunId);
          }
          this.#beginDurableCommand(ws, msg, clientRunId);
          return;
        }
        this.#resolveCommandScope(clientRunId);
        this.#bindClientScope(ws, clientRunId);
        if (!this.#beginDurableCommand(ws, msg, clientRunId)) return;
        const runId = this.#runTranscripts.resolveRunId(clientRunId);
        if (!runId) {
          throw new Error("client_run_id is not associated with an engine run");
        }
        const decision = this.#runManager.stop(runId, msg.command_id);
        this.#sendRunResponse(
          ws,
          {
            type: "ack",
            action: "stop",
            decision,
            client_run_id: clientRunId,
            command_id: msg.command_id,
            run_id: runId,
          },
          clientRunId,
          msg,
        );
        return;
      }
      case "pause":
      case "resume":
      case "inject_hint":
      case "override_gate": {
        const clientRunId = this.#resolveCommandScope(msg.client_run_id);
        if (clientRunId) {
          this.#bindClientScope(ws, clientRunId);
        }
        if (!this.#beginDurableCommand(ws, msg, clientRunId)) return;
        for (const response of await executeInteractiveControlCommand({
          command: msg,
          runManager: this.#runManager,
        })) {
          this.#sendRunResponse(ws, response, clientRunId, msg);
        }
        return;
      }
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
        const clientRunId = this.#resolveCommandScope(msg.client_run_id);
        if (clientRunId) {
          this.#bindClientScope(ws, clientRunId);
        }
        if (!this.#beginDurableCommand(ws, msg, clientRunId)) return;
        for (const response of await executeChatAgentCommand({
          command: msg,
          runManager: this.#runManager,
        })) {
          this.#sendRunResponse(ws, response, clientRunId, msg);
        }
        return;
      }
      case "create_scenario":
      case "create_task":
      case "confirm_scenario":
      case "revise_scenario":
      case "cancel_scenario": {
        const startsScenarioGeneration =
          msg.type === "create_scenario" ||
          msg.type === "create_task" ||
          msg.type === "revise_scenario";
        if (startsScenarioGeneration) {
          this.#send(ws, {
            type: "scenario_generating",
            name: msg.type === "create_task" ? "improvement_task" : "custom_scenario",
          });
        }
        for (const response of await executeInteractiveScenarioCommand({
          command: msg,
          runManager: this.#runManager,
        })) {
          if (startsScenarioGeneration && response.type === "scenario_generating") {
            continue;
          }
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
        if (msg.type !== "whoami") this.#broadcastTranscriptEnvironmentRefresh();
        return;
      }
    }
  }

  async #startRun(
    ws: WebSocket,
    command: Extract<ClientMessage, { type: "start_run" }>,
  ): Promise<void> {
    const requestedClientRunId = command.client_run_id ?? null;
    if (requestedClientRunId) {
      const existingCommand = command.command_id
        ? this.#runTranscripts.inspectCommand({
            clientRunId: requestedClientRunId,
            commandId: command.command_id,
            command,
          })
        : null;
      if (existingCommand) {
        if (
          existingCommand.outcome === "completed" &&
          this.#runTranscripts.resolveRunId(requestedClientRunId)
        ) {
          this.#bindClientScope(ws, requestedClientRunId);
        }
        this.#beginDurableCommand(ws, command, requestedClientRunId);
        return;
      }
      const accepted = this.#runTranscripts.latestFrameOfType(requestedClientRunId, "run_accepted");
      if (accepted) {
        throw new Error("client_run_id is already associated with an existing run");
      }
    }
    if (this.#pendingStart) {
      throw new Error("A run start is already being processed");
    }
    if (this.#runManager.getState().active) {
      throw new Error("A run is already active");
    }
    if (requestedClientRunId) {
      if (!this.#beginDurableCommand(ws, command, requestedClientRunId)) return;
    }

    this.#pendingStart = {
      clientRunId: requestedClientRunId,
      runTranscript: this.#transcriptClients.has(ws),
      ws,
      queuedState: null,
    };
    try {
      const responses = await executeInteractiveControlCommand({
        command,
        runManager: this.#runManager,
      });
      for (const response of responses) {
        if (response.type !== "run_accepted") {
          this.#send(ws, response);
          continue;
        }
        const clientRunId = this.#pendingStart.clientRunId ?? response.run_id;
        this.#runTranscripts.registerRun(clientRunId, response.run_id);
        if (this.#pendingStart.runTranscript) {
          this.#bindClientScope(ws, clientRunId);
        }
        this.#sendRunResponse(ws, response, clientRunId, command);
        const queuedState = this.#pendingStart.queuedState;
        if (queuedState) {
          const stateFrame = this.#runTranscripts.record({
            clientRunId,
            message: buildStateMessage(queuedState),
            runId: response.run_id,
          });
          if (stateFrame) this.#broadcastRetainedFrame(stateFrame);
          this.#pendingStart.queuedState = null;
        }
      }
    } finally {
      this.#pendingStart = null;
    }
  }

  #resumeRunTranscript(
    ws: WebSocket,
    command: Extract<ClientMessage, { type: "resume_run" }>,
  ): void {
    if (!this.#runTranscripts.resolveRunId(command.client_run_id)) {
      this.#sendCommandError(
        ws,
        command,
        "client_run_id is not associated with an engine run",
      );
      return;
    }
    const commandResult = command.command_id
      ? this.#runTranscripts.beginCommand({
          clientRunId: command.client_run_id,
          commandId: command.command_id,
          command,
        })
      : ({ outcome: "proceed" } as const);
    if (commandResult.outcome === "conflict") {
      this.#sendCommandError(
        ws,
        command,
        "command_id is already associated with a different request",
      );
      return;
    }
    if (commandResult.outcome === "pending") {
      this.#sendCommandError(
        ws,
        command,
        "command outcome is pending or unknown; refusing to repeat side effects",
      );
      return;
    }
    this.#bindClientScope(ws, command.client_run_id);
    const frames = this.#runTranscripts.framesAfter(command.client_run_id, command.after_sequence);
    for (const frame of frames) {
      this.#sendWire(ws, frame.wire);
    }
    if (commandResult.outcome === "completed") {
      if (!frames.some((frame) => frame.eventId === commandResult.frame.eventId)) {
        this.#sendWire(ws, commandResult.frame.wire);
      }
      return;
    }
    this.#sendRunResponse(
      ws,
      { type: "ack", action: "resume_run", command_id: command.command_id },
      command.client_run_id,
      command,
    );
  }

  #resolveCommandScope(requestedClientRunId?: string): string | null {
    const state = this.#runManager.getState();
    const activeClientRunId = state.runId
      ? this.#runTranscripts.resolveClientRunId(state.runId)
      : null;
    if (!requestedClientRunId) return activeClientRunId;
    if (!activeClientRunId || activeClientRunId !== requestedClientRunId) {
      throw new Error("client_run_id does not match the current engine run");
    }
    return requestedClientRunId;
  }

  #broadcastRunEvent(event: string, payload: Record<string, unknown>, occurredAt?: string): void {
    const message = buildInteractiveEventMessage(event, payload);
    if (event !== AGENT_PROGRESS_NOTE_EVENT_NAME) {
      this.#broadcastLegacy(message);
    }
    const state = this.#runManager.getState();
    const runId = readEventRunId(event, payload) ?? (state.active ? state.runId : null);
    let clientRunId = runId ? this.#runTranscripts.resolveClientRunId(runId) : null;
    if (!clientRunId && runId && this.#pendingStart) {
      clientRunId = this.#pendingStart.clientRunId ?? runId;
      this.#pendingStart.clientRunId = clientRunId;
      this.#runTranscripts.registerRun(clientRunId, runId);
      if (this.#pendingStart.runTranscript) {
        this.#bindClientScope(this.#pendingStart.ws, clientRunId);
      }
    }
    if (!clientRunId) return;
    const frame = this.#runTranscripts.record({
      clientRunId,
      message,
      occurredAt,
      runId,
    });
    if (frame) {
      if (event === "run_stopped") {
        const commandId = payload.command_id;
        if (typeof commandId === "string" && commandId.length > 0) {
          try {
            this.#runTranscripts.promoteStopCommandTerminalFrame({
              clientRunId,
              commandId,
              command: {
                type: "stop",
                client_run_id: clientRunId,
                command_id: commandId,
              },
              frame,
            });
          } finally {
            this.#broadcastRetainedFrame(frame);
          }
          return;
        }
      }
      this.#broadcastRetainedFrame(frame);
    }
  }

  #broadcastRunState(state: RunManagerState): void {
    const runId = state.runId;
    let clientRunId = runId ? this.#runTranscripts.resolveClientRunId(runId) : null;
    const message = buildStateMessage(state);
    this.#broadcastLegacy(message);
    if (state.active && runId && this.#pendingStart) {
      clientRunId = this.#pendingStart.clientRunId ?? runId;
      this.#pendingStart.clientRunId = clientRunId;
      this.#pendingStart.queuedState = state;
      return;
    }
    if (!clientRunId) {
      for (const client of this.#transcriptClients) {
        if (!this.#clientRunScopes.has(client)) this.#send(client, message);
      }
      return;
    }
    const frame = this.#runTranscripts.record({
      clientRunId,
      message,
      runId,
    });
    if (frame) this.#broadcastRetainedFrame(frame);
  }

  #sendRunResponse(
    ws: WebSocket,
    message: ServerMessage,
    clientRunId: string | null,
    command?: ClientMessage,
  ): void {
    if (!this.#transcriptClients.has(ws)) {
      this.#sendLegacy(ws, message);
      return;
    }
    if (!clientRunId) {
      this.#send(ws, message);
      return;
    }
    const commandId = readCommandId(command ?? null) ?? undefined;
    const runId = readServerMessageRunId(message) ?? this.#runTranscripts.resolveRunId(clientRunId);
    const frame = this.#runTranscripts.record({
      clientRunId,
      commandId,
      message,
      runId,
    });
    if (frame) {
      if (
        command &&
        commandId &&
        this.#runTranscripts.canCompleteCommand({
          clientRunId,
          commandId,
          command,
        })
      ) {
        this.#runTranscripts.completeCommand({
          clientRunId,
          commandId,
          command,
          frame,
        });
      }
      this.#sendWire(ws, frame.wire);
      return;
    }
    this.#send(ws, message);
  }

  #beginDurableCommand(ws: WebSocket, command: ClientMessage, clientRunId: string | null): boolean {
    const commandId = readCommandId(command);
    if (!this.#transcriptClients.has(ws) || !clientRunId || !commandId) return true;
    const result = this.#runTranscripts.beginCommand({
      clientRunId,
      commandId,
      command,
    });
    if (result.outcome === "proceed") return true;
    if (result.outcome === "completed") {
      this.#sendWire(ws, result.frame.wire);
      return false;
    }
    const message =
      result.outcome === "conflict"
        ? "command_id is already associated with a different request"
        : "command outcome is pending or unknown; refusing to repeat side effects";
    this.#sendCommandError(ws, command, message);
    return false;
  }

  #sendCommandError(ws: WebSocket, command: ClientMessage, message: string): void {
    this.#send(ws, {
      type: "error",
      message,
      ...(readClientRunId(command) ? { client_run_id: readClientRunId(command) ?? undefined } : {}),
      ...(readCommandId(command) ? { command_id: readCommandId(command) ?? undefined } : {}),
    });
  }

  #broadcastRetainedFrame(frame: RetainedRunFrame): void {
    for (const client of this.#transcriptClients) {
      const scope = this.#clientRunScopes.get(client);
      if (scope === frame.clientRunId) this.#sendWire(client, frame.wire);
    }
  }

  #broadcastLegacy(message: ServerMessage): void {
    for (const client of this.#interactiveClients) {
      if (!this.#transcriptClients.has(client)) this.#sendLegacy(client, message);
    }
  }

  #broadcastTranscriptEnvironmentRefresh(): void {
    const hello = buildHelloMessage({
      runTranscript: true,
      capabilities: this.#runManager.getInteractiveCapabilities(),
    });
    const environments = buildEnvironmentMessage(this.#runManager.getEnvironmentInfo());
    for (const client of this.#transcriptClients) {
      this.#send(client, hello);
      this.#send(client, environments);
    }
  }

  #reserveInteractiveMessage(messageBytes: number, priority: boolean): boolean {
    if (priority) {
      if (
        this.#pendingPriorityControlCount >= MAX_PENDING_PRIORITY_CONTROL_MESSAGES ||
        this.#pendingPriorityControlBytes + messageBytes > MAX_GLOBAL_PRIORITY_CONTROL_BYTES
      ) return false;
      this.#pendingPriorityControlCount += 1;
      this.#pendingPriorityControlBytes += messageBytes;
      return true;
    }
    if (
      this.#pendingInteractiveMessageCount >= MAX_PENDING_INTERACTIVE_MESSAGES ||
      this.#pendingInteractiveMessageBytes + messageBytes >
        MAX_GLOBAL_INTERACTIVE_MESSAGE_BYTES
    ) return false;
    this.#pendingInteractiveMessageCount += 1;
    this.#pendingInteractiveMessageBytes += messageBytes;
    return true;
  }

  #releaseInteractiveMessage(messageBytes: number, priority: boolean): void {
    if (priority) {
      this.#pendingPriorityControlCount = Math.max(0, this.#pendingPriorityControlCount - 1);
      this.#pendingPriorityControlBytes = Math.max(
        0,
        this.#pendingPriorityControlBytes - messageBytes,
      );
      return;
    }
    this.#pendingInteractiveMessageCount = Math.max(
      0,
      this.#pendingInteractiveMessageCount - 1,
    );
    this.#pendingInteractiveMessageBytes = Math.max(
      0,
      this.#pendingInteractiveMessageBytes - messageBytes,
    );
  }

  #acquireInteractiveMessageHandler(): Promise<void> {
    if (
      this.#activeInteractiveMessageHandlers <
      MAX_CONCURRENT_INTERACTIVE_MESSAGE_HANDLERS
    ) {
      this.#activeInteractiveMessageHandlers += 1;
      return Promise.resolve();
    }
    return new Promise<void>((resolve) => {
      this.#interactiveMessageWaiters.push(() => {
        this.#activeInteractiveMessageHandlers += 1;
        resolve();
      });
    });
  }

  #releaseInteractiveMessageHandler(): void {
    this.#activeInteractiveMessageHandlers = Math.max(
      0,
      this.#activeInteractiveMessageHandlers - 1,
    );
    this.#interactiveMessageWaiters.shift()?.();
  }

  #bindClientScope(ws: WebSocket, clientRunId: string): void {
    this.#clientRunScopes.set(ws, clientRunId);
  }

  #subscribeToRunUpdates(): void {
    if (this.#runSubscriptionsActive) return;
    this.#runSubscriptionsActive = true;
    this.#runManager.subscribeEvents(this.#onRunEvent);
    this.#runManager.subscribeState(this.#onRunState);
  }

  #unsubscribeFromRunUpdates(): void {
    if (!this.#runSubscriptionsActive) return;
    this.#runSubscriptionsActive = false;
    this.#runManager.unsubscribeEvents(this.#onRunEvent);
    this.#runManager.unsubscribeState(this.#onRunState);
  }

  #send(ws: WebSocket, msg: ServerMessage): void {
    this.#sendWire(ws, JSON.stringify(msg));
  }

  #sendLegacy(ws: WebSocket, message: ServerMessage): void {
    this.#send(ws, legacyRunMessage(message));
  }

  #sendLegacyOrCurrent(ws: WebSocket, message: ServerMessage): void {
    if (this.#transcriptClients.has(ws)) {
      this.#send(ws, message);
      return;
    }
    this.#sendLegacy(ws, message);
  }

  #sendWire(ws: WebSocket, wire: string): void {
    if (ws.readyState !== WebSocket.OPEN) return;
    if (
      ws.bufferedAmount + Buffer.byteLength(wire, "utf8") >
      MAX_INTERACTIVE_WEBSOCKET_BUFFERED_BYTES
    ) {
      ws.terminate();
      return;
    }
    ws.send(wire);
  }

  #parseMessage(raw: string): ClientMessage {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    return parseClientMessage(parsed);
  }
}

function rawDataByteLength(data: WebSocket.RawData): number {
  return Array.isArray(data)
    ? data.reduce((total, chunk) => total + chunk.byteLength, 0)
    : data.byteLength;
}

function isPriorityRunControlMessage(message: ClientMessage): boolean {
  return message.type === "stop" || message.type === "pause" || message.type === "resume";
}

function interactiveMessageUsesExpensiveSlot(message: ClientMessage): boolean {
  switch (message.type) {
    case "chat_agent":
    case "create_scenario":
    case "create_task":
    case "confirm_scenario":
    case "revise_scenario":
    case "login":
      return true;
    case "inject_hint":
      return (message.image_attachments?.length ?? 0) > 0;
    default:
      return false;
  }
}

function isTrustedWebSocketOrigin(
  origin: string | string[] | undefined,
  host: string,
  port: number,
): boolean {
  if (origin === undefined) return true;
  const requestedOrigin = Array.isArray(origin) ? origin[0] : origin;
  if (!requestedOrigin) return false;
  try {
    const parsed = new URL(requestedOrigin);
    if (parsed.protocol !== "http:") return false;
    const originPort = parsed.port ? Number(parsed.port) : 80;
    if (originPort !== port) return false;
    const configuredHost = host.toLowerCase().replace(/^\[|\]$/g, "");
    const originHost = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, "");
    if (configuredHost === "0.0.0.0" || configuredHost === "::") {
      return isLoopbackHost(originHost);
    }
    if (isLoopbackHost(configuredHost)) return isLoopbackHost(originHost);
    return originHost === configuredHost;
  } catch {
    return false;
  }
}

function isLoopbackHost(host: string): boolean {
  if (host === "localhost" || host.endsWith(".localhost") || host === "::1") return true;
  const octets = host.split(".");
  return octets.length === 4 && octets[0] === "127" && octets.every((octet) => {
    if (!/^\d{1,3}$/.test(octet)) return false;
    const value = Number(octet);
    return value >= 0 && value <= 255;
  });
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

function buildInteractiveEventMessage(
  event: string,
  payload: Record<string, unknown>,
): ServerMessage {
  if (event === "monitor_alert") {
    return {
      type: "monitor_alert",
      alert_id: stringField(payload.alert_id),
      condition_id: stringField(payload.condition_id),
      condition_name: stringField(payload.condition_name),
      condition_type: stringField(payload.condition_type),
      scope: stringField(payload.scope),
      detail: stringField(payload.detail),
    };
  }
  return { type: "event", event, payload };
}

function readClientRunId(message: ClientMessage | null): string | null {
  if (!message || !("client_run_id" in message)) return null;
  return message.client_run_id ?? null;
}

function readCommandId(message: ClientMessage | null): string | undefined {
  if (!message || !("command_id" in message)) return undefined;
  return message.command_id;
}

function readEventRunId(event: string, payload: Record<string, unknown>): string | null {
  if (typeof payload.run_id === "string" && payload.run_id.length > 0) return payload.run_id;
  if (event === "monitor_alert" && typeof payload.scope === "string") {
    return payload.scope.startsWith("run:") ? payload.scope.slice("run:".length) || null : null;
  }
  return null;
}

function readServerMessageRunId(message: ServerMessage): string | null {
  if ("run_id" in message && typeof message.run_id === "string" && message.run_id.length > 0) {
    return message.run_id;
  }
  if (message.type === "event") return readEventRunId(message.event, message.payload);
  if (message.type === "monitor_alert" && message.scope.startsWith("run:")) {
    return message.scope.slice("run:".length) || null;
  }
  return null;
}

function stringField(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function legacyRunMessage(message: ServerMessage): ServerMessage {
  switch (message.type) {
    case "event":
      return { type: "event", event: message.event, payload: message.payload };
    case "state":
      return {
        type: "state",
        paused: message.paused,
        ...(message.generation === undefined ? {} : { generation: message.generation }),
        ...(message.phase === undefined ? {} : { phase: message.phase }),
      };
    case "run_accepted":
      return {
        type: "run_accepted",
        run_id: message.run_id,
        scenario: message.scenario,
        generations: message.generations,
      };
    case "ack":
      return {
        type: "ack",
        action: message.action,
        ...(message.decision === undefined ? {} : { decision: message.decision }),
      };
    case "chat_response":
      return { type: "chat_response", role: message.role, text: message.text };
    case "error":
      return { type: "error", message: message.message };
    case "monitor_alert":
      return {
        type: "monitor_alert",
        alert_id: message.alert_id,
        condition_id: message.condition_id,
        condition_name: message.condition_name,
        condition_type: message.condition_type,
        scope: message.scope,
        detail: message.detail,
      };
    default:
      return message;
  }
}
