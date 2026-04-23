import { describe, expect, it } from "vitest";

import {
  buildInvestigationHypothesisPrompt,
  buildInvestigationSpecPrompt,
} from "../src/investigation/investigation-generation-prompts.js";

describe("investigation generation prompts", () => {
  it("builds the investigation spec prompt from a description", () => {
    const prompt = buildInvestigationSpecPrompt("Investigate anomaly");

    expect(prompt.systemPrompt).toContain("investigation designer");
    expect(prompt.systemPrompt).toContain("correct_diagnosis");
    expect(prompt.userPrompt).toBe("Investigation: Investigate anomaly");
  });

  it("includes browser context in investigation prompts when provided", () => {
    const browserContext = {
      url: "https://example.com/status",
      title: "Status Page",
      visibleText: "Checkout is degraded for some users.",
      htmlPath: "/tmp/status.html",
      screenshotPath: "/tmp/status.png",
    };

    const specPrompt = buildInvestigationSpecPrompt("Investigate anomaly", {
      browserContext,
    });
    const hypothesisPrompt = buildInvestigationHypothesisPrompt({
      description: "Investigate outage",
      execution: { stepsExecuted: 1, collectedEvidence: [] },
      browserContext,
    });

    expect(specPrompt.userPrompt).toContain("Live browser context");
    expect(specPrompt.userPrompt).toContain("https://example.com/status");
    expect(hypothesisPrompt.userPrompt).toContain("Checkout is degraded for some users.");
  });

  it("builds the hypothesis prompt with evidence, steps, and max hypotheses", () => {
    const prompt = buildInvestigationHypothesisPrompt({
      description: "Investigate outage",
      execution: {
        stepsExecuted: 2,
        collectedEvidence: [{ content: "db saturation" }, { content: "config drift" }],
      },
      maxHypotheses: 3,
    });

    expect(prompt.systemPrompt).toContain("diagnostic analyst");
    expect(prompt.userPrompt).toContain("Evidence collected: db saturation, config drift");
    expect(prompt.userPrompt).toContain("Steps taken: 2");
    expect(prompt.userPrompt).toContain("Maximum hypotheses: 3");
  });

  it("uses the no-evidence fallback text in the hypothesis prompt", () => {
    const prompt = buildInvestigationHypothesisPrompt({
      description: "Investigate outage",
      execution: { stepsExecuted: 0, collectedEvidence: [] },
    });

    expect(prompt.userPrompt).toContain("Evidence collected: none yet");
    expect(prompt.userPrompt).toContain("Maximum hypotheses: 5");
  });
});
