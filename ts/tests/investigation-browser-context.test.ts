import { describe, expect, it, vi } from "vitest";

import {
  buildInvestigationBrowserEvidence,
  captureInvestigationBrowserContext,
  renderInvestigationBrowserContext,
} from "../src/investigation/browser-context.js";

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
  browserDebuggerUrl: "http://127.0.0.1:9333",
  browserPreferredTargetUrl: "",
  browserDownloadsRoot: "",
  browserUploadsRoot: "",
  runsRoot: "/tmp/runs",
  knowledgeRoot: "/tmp/knowledge",
};

describe("investigation browser context", () => {
  it("captures browser context under the investigation artifact root", async () => {
    const browserContext = {
      url: "https://example.com/status",
      title: "Status",
      visibleText: "Checkout is degraded",
      htmlPath: "/tmp/status.html",
      screenshotPath: "/tmp/status.png",
    };
    const captureBrowserContextFromUrl = vi.fn(async () => browserContext);

    const context = await captureInvestigationBrowserContext(
      {
        settings: SETTINGS,
        browserUrl: "https://example.com/status",
        investigationName: "checkout_rca",
      },
      {
        captureBrowserContextFromUrl,
      },
    );

    expect(context).toBe(browserContext);
    expect(captureBrowserContextFromUrl).toHaveBeenCalledWith({
      settings: SETTINGS,
      browserUrl: "https://example.com/status",
      evidenceRoot: "/tmp/knowledge/_investigations/checkout_rca",
    });
  });

  it("renders and converts browser context into evidence", () => {
    const context = {
      url: "https://example.com/status",
      title: "Status",
      visibleText: "Checkout is degraded",
      htmlPath: "/tmp/status.html",
      screenshotPath: "/tmp/status.png",
    };

    expect(renderInvestigationBrowserContext(context)).toContain("Live browser context");
    expect(buildInvestigationBrowserEvidence(context)).toEqual({
      id: "browser_snapshot",
      kind: "browser_snapshot",
      source: "https://example.com/status",
      summary: "Status\nCheckout is degraded",
      supports: [],
      contradicts: [],
      isRedHerring: false,
    });
  });
});
