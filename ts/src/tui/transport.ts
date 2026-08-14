import WebSocket from "ws";

import {
  TRANSCRIPT_PROTOCOL_QUERY_PARAM,
  TRANSCRIPT_PROTOCOL_QUERY_VALUE,
  parseServerMessage,
  type ClientMessage,
  type ServerMessage,
} from "../server/protocol.js";

export interface TuiTransportConnectionEvent {
  readonly status: "connecting" | "connected" | "reconnecting" | "disconnected" | "failed";
  readonly attempt: number;
  readonly error?: string;
}

export interface TuiTransport {
  readonly endpoint: string;
  connect(): Promise<void>;
  disconnect(): void;
  send(message: ClientMessage): void;
  onMessage(listener: (message: ServerMessage) => void): () => void;
  onConnection(listener: (event: TuiTransportConnectionEvent) => void): () => void;
}

export interface WebSocketTuiTransportOptions {
  readonly reconnectBaseMs?: number;
  readonly reconnectMaxMs?: number;
  readonly createSocket?: (url: string) => WebSocket;
}

export class WebSocketTuiTransport implements TuiTransport {
  readonly endpoint: string;
  readonly #reconnectBaseMs: number;
  readonly #reconnectMaxMs: number;
  readonly #createSocket: (url: string) => WebSocket;
  readonly #messageListeners = new Set<(message: ServerMessage) => void>();
  readonly #connectionListeners = new Set<(event: TuiTransportConnectionEvent) => void>();
  #socket: WebSocket | null = null;
  #reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  #desiredOpen = false;
  #attempt = 0;
  #initialConnect: { resolve: () => void; reject: (error: Error) => void } | null = null;

  constructor(endpoint: string, options: WebSocketTuiTransportOptions = {}) {
    const normalizedEndpoint = normalizeTuiEndpoint(endpoint);
    assertSecureTuiEndpoint(normalizedEndpoint);
    this.endpoint = normalizedEndpoint;
    this.#reconnectBaseMs = options.reconnectBaseMs ?? 250;
    this.#reconnectMaxMs = options.reconnectMaxMs ?? 5_000;
    this.#createSocket = options.createSocket ?? ((url) => new WebSocket(url));
  }

  connect(): Promise<void> {
    if (this.#desiredOpen && this.#socket?.readyState === WebSocket.OPEN) {
      return Promise.resolve();
    }
    this.#desiredOpen = true;
    if (!this.#initialConnect) {
      const promise = new Promise<void>((resolve, reject) => {
        this.#initialConnect = { resolve, reject };
      });
      this.#open(false);
      return promise;
    }
    return new Promise<void>((resolve, reject) => {
      const prior = this.#initialConnect!;
      this.#initialConnect = {
        resolve: () => {
          prior.resolve();
          resolve();
        },
        reject: (error) => {
          prior.reject(error);
          reject(error);
        },
      };
    });
  }

  disconnect(): void {
    this.#desiredOpen = false;
    if (this.#reconnectTimer) clearTimeout(this.#reconnectTimer);
    this.#reconnectTimer = null;
    const initialConnect = this.#initialConnect;
    this.#initialConnect = null;
    initialConnect?.reject(new Error("TUI transport disconnected before connecting"));
    const socket = this.#socket;
    this.#socket = null;
    if (socket && socket.readyState !== WebSocket.CLOSED) socket.close();
    this.#emitConnection({ status: "disconnected", attempt: this.#attempt });
  }

  send(message: ClientMessage): void {
    if (this.#socket?.readyState !== WebSocket.OPEN) {
      throw new Error("TUI transport is not connected");
    }
    this.#socket.send(JSON.stringify(message));
  }

  onMessage(listener: (message: ServerMessage) => void): () => void {
    this.#messageListeners.add(listener);
    return () => this.#messageListeners.delete(listener);
  }

  onConnection(listener: (event: TuiTransportConnectionEvent) => void): () => void {
    this.#connectionListeners.add(listener);
    return () => this.#connectionListeners.delete(listener);
  }

  #open(reconnecting: boolean): void {
    if (!this.#desiredOpen) return;
    this.#attempt += 1;
    this.#emitConnection({
      status: reconnecting ? "reconnecting" : "connecting",
      attempt: this.#attempt,
    });
    let socket: WebSocket;
    try {
      socket = this.#createSocket(this.endpoint);
    } catch (error) {
      this.#handleOpenFailure(error);
      return;
    }
    this.#socket = socket;
    socket.on("open", () => {
      if (this.#socket !== socket) return;
      this.#emitConnection({ status: "connected", attempt: this.#attempt });
      this.#initialConnect?.resolve();
      this.#initialConnect = null;
    });
    socket.on("message", (data) => {
      try {
        const raw: unknown = JSON.parse(data.toString());
        if (!isRecord(raw)) throw new Error("server message must be an object");
        const message = parseServerMessage(raw);
        for (const listener of [...this.#messageListeners]) listener(message);
      } catch (error) {
        this.#emitConnection({
          status: "failed",
          attempt: this.#attempt,
          error: `Malformed server message: ${errorMessage(error)}`,
        });
      }
    });
    socket.on("error", (error) => {
      if (socket.readyState === WebSocket.OPEN) {
        this.#emitConnection({
          status: "reconnecting",
          attempt: this.#attempt,
          error: error.message,
        });
      }
    });
    socket.on("close", () => {
      if (this.#socket === socket) this.#socket = null;
      if (!this.#desiredOpen) return;
      this.#scheduleReconnect();
    });
  }

  #handleOpenFailure(error: unknown): void {
    const failure = new Error(errorMessage(error));
    if (this.#attempt === 1) {
      this.#initialConnect?.reject(failure);
      this.#initialConnect = null;
    }
    this.#emitConnection({
      status: "reconnecting",
      attempt: this.#attempt,
      error: failure.message,
    });
    this.#scheduleReconnect();
  }

  #scheduleReconnect(): void {
    if (!this.#desiredOpen || this.#reconnectTimer) return;
    const delay = Math.min(
      this.#reconnectBaseMs * 2 ** Math.max(0, this.#attempt - 1),
      this.#reconnectMaxMs,
    );
    this.#emitConnection({ status: "reconnecting", attempt: this.#attempt });
    this.#reconnectTimer = setTimeout(() => {
      this.#reconnectTimer = null;
      this.#open(true);
    }, delay);
    this.#reconnectTimer.unref?.();
  }

  #emitConnection(event: TuiTransportConnectionEvent): void {
    for (const listener of [...this.#connectionListeners]) listener(event);
  }
}

