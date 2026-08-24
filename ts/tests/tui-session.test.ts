import { EventEmitter } from "node:events";
import { describe, expect, it, vi } from "vitest";
import WebSocket from "ws";

import type { ClientMessage, ServerMessage } from "../src/server/protocol.js";
import { TuiSession } from "../src/tui/session.js";
import type {
  TuiTransport,
  TuiTransportConnectionEvent,
} from "../src/tui/transport.js";
import {
  assertSecureTuiEndpoint,
  displayTuiEndpoint,
  WebSocketTuiTransport,
  normalizeTuiEndpoint,
  tuiHttpBaseUrl,
} from "../src/tui/transport.js";

class FakeTransport implements TuiTransport {
  readonly endpoint = "ws://example/ws/interactive?transcript_protocol_version=1";
  readonly sent: ClientMessage[] = [];
  readonly #messages = new Set<(message: ServerMessage) => void>();
  readonly #connections = new Set<(event: TuiTransportConnectionEvent) => void>();
  disconnect = vi.fn();

  constructor(private readonly autoHello = true) {}

  async connect(): Promise<void> {
    this.connection({ status: "connected", attempt: 1 });
    if (this.autoHello) {
      this.message({
        type: "hello",
        protocol_version: 2,
        transcript_protocol_version: 1,
        capabilities: [],
      });
    }
  }

  send(message: ClientMessage): void {
    this.sent.push(message);
  }

  onMessage(listener: (message: ServerMessage) => void): () => void {
    this.#messages.add(listener);
    return () => this.#messages.delete(listener);
  }

  onConnection(listener: (event: TuiTransportConnectionEvent) => void): () => void {
    this.#connections.add(listener);
    return () => this.#connections.delete(listener);
  }

