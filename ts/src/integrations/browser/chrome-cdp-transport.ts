import WebSocket from "ws";

export class ChromeCdpTransportError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ChromeCdpTransportError";
  }
}

export interface BrowserWebSocketLike {
  readonly readyState: number;
  on(event: "message", listener: (data: unknown) => void): this;
  on(event: "close", listener: () => void): this;
  on(event: "error", listener: (error: Error) => void): this;
  once(event: "open", listener: () => void): this;
  once(event: "error", listener: (error: Error) => void): this;
  once(event: "close", listener: () => void): this;
  removeListener(event: "open" | "error" | "close", listener: (...args: any[]) => void): this;
  send(data: string): void;
  close(): void;
}

export type BrowserWebSocketFactory = (url: string) => BrowserWebSocketLike;

export interface ChromeCdpWebSocketTransportOpts {
  readonly url: string;
  readonly connectionTimeoutMs?: number;
  readonly webSocketFactory?: BrowserWebSocketFactory;
}

type PendingRequest = {
  readonly resolve: (value: Record<string, unknown>) => void;
  readonly reject: (error: Error) => void;
};

const OPEN_READY_STATE = 1;
const CLOSED_READY_STATE = 3;

export class ChromeCdpWebSocketTransport {
  readonly url: string;
  readonly connectionTimeoutMs: number;

  private readonly webSocketFactory: BrowserWebSocketFactory;
  private socket: BrowserWebSocketLike | null = null;
  private connectPromise: Promise<void> | null = null;
  private nextId = 0;
  private readonly pending = new Map<number, PendingRequest>();

  constructor(opts: ChromeCdpWebSocketTransportOpts) {
    this.url = opts.url;
    this.connectionTimeoutMs = opts.connectionTimeoutMs ?? 5_000;
    this.webSocketFactory = opts.webSocketFactory ?? ((url) => new WebSocket(url));
  }

  async connect(): Promise<void> {
    if (this.socket && this.socket.readyState === OPEN_READY_STATE) {
      return;
    }
    if (this.connectPromise) {
      return this.connectPromise;
    }

    this.connectPromise = new Promise<void>((resolve, reject) => {
      const socket = this.webSocketFactory(this.url);
      const timeout = setTimeout(() => {
        reject(new ChromeCdpTransportError(`Timed out connecting to CDP websocket: ${this.url}`));
      }, this.connectionTimeoutMs);

      const onOpen = () => {
        clearTimeout(timeout);
        socket.removeListener("error", onError);
        this.socket = socket;
        resolve();
      };

      const onError = (error: Error) => {
        clearTimeout(timeout);
        socket.removeListener("open", onOpen);
        reject(new ChromeCdpTransportError(`Failed to connect to CDP websocket: ${error.message}`));
      };

      this.attachSocketHandlers(socket);
      socket.once("open", onOpen);
      socket.once("error", onError);
    });

    try {
      await this.connectPromise;
    } finally {
      this.connectPromise = null;
    }
  }

  async send(method: string, params: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    await this.connect();
    const socket = this.socket;
    if (!socket || socket.readyState !== OPEN_READY_STATE) {
      throw new ChromeCdpTransportError("CDP websocket is not connected");
    }

    this.nextId += 1;
    const id = this.nextId;

    return await new Promise<Record<string, unknown>>((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      try {
        socket.send(JSON.stringify({ id, method, params }));
      } catch (error) {
        this.pending.delete(id);
        reject(asTransportError(error, `Failed to send CDP message ${method}`));
      }
    });
  }

  async close(): Promise<void> {
    const socket = this.socket;
    this.socket = null;
    if (!socket) {
      if (this.connectPromise) {
        await this.connectPromise.catch(() => undefined);
      }
      return;
    }
    if (socket.readyState === CLOSED_READY_STATE) {
      return;
    }
    await new Promise<void>((resolve) => {
      socket.once("close", () => resolve());
      socket.close();
    });
  }

  private attachSocketHandlers(socket: BrowserWebSocketLike): void {
    socket.on("message", (data) => {
      const payload = decodeMessage(data);
      if (!payload) {
        return;
      }
      const messageId = payload.id;
      if (typeof messageId !== "number") {
        return;
      }
      const pending = this.pending.get(messageId);
      if (!pending) {
        return;
      }
      this.pending.delete(messageId);
      if (isRecord(payload.error)) {
        pending.reject(new ChromeCdpTransportError(errorMessage(payload.error)));
        return;
      }
      pending.resolve(payload);
    });

    socket.on("close", () => {
      this.failPending(new ChromeCdpTransportError("CDP websocket closed"));
      this.socket = null;
    });

    socket.on("error", (error) => {
      if (!this.connectPromise) {
        this.failPending(new ChromeCdpTransportError(`CDP websocket failed: ${error.message}`));
        this.socket = null;
      }
    });
  }

  private failPending(error: ChromeCdpTransportError): void {
    const entries = [...this.pending.values()];
    this.pending.clear();
    for (const pending of entries) {
      pending.reject(error);
    }
  }
}

function decodeMessage(data: unknown): Record<string, unknown> | null {
  const text = rawDataToString(data);
  if (text === null) {
    return null;
  }
  try {
    const parsed = JSON.parse(text) as unknown;
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function rawDataToString(data: unknown): string | null {
  if (typeof data === "string") {
    return data;
  }
  if (Buffer.isBuffer(data)) {
    return data.toString("utf-8");
  }
  if (data instanceof ArrayBuffer) {
    return Buffer.from(data).toString("utf-8");
  }
  if (ArrayBuffer.isView(data)) {
    return Buffer.from(data.buffer, data.byteOffset, data.byteLength).toString("utf-8");
  }
  if (Array.isArray(data) && data.every((entry) => Buffer.isBuffer(entry))) {
    return Buffer.concat(data).toString("utf-8");
  }
  return null;
}

function errorMessage(error: Record<string, unknown>): string {
  const message = error.message;
  if (typeof message === "string" && message.length > 0) {
    return message;
  }
  return `CDP error: ${JSON.stringify(error)}`;
}

function asTransportError(error: unknown, prefix: string): ChromeCdpTransportError {
  if (error instanceof ChromeCdpTransportError) {
    return error;
  }
  if (error instanceof Error) {
    return new ChromeCdpTransportError(`${prefix}: ${error.message}`);
  }
  return new ChromeCdpTransportError(prefix);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