export function normalizeTuiEndpoint(endpoint: string): string {
  const url = new URL(endpoint);
  if (url.protocol === "http:") url.protocol = "ws:";
  if (url.protocol === "https:") url.protocol = "wss:";
  if (url.protocol !== "ws:" && url.protocol !== "wss:") {
    throw new Error("TUI endpoint must use ws, wss, http, or https");
  }
  if (url.pathname === "/" || url.pathname === "") url.pathname = "/ws/interactive";
  url.searchParams.set(TRANSCRIPT_PROTOCOL_QUERY_PARAM, TRANSCRIPT_PROTOCOL_QUERY_VALUE);
  return url.toString();
}

export function tuiHttpBaseUrl(endpoint: string): string {
  const url = new URL(normalizeTuiEndpoint(endpoint));
  url.protocol = url.protocol === "wss:" ? "https:" : "http:";
  url.pathname = "/";
  url.search = "";
  url.hash = "";
  return url.toString().replace(/\/$/, "");
}

/** Return an endpoint suitable for terminal display without embedded credentials. */
export function displayTuiEndpoint(endpoint: string): string {
  const url = new URL(normalizeTuiEndpoint(endpoint));
  url.username = "";
  url.password = "";
  for (const key of [...url.searchParams.keys()]) {
    if (isSensitiveQueryParameter(key)) url.searchParams.set(key, "REDACTED");
  }
  if (url.hash.includes("=")) {
    const fragment = new URLSearchParams(url.hash.slice(1));
    for (const key of [...fragment.keys()]) {
      if (isSensitiveQueryParameter(key)) fragment.set(key, "REDACTED");
    }
    url.hash = fragment.toString();
  }
  return url.toString();
}

/** Plaintext WebSockets are safe only when they never leave the local loopback. */
export function assertSecureTuiEndpoint(endpoint: string): void {
  const url = new URL(normalizeTuiEndpoint(endpoint));
  if (url.protocol === "ws:" && !isLoopbackHostname(url.hostname)) {
    throw new Error("Remote TUI endpoints must use wss or https");
  }
}

function isLoopbackHostname(hostname: string): boolean {
  const normalized = hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (normalized === "localhost" || normalized === "::1") return true;
  const octets = normalized.split(".");
  return octets.length === 4 && octets[0] === "127" && octets.every((octet) => {
    if (!/^\d{1,3}$/.test(octet)) return false;
    const value = Number(octet);
    return value >= 0 && value <= 255;
  });
}

function isSensitiveQueryParameter(key: string): boolean {
  const normalized = key.toLowerCase().replace(/[^a-z0-9]/g, "");
  return [
    "apikey",
    "accesstoken",
    "auth",
    "authtoken",
    "authorization",
    "bearer",
    "credential",
    "jwt",
    "key",
    "password",
    "secret",
    "signature",
    "sig",
    "token",
  ].some((sensitive) => normalized === sensitive || normalized.endsWith(sensitive));
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
