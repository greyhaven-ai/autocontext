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

  async connect(): Promise<void> {
    this.connection({ status: "connected", attempt: 1 });
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

  it("resumes from the last durable cursor after reconnect without stopping the run", async () => {
    const transport = new FakeTransport();
    const session = new TuiSession(transport);
    await session.start();
    transport.message({
      type: "hello",
      protocol_version: 1,
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
  });

  it("reconnects with backoff and keeps parsed messages on one typed transport", async () => {
    vi.useFakeTimers();
    try {
      const sockets: FakeSocket[] = [];
      const events: TuiTransportConnectionEvent[] = [];
      const messages: ServerMessage[] = [];
      const transport = new WebSocketTuiTransport("ws://example/ws/interactive", {
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
        protocol_version: 1,
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
    const transport = new WebSocketTuiTransport("ws://example/ws/interactive", {
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
    const transport = new WebSocketTuiTransport("ws://example/ws/interactive", {
      createSocket: () => socket as unknown as WebSocket,
    });
    const connected = transport.connect();
    transport.disconnect();
    await expect(connected).rejects.toThrow("disconnected before connecting");
    expect(socket.close).toHaveBeenCalledOnce();
  });
});
