import { EventEmitter } from "node:events";
import { describe, expect, test } from "vitest";

import {
  ChromeCdpTransportError,
  ChromeCdpWebSocketTransport,
  type BrowserWebSocketFactory,
} from "../../../src/integrations/browser/chrome-cdp-transport.js";

type ScriptStep = (request: Record<string, unknown>) => Record<string, unknown>;

class FakeWebSocket extends EventEmitter {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;

  readonly sent: Array<Record<string, unknown>> = [];
  readyState = FakeWebSocket.CONNECTING;

  private readonly steps: ScriptStep[];

  constructor(
    readonly url: string,
    script: ScriptStep[],
  ) {
    super();
    this.steps = [...script];
    queueMicrotask(() => {
      this.readyState = FakeWebSocket.OPEN;
      this.emit("open");
    });
  }

  send(data: string): void {
    const request = JSON.parse(data) as Record<string, unknown>;
    this.sent.push(request);
    const next = this.steps.shift() ?? ((message) => ({ id: message.id, result: { ok: true } }));
    const response = next(request);
    queueMicrotask(() => {
      this.emit("message", Buffer.from(JSON.stringify(response)));
    });
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
    queueMicrotask(() => {
      this.emit("close");
    });
  }
}

function createFactory(script: ScriptStep[]): {
  readonly factory: BrowserWebSocketFactory;
  readonly sockets: FakeWebSocket[];
} {
  const sockets: FakeWebSocket[] = [];
  return {
    factory: (url: string) => {
      const socket = new FakeWebSocket(url, script);
      sockets.push(socket);
      return socket;
    },
    sockets,
  };
}

describe("chrome cdp websocket transport", () => {
  test("round trips cdp commands over the websocket", async () => {
    const { factory, sockets } = createFactory([
      (request) => ({
        id: request.id,
        result: {
          product: "Chrome",
          echoMethod: request.method,
          echoParams: request.params,
        },
      }),
    ]);
    const transport = new ChromeCdpWebSocketTransport({
      url: "ws://127.0.0.1:9222/devtools/page/1",
      webSocketFactory: factory,
    });

    const response = await transport.send("Browser.getVersion", { verbose: true });
    await transport.close();

    expect(sockets).toHaveLength(1);
    expect(sockets[0]?.sent).toEqual([
      {
        id: 1,
        method: "Browser.getVersion",
        params: { verbose: true },
      },
    ]);
    expect(response.result).toEqual({
      product: "Chrome",
      echoMethod: "Browser.getVersion",
      echoParams: { verbose: true },
    });
  });

  test("raises on cdp protocol errors", async () => {
    const { factory } = createFactory([
      (request) => ({
        id: request.id,
        error: {
          message: "domain blocked",
        },
      }),
    ]);
    const transport = new ChromeCdpWebSocketTransport({
      url: "ws://127.0.0.1:9222/devtools/page/1",
      webSocketFactory: factory,
    });

    const sendPromise = transport.send("Page.navigate", { url: "https://blocked.example" });

    await expect(sendPromise).rejects.toThrowError(ChromeCdpTransportError);
    await expect(sendPromise).rejects.toThrow("domain blocked");
    await transport.close();
  });
});
