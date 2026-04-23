import { describe, expect, it, vi } from "vitest";

import {
  captureBrowserContextFromUrl,
  renderCapturedBrowserContext,
} from "../../../src/integrations/browser/context-capture.js";
import type {
  BrowserAuditEvent,
  BrowserSessionConfig,
  BrowserSnapshot,
} from "../../../src/integrations/browser/index.js";

function buildSessionConfig(): BrowserSessionConfig {
  return {
    schemaVersion: "1.0",
    profileMode: "ephemeral",
    allowedDomains: ["example.com"],
    allowAuth: false,
    allowUploads: false,
    allowDownloads: false,
    captureScreenshots: true,
    headless: true,
    downloadsRoot: null,
    uploadsRoot: null,
  };
}

function buildAuditEvent(overrides: Partial<BrowserAuditEvent> = {}): BrowserAuditEvent {
  return {
    schemaVersion: "1.0",
    eventId: "evt_1",
    sessionId: "browser_session",
    actionId: "act_1",
    kind: "action_result",
    allowed: true,
    policyReason: "allowed",
    timestamp: "2026-04-22T12:00:00.000Z",
    beforeUrl: "about:blank",
    afterUrl: "https://example.com/status",
    artifacts: {
      htmlPath: null,
      screenshotPath: null,
      downloadPath: null,
    },
    ...overrides,
  };
}

function buildSnapshot(): BrowserSnapshot {
  return {
    schemaVersion: "1.0",
    sessionId: "browser_session",
    capturedAt: "2026-04-22T12:00:01.000Z",
    url: "https://example.com/status",
    title: "Example Status",
    refs: [],
    visibleText: " Checkout   is degraded due to upstream latency. ".repeat(40),
    htmlPath: "/tmp/status.html",
    screenshotPath: "/tmp/status.png",
  };
}

const SETTINGS = {
  browserEnabled: true,
  browserBackend: "chrome-cdp",
  browserProfileMode: "ephemeral" as const,
  browserAllowedDomains: "example.com",
  browserAllowAuth: false,
  browserAllowUploads: false,
  browserAllowDownloads: false,
  browserCaptureScreenshots: true,
  browserHeadless: true,
  browserDebuggerUrl: "http://127.0.0.1:9222",
  browserPreferredTargetUrl: "",
  browserDownloadsRoot: "",
  browserUploadsRoot: "",
  runsRoot: "/tmp/runs",
};

describe("browser context capture", () => {
  it("captures a normalized browser snapshot and closes the session", async () => {
    const session = {
      navigate: vi.fn(async () => buildAuditEvent()),
      snapshot: vi.fn(async () => buildSnapshot()),
      close: vi.fn(async () => undefined),
    };
    const createBrowserRuntimeFromSettings = vi.fn(() => ({
      sessionConfig: buildSessionConfig(),
      runtime: {
        createSession: vi.fn(async () => session),
      },
    }));

    const context = await captureBrowserContextFromUrl(
      {
        settings: SETTINGS,
        browserUrl: "https://example.com/status",
        evidenceRoot: "/tmp/evidence",
      },
      { createBrowserRuntimeFromSettings } as never,
    );

    expect(createBrowserRuntimeFromSettings).toHaveBeenCalledWith(SETTINGS, {
      evidenceRoot: "/tmp/evidence",
    });
    expect(session.navigate).toHaveBeenCalledWith("https://example.com/status");
    expect(session.snapshot).toHaveBeenCalledOnce();
    expect(session.close).toHaveBeenCalledOnce();
    expect(context).toEqual({
      url: "https://example.com/status",
      title: "Example Status",
      visibleText: expect.stringMatching(/^Checkout is degraded/),
      htmlPath: "/tmp/status.html",
      screenshotPath: "/tmp/status.png",
    });
    expect(context.visibleText.length).toBeLessThanOrEqual(1200);
  });

  it("fails closed when navigation is denied by policy", async () => {
    const session = {
      navigate: vi.fn(async () => buildAuditEvent({
        allowed: false,
        policyReason: "domain_not_allowed",
      })),
      snapshot: vi.fn(async () => buildSnapshot()),
      close: vi.fn(async () => undefined),
    };
    const createBrowserRuntimeFromSettings = vi.fn(() => ({
      sessionConfig: buildSessionConfig(),
      runtime: {
        createSession: vi.fn(async () => session),
      },
    }));

    await expect(
      captureBrowserContextFromUrl(
        {
          settings: SETTINGS,
          browserUrl: "https://blocked.example/status",
          evidenceRoot: "/tmp/evidence",
        },
        { createBrowserRuntimeFromSettings } as never,
      ),
    ).rejects.toThrow("browser navigation blocked by policy: domain_not_allowed");

    expect(session.snapshot).not.toHaveBeenCalled();
    expect(session.close).toHaveBeenCalledOnce();
  });

  it("requires browser exploration to be enabled", async () => {
    await expect(
      captureBrowserContextFromUrl(
        {
          settings: {
            ...SETTINGS,
            browserEnabled: false,
          },
          browserUrl: "https://example.com/status",
          evidenceRoot: "/tmp/evidence",
        },
        { createBrowserRuntimeFromSettings: vi.fn(() => null) } as never,
      ),
    ).rejects.toThrow("browser exploration is disabled");
  });

  it("renders prompt-friendly browser context", () => {
    expect(
      renderCapturedBrowserContext({
        url: "https://example.com/status",
        title: "Example Status",
        visibleText: "Checkout is degraded",
        htmlPath: "/tmp/status.html",
        screenshotPath: "/tmp/status.png",
      }),
    ).toContain("URL: https://example.com/status");
  });
});
