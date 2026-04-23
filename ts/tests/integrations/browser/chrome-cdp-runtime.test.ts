import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, test } from "vitest";

import { ChromeCdpSession } from "../../../src/integrations/browser/chrome-cdp.js";
import { ChromeCdpRuntime } from "../../../src/integrations/browser/chrome-cdp-runtime.js";
import { buildDefaultBrowserSessionConfig } from "../../../src/integrations/browser/policy.js";

class FakeTransport {
  async send(): Promise<Record<string, unknown>> {
    return {};
  }

  async close(): Promise<void> {
    return;
  }
}

describe("chrome cdp runtime", () => {
  test("creates sessions with the configured transport and evidence store", async () => {
    const rootDir = mkdtempSync(join(tmpdir(), "browser-runtime-"));
    const createdUrls: string[] = [];
    const transport = new FakeTransport();
    const runtime = new ChromeCdpRuntime({
      websocketUrl: "ws://127.0.0.1:9222/devtools/page/1",
      evidenceRoot: rootDir,
      transportFactory: (url) => {
        createdUrls.push(url);
        return transport;
      },
      sessionIdFactory: () => "session_fixed",
    });

    const session = await runtime.createSession(
      buildDefaultBrowserSessionConfig({ allowedDomains: ["example.com"] }),
    );

    expect(session).toBeInstanceOf(ChromeCdpSession);
    expect(createdUrls).toEqual(["ws://127.0.0.1:9222/devtools/page/1"]);
    expect(session.sessionId).toBe("session_fixed");
    expect(session.transport).toBe(transport);
    expect(session.evidenceStore?.rootDir).toBe(rootDir);
  });

  test("resolves the websocket target from discovery before creating the session", async () => {
    const rootDir = mkdtempSync(join(tmpdir(), "browser-runtime-"));
    const createdUrls: string[] = [];
    const transport = new FakeTransport();
    const discoveryCalls: Array<{ preferredUrl?: string }> = [];
    const runtime = new ChromeCdpRuntime({
      debuggerUrl: "http://127.0.0.1:9222",
      preferredTargetUrl: "https://example.com/dashboard",
      evidenceRoot: rootDir,
      targetDiscovery: {
        async resolveWebSocketUrl(_config, opts = {}) {
          discoveryCalls.push(opts);
          return "ws://127.0.0.1:9222/devtools/page/discovered";
        },
      },
      transportFactory: (url) => {
        createdUrls.push(url);
        return transport;
      },
      sessionIdFactory: () => "session_fixed",
    });

    const session = await runtime.createSession(
      buildDefaultBrowserSessionConfig({ allowedDomains: ["example.com"] }),
    );

    expect(session).toBeInstanceOf(ChromeCdpSession);
    expect(createdUrls).toEqual(["ws://127.0.0.1:9222/devtools/page/discovered"]);
    expect(discoveryCalls).toEqual([{ preferredUrl: "https://example.com/dashboard" }]);
  });
});