  message(message: ServerMessage): void {
    for (const listener of this.#messages) listener(message);
  }

  connection(event: TuiTransportConnectionEvent): void {
    for (const listener of this.#connections) listener(event);
  }
}

class FakeSocket extends EventEmitter {
  readyState: number = WebSocket.CONNECTING;
  readonly sent: string[] = [];
  readonly close = vi.fn(() => {
    this.readyState = WebSocket.CLOSED;
    this.emit("close");
  });

  open(): void {
    this.readyState = WebSocket.OPEN;
    this.emit("open");
  }

  send(value: string): void {
    this.sent.push(value);
  }

  serverMessage(message: ServerMessage): void {
    this.emit("message", Buffer.from(JSON.stringify(message)));
  }

  serverClose(): void {
    this.readyState = WebSocket.CLOSED;
    this.emit("close");
  }
}

describe("TuiSession", () => {
  it("serializes async commands and keeps errors correlated by stable command id", async () => {
    const transport = new FakeTransport();
    const session = new TuiSession(transport);
    await session.start();
    transport.message({
      type: "state",
      active: true,
      paused: false,
      client_run_id: "client-1",
      run_id: "run-1",
    });

    const pause = session.pause();
    const resume = session.resume();
    await vi.waitFor(() => expect(transport.sent).toHaveLength(1));
    const pauseMessage = transport.sent[0]!;
    expect(pauseMessage.type).toBe("pause");
    transport.message({
      type: "error",
      message: "pause rejected",
      command_id: "command_id" in pauseMessage ? pauseMessage.command_id : undefined,
      client_run_id: "client-1",
    });
    await expect(pause).rejects.toThrow("pause rejected");
    await vi.waitFor(() => expect(transport.sent).toHaveLength(2));
    const resumeMessage = transport.sent[1]!;
    transport.message({
      type: "ack",
      action: "resume",
      command_id: "command_id" in resumeMessage ? resumeMessage.command_id : undefined,
      client_run_id: "client-1",
    });
    await expect(resume).resolves.toBeUndefined();
    session.close();
  });

  it("sends stop immediately while an ordinary command is still pending", async () => {
    const transport = new FakeTransport();
    const session = new TuiSession(transport);
    await session.start();
    transport.message({
      type: "hello",
      protocol_version: 2,
      transcript_protocol_version: 1,
      capabilities: ["run_transcript_v1", "safe_run_stop_v1"],
    });
    transport.message({
      type: "state",
      active: true,
      paused: false,
      client_run_id: "client-priority",
      run_id: "run-priority",
    });

    const chat = session.chat("analyst", "stay pending");
    const chatRejected = expect(chat).rejects.toThrow("detached");
    await vi.waitFor(() => expect(transport.sent.at(-1)?.type).toBe("chat_agent"));
    const stop = session.stopActiveRun();
    await vi.waitFor(() => expect(transport.sent.at(-1)?.type).toBe("stop"));
    const stopMessage = transport.sent.at(-1)!;
    transport.message({
      type: "ack",
      action: "stop",
      decision: "requested",
      client_run_id: "client-priority",
      command_id: "command_id" in stopMessage ? stopMessage.command_id : undefined,
      run_id: "run-priority",
    });
    await expect(stop).resolves.toBe("requested");

    session.close();
    await chatRejected;
  });

  it("resumes from the last durable cursor after reconnect without stopping the run", async () => {
    const transport = new FakeTransport();
    const session = new TuiSession(transport);
    await session.start();
    transport.message({
      type: "hello",
      protocol_version: 2,
      transcript_protocol_version: 1,
      capabilities: ["run_transcript_v1", "safe_run_stop_v1"],
    });
    transport.message({
      type: "event",
      event: "generation_started",
      payload: { generation: 2 },
      event_id: "event-7",
      sequence: 7,
      client_run_id: "client-1",
      run_id: "run-1",
    });
    transport.message({
      type: "state",
      active: true,
      paused: false,
      client_run_id: "client-1",
      run_id: "run-1",
    });
    expect(transport.sent.at(-1)).toMatchObject({
      type: "resume_run",
      client_run_id: "client-1",
      after_sequence: 7,
    });

    transport.connection({ status: "reconnecting", attempt: 2 });
    transport.connection({ status: "connected", attempt: 3 });
    transport.message({
      type: "hello",
      protocol_version: 2,
      transcript_protocol_version: 1,
      capabilities: ["run_transcript_v1", "safe_run_stop_v1"],
    });
    transport.message({
      type: "state",
      active: true,
      paused: false,
      client_run_id: "client-1",
      run_id: "run-1",
    });
    expect(transport.sent.at(-1)).toMatchObject({
      type: "resume_run",
      after_sequence: 7,
    });
    session.close();
    expect(transport.disconnect).toHaveBeenCalledOnce();
    expect(transport.sent.some((message) => message.type === "stop")).toBe(false);
  });

  it("resumes a second run with that run's independent transcript cursor", async () => {
    const transport = new FakeTransport();
    const session = new TuiSession(transport);
    await session.start();
    transport.message({
      type: "hello",
      protocol_version: 2,
      transcript_protocol_version: 1,
      capabilities: ["run_transcript_v1"],
    });
    transport.message({
      type: "event",
      event: "run_completed",
      payload: { completed_generations: 1 },
      event_id: "completed-1",
      sequence: 10,
      client_run_id: "client-1",
      run_id: "run-1",
    });
    transport.message({
      type: "state",
      active: false,
      paused: false,
      client_run_id: "client-1",
      run_id: "run-1",
    });
    expect(transport.sent.at(-1)).toMatchObject({
      type: "resume_run",
      client_run_id: "client-1",
      after_sequence: 10,
    });

    transport.message({
      type: "state",
      active: true,
      paused: false,
      client_run_id: "client-2",
      run_id: "run-2",
      event_id: "state-2",
      sequence: 1,
    });
    transport.message({
      type: "run_accepted",
      run_id: "run-2",
      client_run_id: "client-2",
      scenario: "incident",
      generations: 1,
      event_id: "accepted-2",
      sequence: 2,
    });
    transport.connection({ status: "reconnecting", attempt: 2 });
    transport.connection({ status: "connected", attempt: 3 });
    transport.message({
      type: "hello",
      protocol_version: 2,
      transcript_protocol_version: 1,
      capabilities: ["run_transcript_v1"],
    });
    transport.message({
      type: "state",
      active: true,
      paused: false,
      client_run_id: "client-2",
      run_id: "run-2",
    });
    expect(transport.sent.at(-1)).toMatchObject({
      type: "resume_run",
      client_run_id: "client-2",
      after_sequence: 2,
    });
    session.close();
  });

  it("validates positive iteration counts before sending", async () => {
    const transport = new FakeTransport();
    const session = new TuiSession(transport);
    await session.start();
    await expect(session.startRun("grid", 0)).rejects.toThrow(/positive integer/);
    expect(transport.sent).toHaveLength(0);
    session.close();
  });

  it("keeps submitted credentials out of presentation state and errors", async () => {
    const transport = new FakeTransport();
    const session = new TuiSession(transport);
    await session.start();
    const secret = "sk-ant-never-render-this";
    const login = session.login("anthropic", secret, "claude-test");
    await vi.waitFor(() => expect(transport.sent.at(-1)?.type).toBe("login"));
    transport.message({
      type: "auth_status",
      provider: "anthropic",
      authenticated: true,
      model: "claude-test",
    });
    await expect(login).resolves.toBeUndefined();
    expect(JSON.stringify(session.viewModel)).not.toContain(secret);
    session.close();
  });

  it("settles scenario failures immediately and releases the serialized command queue", async () => {
    const transport = new FakeTransport();
    const session = new TuiSession(transport);
    await session.start();

    const creation = session.createScenario("Create an incident response scenario");
    await vi.waitFor(() => expect(transport.sent.at(-1)?.type).toBe("create_scenario"));
    transport.message({
      type: "scenario_error",
      stage: "generation",
      message: "scenario generator unavailable",
    });
    await expect(creation).rejects.toThrow("scenario generator unavailable");
    expect(session.isBusy).toBe(false);

    const whoami = session.whoami();
    await vi.waitFor(() => expect(transport.sent.at(-1)?.type).toBe("whoami"));
    transport.message({ type: "auth_status", provider: "deterministic", authenticated: true });
    await expect(whoami).resolves.toMatchObject({ provider: "deterministic" });
    session.close();
  });

  it("settles uncorrelated auth errors using the single serialized request", async () => {
    const transport = new FakeTransport();
    const session = new TuiSession(transport);
    await session.start();
    const login = session.login("anthropic", "invalid");
    await vi.waitFor(() => expect(transport.sent.at(-1)?.type).toBe("login"));
    transport.message({ type: "error", message: "authentication rejected" });
    await expect(login).rejects.toThrow("authentication rejected");
    expect(session.isBusy).toBe(false);
    session.close();
  });

  it("refuses commands after a legacy hello omits transcript negotiation", async () => {
    const transport = new FakeTransport(false);
    const session = new TuiSession(transport);
    const starting = session.start();
    await expect(session.whoami()).rejects.toThrow("required transcript protocol");
    expect(transport.sent).toHaveLength(0);
    transport.message({ type: "hello", protocol_version: 2 });
    await expect(starting).rejects.toThrow("required transcript protocol");
    await expect(session.whoami()).rejects.toThrow("required transcript protocol");
    expect(transport.sent).toEqual([]);
    session.close();
  });
});

describe("WebSocket TUI transport", () => {
  it("normalizes local and remote endpoints onto the versioned transcript protocol", () => {
    expect(normalizeTuiEndpoint("http://127.0.0.1:8000")).toBe(
      "ws://127.0.0.1:8000/ws/interactive?transcript_protocol_version=1",
    );
    expect(normalizeTuiEndpoint("https://host.example/custom?token=opaque")).toBe(
      "wss://host.example/custom?token=opaque&transcript_protocol_version=1",
    );
    expect(tuiHttpBaseUrl("wss://host.example/ws/interactive?token=opaque")).toBe(
      "https://host.example",
    );
    expect(displayTuiEndpoint(
      "wss://operator:password@host.example/ws/interactive?token=opaque&region=us",
    )).toBe(
      "wss://host.example/ws/interactive?token=REDACTED&region=us&transcript_protocol_version=1",
    );
    expect(displayTuiEndpoint("wss://host.example/ws#access%5Ftoken=opaque"))
      .not.toContain("opaque");
    const authEndpoint = displayTuiEndpoint(
      "wss://host.example/ws?auth=auth-value&bearer=bearer-value&region=us",
    );
    expect(authEndpoint).not.toContain("auth-value");
    expect(authEndpoint).not.toContain("bearer-value");
    expect(authEndpoint).toContain("auth=REDACTED&bearer=REDACTED&region=us");
  });

  it("requires encrypted transport for non-loopback endpoints", () => {
    expect(() => assertSecureTuiEndpoint("ws://remote.example/ws/interactive")).toThrow(
      "must use wss or https",
    );
    expect(() => new WebSocketTuiTransport("ws://remote.example/ws/interactive")).toThrow(
      "must use wss or https",
    );
    expect(() => assertSecureTuiEndpoint("ws://127.20.30.40/ws/interactive")).not.toThrow();
    expect(() => assertSecureTuiEndpoint("ws://[::1]/ws/interactive")).not.toThrow();
  });

  it("reconnects with backoff and keeps parsed messages on one typed transport", async () => {
    vi.useFakeTimers();
    try {
      const sockets: FakeSocket[] = [];
      const events: TuiTransportConnectionEvent[] = [];
      const messages: ServerMessage[] = [];
      const transport = new WebSocketTuiTransport("ws://127.0.0.1/ws/interactive", {
        reconnectBaseMs: 10,
        reconnectMaxMs: 20,
        createSocket: () => {
          const socket = new FakeSocket();
          sockets.push(socket);
          return socket as unknown as WebSocket;
        },
      });
      transport.onConnection((event) => events.push(event));
      transport.onMessage((message) => messages.push(message));

      const connected = transport.connect();
      expect(events.at(-1)).toMatchObject({ status: "connecting", attempt: 1 });
      sockets[0]!.open();
      await connected;
      transport.send({ type: "whoami" });
      expect(JSON.parse(sockets[0]!.sent[0]!)).toEqual({ type: "whoami" });
      sockets[0]!.serverMessage({
        type: "hello",
        protocol_version: 2,
        transcript_protocol_version: 1,
        capabilities: ["run_transcript_v1"],
      });
      expect(messages.at(-1)?.type).toBe("hello");

      sockets[0]!.serverClose();
      expect(events.at(-1)).toMatchObject({ status: "reconnecting", attempt: 1 });
      await vi.advanceTimersByTimeAsync(10);
      expect(sockets).toHaveLength(2);
      expect(events.at(-1)).toMatchObject({ status: "reconnecting", attempt: 2 });
      sockets[1]!.open();
      expect(events.at(-1)).toMatchObject({ status: "connected", attempt: 2 });

      transport.disconnect();
      expect(events.at(-1)?.status).toBe("disconnected");
      await vi.advanceTimersByTimeAsync(100);
      expect(sockets).toHaveLength(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("surfaces malformed server messages without exposing raw payloads", async () => {
    const socket = new FakeSocket();
    const events: TuiTransportConnectionEvent[] = [];
    const transport = new WebSocketTuiTransport("ws://127.0.0.1/ws/interactive", {
      createSocket: () => socket as unknown as WebSocket,
    });
    transport.onConnection((event) => events.push(event));
    const connected = transport.connect();
    socket.open();
    await connected;
    socket.emit("message", Buffer.from('{"apiKey":"sk-private"}'));
    expect(events.at(-1)).toMatchObject({ status: "failed" });
    expect(events.at(-1)?.error).not.toContain("sk-private");
    transport.disconnect();
  });

  it("settles an initial connection attempt when the operator detaches", async () => {
    const socket = new FakeSocket();
    const transport = new WebSocketTuiTransport("ws://127.0.0.1/ws/interactive", {
      createSocket: () => socket as unknown as WebSocket,
    });
    const connected = transport.connect();
    transport.disconnect();
    await expect(connected).rejects.toThrow("disconnected before connecting");
    expect(socket.close).toHaveBeenCalledOnce();
  });
});
