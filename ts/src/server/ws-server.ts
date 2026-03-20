/**
 * Interactive WebSocket server for the TS control plane (AC-347 Task 25).
 */

import { createServer, type Server as HttpServer } from "node:http";
import { WebSocketServer, WebSocket } from "ws";
import type { AddressInfo } from "node:net";
import { parseClientMessage } from "./protocol.js";
import type { ClientMessage, ServerMessage } from "./protocol.js";
import { RunManager } from "./run-manager.js";
import type { RunManagerState } from "./run-manager.js";
import type { EventCallback } from "../loop/events.js";

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
      if (req.url === "/health") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: true }));
        return;
      }
      res.writeHead(404, { "Content-Type": "text/plain" });
      res.end("Not found");
    });

    const wsServer = new WebSocketServer({ noServer: true });
    httpServer.on("upgrade", (req, socket, head) => {
      if (req.url !== "/ws/interactive") {
        socket.write("HTTP/1.1 404 Not Found\r\n\r\n");
        socket.destroy();
        return;
      }
      wsServer.handleUpgrade(req, socket, head, (ws: WebSocket) => {
        wsServer.emit("connection", ws, req);
      });
    });

    wsServer.on("connection", (ws: WebSocket) => {
      this.attachClient(ws);
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
      try {
        const msg = this.parseMessage(data.toString());
        await this.handleClientMessage(ws, msg);
      } catch (err) {
        this.send(ws, {
          type: "error",
          message: err instanceof Error ? err.message : String(err),
        });
      }
    });

    ws.on("close", () => {
      this.runManager.unsubscribeEvents(eventCallback);
      this.runManager.unsubscribeState(stateCallback);
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
      case "create_scenario":
      case "confirm_scenario":
      case "revise_scenario":
      case "cancel_scenario":
        this.send(ws, {
          type: "scenario_error",
          message: "Scenario creation is not wired into the TS server yet.",
          stage: "server",
        });
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
