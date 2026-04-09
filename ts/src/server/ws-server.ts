/**
 * Interactive WebSocket server for the TS control plane (AC-347 Task 25).
 */

import { createServer, type IncomingMessage, type Server as HttpServer, type ServerResponse } from "node:http";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { WebSocketServer, WebSocket } from "ws";
import type { AddressInfo } from "node:net";
import { URL, fileURLToPath } from "node:url";
import { MissionEventEmitter, type MissionCreatedEvent, type MissionStepEvent, type MissionStatusChangedEvent, type MissionVerifiedEvent } from "../mission/events.js";
import { CampaignManager } from "../mission/campaign.js";
import { MissionManager } from "../mission/manager.js";
import { buildMissionStatusPayload, requireMission, runMissionLoop, writeMissionCheckpoint } from "../mission/control-plane.js";
import { buildCampaignApiRoutes } from "./campaign-api.js";
import { buildMissionApiRoutes } from "./mission-api.js";
import { buildSimulationApiRoutes } from "./simulation-api.js";
import { renderDashboardHtml } from "./simulation-dashboard.js";
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
  private readonly runManager: RunManager;
  private readonly missionManager: MissionManager;
  private readonly campaignManager: CampaignManager;
  private readonly missionEvents: MissionEventEmitter;
  private readonly host: string;
  private readonly requestedPort: number;
  // Dashboard removed (AC-467) — server is API-only
  private httpServer: HttpServer | null = null;
  private wsServer: WebSocketServer | null = null;
  private boundPort = 0;

  constructor(opts: InteractiveServerOpts) {
    this.runManager = opts.runManager;
    this.missionEvents = new MissionEventEmitter();
    this.missionManager = new MissionManager(this.runManager["opts"].dbPath, {
      events: this.missionEvents,
    });
    this.campaignManager = new CampaignManager(this.missionManager);
    this.host = opts.host ?? "127.0.0.1";
    this.requestedPort = opts.port ?? 8000;
    // Dashboard removed (AC-467)
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
      void this.handleHttpRequest(req, res).catch((err) => {
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
      httpServer.once("error", (err: NodeJS.ErrnoException) => {
        if (err.code === "EADDRINUSE") {
          reject(new PortInUseError(this.requestedPort));
        } else {
          reject(err);
        }
      });
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

  private async handleHttpRequest(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const requestUrl = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`);
    const url = requestUrl.pathname;
    const method = req.method ?? "GET";
    const campaignApi = buildCampaignApiRoutes(this.campaignManager);
    const missionApi = buildMissionApiRoutes(this.missionManager, this.runManager["opts"].runsRoot);
    const simulationApi = buildSimulationApiRoutes(this.runManager["opts"].knowledgeRoot);

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

    // GET /api/simulations
    if (method === "GET" && url === "/api/simulations") {
      json(200, simulationApi.listSimulations());
      return;
    }

    // GET /api/simulations/:name
    const simulationMatch = url.match(/^\/api\/simulations\/([^/]+)$/);
    if (method === "GET" && simulationMatch) {
      const [, rawName] = simulationMatch;
      const simulation = simulationApi.getSimulation(
        decodeURIComponent(rawName!),
      );
      if (!simulation) {
        json(404, { error: `Simulation '${rawName}' not found` });
        return;
      }
      json(200, simulation);
      return;
    }

    // GET /api/simulations/:name/dashboard
    const simulationDashboardMatch = url.match(
      /^\/api\/simulations\/([^/]+)\/dashboard$/,
    );
    if (method === "GET" && simulationDashboardMatch) {
      const [, rawName] = simulationDashboardMatch;
      const dashboard = simulationApi.getDashboardData(
        decodeURIComponent(rawName!),
      );
      if (!dashboard) {
        json(404, { error: `Simulation '${rawName}' not found` });
        return;
      }
      json(200, dashboard);
      return;
    }

    // GET /api/campaigns
    if (method === "GET" && url === "/api/campaigns") {
      json(200, campaignApi.listCampaigns(requestUrl.searchParams.get("status") ?? undefined));
      return;
    }

    // POST /api/campaigns
    if (method === "POST" && url === "/api/campaigns") {
      const body = await this.readJsonBody(req);
      const result = campaignApi.createCampaign({
        name: String(body.name ?? ""),
        goal: String(body.goal ?? ""),
        budgetTokens:
          typeof body.budgetTokens === "number" ? body.budgetTokens : undefined,
        budgetCost:
          typeof body.budgetCost === "number" ? body.budgetCost : undefined,
      });
      json(200, result);
      return;
    }

    // GET /api/campaigns/:id
    const campaignMatch = url.match(/^\/api\/campaigns\/([^/]+)$/);
    if (method === "GET" && campaignMatch) {
      const [, campaignId] = campaignMatch;
      const campaign = campaignApi.getCampaign(campaignId!);
      if (!campaign) {
        json(404, { error: `Campaign '${campaignId}' not found` });
        return;
      }
      json(200, campaign);
      return;
    }

    // GET /api/campaigns/:id/progress
    const campaignProgressMatch = url.match(/^\/api\/campaigns\/([^/]+)\/progress$/);
    if (method === "GET" && campaignProgressMatch) {
      const [, campaignId] = campaignProgressMatch;
      const progress = campaignApi.getCampaignProgress(campaignId!);
      if (!progress) {
        json(404, { error: `Campaign '${campaignId}' not found` });
        return;
      }
      json(200, {
        progress,
        budget: this.campaignManager.budgetUsage(campaignId!),
      });
      return;
    }

    // POST /api/campaigns/:id/missions
    const campaignMissionMatch = url.match(/^\/api\/campaigns\/([^/]+)\/missions$/);
    if (method === "POST" && campaignMissionMatch) {
      const [, campaignId] = campaignMissionMatch;
      const body = await this.readJsonBody(req);
      campaignApi.addMission(campaignId!, {
        missionId: String(body.missionId ?? ""),
        priority:
          typeof body.priority === "number" ? body.priority : undefined,
        dependsOn: Array.isArray(body.dependsOn)
          ? body.dependsOn.filter((value): value is string => typeof value === "string")
          : undefined,
      });
      json(200, { ok: true });
      return;
    }

    // POST /api/campaigns/:id/(pause|resume|cancel)
    const campaignActionMatch = url.match(/^\/api\/campaigns\/([^/]+)\/(pause|resume|cancel)$/);
    if (method === "POST" && campaignActionMatch) {
      const [, campaignId, action] = campaignActionMatch;
      const campaign = campaignApi.getCampaign(campaignId!);
      if (!campaign) {
        json(404, { error: `Campaign '${campaignId}' not found` });
        return;
      }
      const status = action === "pause"
        ? "paused"
        : action === "resume"
          ? "active"
          : "canceled";
      campaignApi.updateStatus(campaignId!, status);
      json(200, {
        ok: true,
        status,
      });
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
      const mission = missionApi.getMission(missionId!);
      if (!mission) {
        json(404, { error: `Mission '${missionId}' not found` });
        return;
      }
      json(200, mission);
      return;
    }

    // GET /api/missions/:id/steps
    const missionStepsMatch = url.match(/^\/api\/missions\/([^/]+)\/steps$/);
    if (method === "GET" && missionStepsMatch) {
      const [, missionId] = missionStepsMatch;
      if (!this.missionManager.get(missionId!)) {
        json(404, { error: `Mission '${missionId}' not found` });
        return;
      }
      json(200, missionApi.getMissionSteps(missionId!));
      return;
    }

    // GET /api/missions/:id/subgoals
    const missionSubgoalsMatch = url.match(/^\/api\/missions\/([^/]+)\/subgoals$/);
    if (method === "GET" && missionSubgoalsMatch) {
      const [, missionId] = missionSubgoalsMatch;
      if (!this.missionManager.get(missionId!)) {
        json(404, { error: `Mission '${missionId}' not found` });
        return;
      }
      json(200, missionApi.getMissionSubgoals(missionId!));
      return;
    }

    // GET /api/missions/:id/budget
    const missionBudgetMatch = url.match(/^\/api\/missions\/([^/]+)\/budget$/);
    if (method === "GET" && missionBudgetMatch) {
      const [, missionId] = missionBudgetMatch;
      if (!this.missionManager.get(missionId!)) {
        json(404, { error: `Mission '${missionId}' not found` });
        return;
      }
      json(200, missionApi.getMissionBudget(missionId!));
      return;
    }

    // GET /api/missions/:id/artifacts
    const missionArtifactsMatch = url.match(/^\/api\/missions\/([^/]+)\/artifacts$/);
    if (method === "GET" && missionArtifactsMatch) {
      const [, missionId] = missionArtifactsMatch;
      if (!this.missionManager.get(missionId!)) {
        json(404, { error: `Mission '${missionId}' not found` });
        return;
      }
      json(200, missionApi.getMissionArtifacts(missionId!));
      return;
    }

    // POST /api/missions/:id/(run|pause|resume|cancel)
    const missionActionMatch = url.match(/^\/api\/missions\/([^/]+)\/(run|pause|resume|cancel)$/);
    if (method === "POST" && missionActionMatch) {
      const [, missionId, action] = missionActionMatch;
      const mission = this.missionManager.get(missionId!);
      if (!mission) {
        json(404, { error: `Mission '${missionId}' not found` });
        return;
      }

      if (action === "run") {
        const body = await this.readJsonBody(req);
        const maxIterations = typeof body.maxIterations === "number"
          ? body.maxIterations
          : Number.parseInt(String(body.maxIterations ?? "1"), 10);
        const missionType = (mission.metadata as Record<string, unknown> | undefined)?.missionType;
        const provider = missionType !== "code" && missionType !== "proof"
          ? this.runManager.buildMissionProvider()
          : undefined;
        const payload = await runMissionLoop(
          this.missionManager,
          missionId!,
          this.runManager["opts"].runsRoot,
          this.runManager["opts"].knowledgeRoot,
          {
            maxIterations: Number.isInteger(maxIterations) && maxIterations > 0 ? maxIterations : 1,
            stepDescription: typeof body.stepDescription === "string" ? body.stepDescription : undefined,
            provider,
          },
        );
        json(200, payload);
        return;
      }

      requireMission(this.missionManager, mission.id);
      if (action === "pause") {
        this.missionManager.pause(mission.id);
      } else if (action === "resume") {
        this.missionManager.resume(mission.id);
      } else {
        this.missionManager.cancel(mission.id);
      }
      const checkpointPath = writeMissionCheckpoint(
        this.missionManager,
        mission.id,
        this.runManager["opts"].runsRoot,
      );
      json(200, {
        ...buildMissionStatusPayload(this.missionManager, mission.id),
        checkpointPath,
      });
      return;
    }

    // 404 fallback
    json(404, { error: "Not found" });
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

  private async readJsonBody(req: IncomingMessage): Promise<Record<string, unknown>> {
    const chunks: Buffer[] = [];
    for await (const chunk of req) {
      chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    }
    if (chunks.length === 0) {
      return {};
    }
    return JSON.parse(Buffer.concat(chunks).toString("utf-8")) as Record<string, unknown>;
  }

  private buildMissionProgress(missionId: string, latestStep?: string): Extract<ServerMessage, { type: "mission_progress" }> | null {
    const mission = this.missionManager.get(missionId);
    if (!mission) {
      return null;
    }
    const steps = this.missionManager.steps(missionId);
    const budget = this.missionManager.budgetUsage(missionId);
    return MissionProgressMsgSchema.parse({
      type: "mission_progress",
      missionId,
      status: mission.status,
      stepsCompleted: steps.length,
      latestStep: latestStep ?? steps.at(-1)?.description,
      budgetUsed: budget.stepsUsed,
      budgetMax: budget.maxSteps,
    });
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

    this.campaignManager.close();
    this.missionManager.close();
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

    const sendMissionProgress = (missionId: string, latestStep?: string) => {
      const progress = this.buildMissionProgress(missionId, latestStep);
      if (progress) {
        this.send(ws, progress);
      }
    };
    const onMissionCreated = (event: MissionCreatedEvent) => sendMissionProgress(event.missionId);
    const onMissionStep = (event: MissionStepEvent) => sendMissionProgress(event.missionId, event.description);
    const onMissionStatusChanged = (event: MissionStatusChangedEvent) => sendMissionProgress(event.missionId);
    const onMissionVerified = (event: MissionVerifiedEvent) => sendMissionProgress(event.missionId);
    this.missionEvents.on("mission_created", onMissionCreated);
    this.missionEvents.on("mission_step", onMissionStep);
    this.missionEvents.on("mission_status_changed", onMissionStatusChanged);
    this.missionEvents.on("mission_verified", onMissionVerified);

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
      this.missionEvents.off("mission_created", onMissionCreated);
      this.missionEvents.off("mission_step", onMissionStep);
      this.missionEvents.off("mission_status_changed", onMissionStatusChanged);
      this.missionEvents.off("mission_verified", onMissionVerified);
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

    const sendMissionProgress = (missionId: string, latestStep?: string) => {
      const progress = this.buildMissionProgress(missionId, latestStep);
      if (!progress || ws.readyState !== WebSocket.OPEN) {
        return;
      }
      ws.send(JSON.stringify({
        channel: "mission",
        event: "mission_progress",
        payload: progress,
        ts: new Date().toISOString(),
        v: 1,
      }));
    };
    const onMissionCreated = (event: MissionCreatedEvent) => sendMissionProgress(event.missionId);
    const onMissionStep = (event: MissionStepEvent) => sendMissionProgress(event.missionId, event.description);
    const onMissionStatusChanged = (event: MissionStatusChangedEvent) => sendMissionProgress(event.missionId);
    const onMissionVerified = (event: MissionVerifiedEvent) => sendMissionProgress(event.missionId);
    this.missionEvents.on("mission_created", onMissionCreated);
    this.missionEvents.on("mission_step", onMissionStep);
    this.missionEvents.on("mission_status_changed", onMissionStatusChanged);
    this.missionEvents.on("mission_verified", onMissionVerified);

    ws.on("close", () => {
      this.runManager.unsubscribeEvents(eventCallback);
      this.missionEvents.off("mission_created", onMissionCreated);
      this.missionEvents.off("mission_step", onMissionStep);
      this.missionEvents.off("mission_status_changed", onMissionStatusChanged);
      this.missionEvents.off("mission_verified", onMissionVerified);
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
      case "login": {
        const { handleTuiLogin, handleTuiWhoami, resolveTuiAuthSelection } = await import("./tui-auth.js");
        const { resolveConfigDir } = await import("../config/index.js");
        const configDir = resolveConfigDir();
        const loginResult = await handleTuiLogin(configDir, msg.provider, msg.apiKey, msg.model, msg.baseUrl);
        if (!loginResult.saved) {
          throw new Error(loginResult.validationWarning ?? `Unable to log in to ${msg.provider}`);
        }
        const selection = resolveTuiAuthSelection(configDir, loginResult.provider);
        if (selection.provider !== "none") {
          this.runManager.setActiveProvider({
            providerType: selection.provider,
            ...(selection.apiKey ? { apiKey: selection.apiKey } : {}),
            ...(selection.model ? { model: selection.model } : {}),
            ...(selection.baseUrl ? { baseUrl: selection.baseUrl } : {}),
          });
        }
        const status = handleTuiWhoami(configDir, loginResult.provider);
        this.send(ws, {
          type: "auth_status",
          provider: status.provider,
          authenticated: status.authenticated,
          ...(status.model ? { model: status.model } : {}),
          ...(status.configuredProviders ? { configuredProviders: status.configuredProviders } : {}),
        });
        return;
      }
      case "logout": {
        const { handleTuiLogout, handleTuiWhoami, resolveTuiAuthSelection } = await import("./tui-auth.js");
        const { resolveConfigDir } = await import("../config/index.js");
        const configDir = resolveConfigDir();
        const currentProvider = this.runManager.getActiveProviderType() ?? undefined;
        const removedProvider = msg.provider?.trim().toLowerCase();
        handleTuiLogout(configDir, msg.provider);
        if (!msg.provider) {
          this.runManager.clearActiveProvider();
        } else {
          const preferredProvider =
            currentProvider === removedProvider ? removedProvider : currentProvider;
          const selection = resolveTuiAuthSelection(configDir, preferredProvider);
          if (selection.provider === "none") {
            this.runManager.clearActiveProvider();
          } else {
            this.runManager.setActiveProvider({
              providerType: selection.provider,
              ...(selection.apiKey ? { apiKey: selection.apiKey } : {}),
              ...(selection.model ? { model: selection.model } : {}),
              ...(selection.baseUrl ? { baseUrl: selection.baseUrl } : {}),
            });
          }
        }
        const status = handleTuiWhoami(
          configDir,
          msg.provider ? (currentProvider === removedProvider ? removedProvider : currentProvider) : undefined,
        );
        this.send(ws, {
          type: "auth_status",
          provider: status.provider,
          authenticated: status.authenticated,
          ...(status.model ? { model: status.model } : {}),
          ...(status.configuredProviders ? { configuredProviders: status.configuredProviders } : {}),
        });
        return;
      }
      case "switch_provider": {
        const { handleTuiSwitchProvider, resolveTuiAuthSelection } = await import("./tui-auth.js");
        const { resolveConfigDir } = await import("../config/index.js");
        const configDir = resolveConfigDir();
        const status = handleTuiSwitchProvider(configDir, msg.provider);
        const selection = resolveTuiAuthSelection(configDir, msg.provider);
        if (selection.provider === "none") {
          this.runManager.clearActiveProvider();
        } else {
          this.runManager.setActiveProvider({
            providerType: selection.provider,
            ...(selection.apiKey ? { apiKey: selection.apiKey } : {}),
            ...(selection.model ? { model: selection.model } : {}),
            ...(selection.baseUrl ? { baseUrl: selection.baseUrl } : {}),
          });
        }
        this.send(ws, {
          type: "auth_status",
          provider: status.provider,
          authenticated: status.authenticated,
          ...(status.model ? { model: status.model } : {}),
          ...(status.configuredProviders ? { configuredProviders: status.configuredProviders } : {}),
        });
        return;
      }
      case "whoami": {
        const { handleTuiWhoami } = await import("./tui-auth.js");
        const { resolveConfigDir } = await import("../config/index.js");
        const configDir = resolveConfigDir();
        const status = handleTuiWhoami(configDir, this.runManager.getActiveProviderType() ?? undefined);
        this.send(ws, {
          type: "auth_status",
          provider: status.provider,
          authenticated: status.authenticated,
          ...(status.model ? { model: status.model } : {}),
          ...(status.configuredProviders ? { configuredProviders: status.configuredProviders } : {}),
        });
        return;
      }
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
