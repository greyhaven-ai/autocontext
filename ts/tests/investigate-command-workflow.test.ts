import { describe, expect, it, vi } from "vitest";

import {
  INVESTIGATE_HELP_TEXT,
  executeInvestigateCommandWorkflow,
  prepareInvestigateRequest,
  planInvestigateCommand,
  renderInvestigationSuccess,
} from "../src/cli/investigate-command-workflow.js";
import type { InvestigationResult } from "../src/investigation/engine.js";

function buildResult(
  overrides: Partial<InvestigationResult> = {},
): InvestigationResult {
  return {
    id: "inv_123",
    name: "checkout_rca",
    family: "investigation",
    status: "completed",
    description: "why did conversion drop",
    question: "Why did conversion drop?",
    hypotheses: [
      { id: "h1", statement: "Config change", confidence: 0.74, status: "supported" },
      { id: "h2", statement: "Traffic spike", confidence: 0.2, status: "contradicted" },
    ],
    evidence: [],
    conclusion: {
      bestExplanation: "Config change",
      confidence: 0.74,
      limitations: [],
    },
    unknowns: ["Need production logs"],
    recommendedNextSteps: ["Inspect the rollout diff"],
    stepsExecuted: 4,
    artifacts: { investigationDir: "/tmp/investigations/checkout_rca" },
    ...overrides,
  };
}

describe("investigate command workflow", () => {
  it("exposes investigate help text", () => {
    expect(INVESTIGATE_HELP_TEXT).toContain("autoctx investigate");
    expect(INVESTIGATE_HELP_TEXT).toContain("--description");
    expect(INVESTIGATE_HELP_TEXT).toContain("--max-steps");
    expect(INVESTIGATE_HELP_TEXT).toContain("--browser-url");
  });

  it("plans an investigation request from CLI values", () => {
    expect(
      planInvestigateCommand({
        description: "why did conversion drop",
        "max-steps": "12",
        hypotheses: "7",
        "save-as": "checkout_rca",
      }),
    ).toEqual({
      description: "why did conversion drop",
      maxSteps: 12,
      maxHypotheses: 7,
      saveAs: "checkout_rca",
    });
  });

  it("rejects investigate commands without a description", () => {
    expect(() => planInvestigateCommand({})).toThrow(
      "Error: --description is required. Run 'autoctx investigate --help' for usage.",
    );
  });

  it("renders human-readable investigation success output", () => {
    expect(renderInvestigationSuccess(buildResult())).toBe(
      [
        "Investigation: checkout_rca",
        "Question: Why did conversion drop?",
        "",
        "Hypotheses:",
        "  ✓ Config change (confidence: 0.74, supported)",
        "  ✗ Traffic spike (confidence: 0.20, contradicted)",
        "",
        "Conclusion: Config change",
        "Confidence: 0.74",
        "",
        "Unknowns:",
        "  - Need production logs",
        "",
        "Next steps:",
        "  → Inspect the rollout diff",
        "",
        "Artifacts: /tmp/investigations/checkout_rca",
      ].join("\n"),
    );
  });

  it("executes the investigation request through the engine", async () => {
    const run = vi.fn().mockResolvedValue(buildResult());

    const result = await executeInvestigateCommandWorkflow({
      values: {
        description: "why did conversion drop",
        "max-steps": "10",
        hypotheses: "6",
      },
      engine: { run },
    });

    expect(run).toHaveBeenCalledWith({
      description: "why did conversion drop",
      maxSteps: 10,
      maxHypotheses: 6,
      saveAs: undefined,
    });
    expect(result.name).toBe("checkout_rca");
  });

  it("prepares an investigation request with captured browser context when requested", async () => {
    const browserContext = {
      url: "https://example.com/status",
      title: "Status",
      visibleText: "Checkout is degraded",
      htmlPath: "/tmp/status.html",
      screenshotPath: "/tmp/status.png",
    };
    const captureBrowserContext = vi.fn().mockResolvedValue(browserContext);

    const request = await prepareInvestigateRequest(
      {
        values: {
          description: "why did conversion drop",
          "browser-url": "https://example.com/status",
          "save-as": "checkout_rca",
        },
        settings: {
          browserEnabled: true,
          browserBackend: "chrome-cdp",
          browserProfileMode: "ephemeral",
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
        },
      },
      {
        captureBrowserContext,
      },
    );

    expect(captureBrowserContext).toHaveBeenCalledWith({
      settings: expect.objectContaining({
        browserEnabled: true,
        browserBackend: "chrome-cdp",
      }),
      browserUrl: "https://example.com/status",
      investigationName: "checkout_rca",
    });
    expect(request).toEqual({
      description: "why did conversion drop",
      maxSteps: undefined,
      maxHypotheses: undefined,
      saveAs: "checkout_rca",
      browserContext,
    });
  });
});
