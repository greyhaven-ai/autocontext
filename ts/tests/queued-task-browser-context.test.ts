import { describe, expect, it, vi } from "vitest";

import {
  createQueuedTaskBrowserContextService,
  mergeQueuedTaskReferenceContext,
} from "../src/execution/queued-task-browser-context.js";

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

describe("queued task browser context", () => {
  it("captures browser context below the queued task artifact root", async () => {
    const captureBrowserContextFromUrl = vi.fn(async () => ({
      url: "https://example.com/status",
      title: "Status",
      visibleText: "Checkout is degraded",
      htmlPath: "/tmp/status.html",
      screenshotPath: "/tmp/status.png",
    }));

    const service = createQueuedTaskBrowserContextService(
      SETTINGS,
      { captureBrowserContextFromUrl },
    );

    const referenceContext = await service.buildReferenceContext({
      taskId: "task_123",
      browserUrl: "https://example.com/status",
      referenceContext: "Saved facts",
    });

    expect(captureBrowserContextFromUrl).toHaveBeenCalledWith({
      settings: SETTINGS,
      browserUrl: "https://example.com/status",
      evidenceRoot: "/tmp/runs/task_queue/task_123",
    });
    expect(referenceContext).toContain("Saved facts");
    expect(referenceContext).toContain("Live browser context:");
    expect(referenceContext).toContain("Checkout is degraded");
  });

  it("merges queued reference context without introducing blank noise", () => {
    expect(mergeQueuedTaskReferenceContext(" Existing facts ", " Browser facts ")).toBe(
      "Existing facts\n\nBrowser facts",
    );
    expect(mergeQueuedTaskReferenceContext(undefined, " Browser facts ")).toBe("Browser facts");
  });
});
