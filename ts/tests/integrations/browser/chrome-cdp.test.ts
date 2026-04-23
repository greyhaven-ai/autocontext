import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, test } from "vitest";

import { ChromeCdpSession } from "../../../src/integrations/browser/chrome-cdp.js";
import { BrowserEvidenceStore } from "../../../src/integrations/browser/evidence.js";
import { buildDefaultBrowserSessionConfig } from "../../../src/integrations/browser/policy.js";

class FakeTransport {
  readonly calls: Array<{ method: string; params: Record<string, unknown> }> = [];
  readonly responses: Array<Record<string, unknown>>;
  closed = false;

  constructor(responses: Array<Record<string, unknown>>) {
    this.responses = [...responses];
  }

  async send(method: string, params: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    this.calls.push({ method, params });
    return this.responses.shift() ?? {};
  }

  async close(): Promise<void> {
    this.closed = true;
  }
}

describe("chrome cdp session", () => {
  test("navigate blocks disallowed domains before transport", async () => {
    const rootDir = mkdtempSync(join(tmpdir(), "browser-cdp-"));
    const transport = new FakeTransport([]);
    const session = new ChromeCdpSession({
      sessionId: "session_1",
      config: buildDefaultBrowserSessionConfig({ allowedDomains: ["example.com"] }),
      transport,
      evidenceStore: new BrowserEvidenceStore({ rootDir }),
    });

    const event = await session.navigate("https://blocked.example.net/dashboard");

    expect(event.allowed).toBe(false);
    expect(event.policyReason).toBe("domain_not_allowed");
    expect(transport.calls).toHaveLength(0);
  });

  test("snapshot persists artifacts and click uses ref mapping", async () => {
    const rootDir = mkdtempSync(join(tmpdir(), "browser-cdp-"));
    const transport = new FakeTransport([
      {},
      {},
      {
        result: {
          value: {
            url: "https://example.com/dashboard",
            title: "Dashboard",
            visibleText: "Welcome back",
            refs: [
              {
                id: "@e1",
                role: "button",
                name: "Continue",
                selector: "button:nth-of-type(1)",
              },
            ],
            html: "<html><body>Welcome back</body></html>",
          },
        },
      },
      { data: Buffer.from("png-bytes").toString("base64") },
      { result: { value: { ok: true } } },
    ]);
    const session = new ChromeCdpSession({
      sessionId: "session_1",
      config: buildDefaultBrowserSessionConfig({ allowedDomains: ["example.com"] }),
      transport,
      evidenceStore: new BrowserEvidenceStore({ rootDir }),
    });

    const snapshot = await session.snapshot();
    const event = await session.click("@e1");

    expect(snapshot.url).toBe("https://example.com/dashboard");
    expect(snapshot.htmlPath).toBeTruthy();
    expect(snapshot.screenshotPath).toBeTruthy();
    expect(readFileSync(snapshot.screenshotPath!)).toEqual(Buffer.from("png-bytes"));
    expect(String(transport.calls[2]?.params.expression)).toContain("selectorFor(element)");
    expect(event.allowed).toBe(true);
    expect(transport.calls.at(-1)?.method).toBe("Runtime.evaluate");
    expect(String(transport.calls.at(-1)?.params.expression)).toContain("button:nth-of-type(1)");
  });

  test("fill denies password entry when auth is disabled", async () => {
    const rootDir = mkdtempSync(join(tmpdir(), "browser-cdp-"));
    const transport = new FakeTransport([]);
    const session = new ChromeCdpSession({
      sessionId: "session_1",
      config: buildDefaultBrowserSessionConfig({ allowedDomains: ["example.com"] }),
      transport,
      evidenceStore: new BrowserEvidenceStore({ rootDir }),
    });

    const event = await session.fill("@e1", "super-secret", { fieldKind: "password" });

    expect(event.allowed).toBe(false);
    expect(event.policyReason).toBe("auth_blocked");
    expect(transport.calls).toHaveLength(0);
  });
});
