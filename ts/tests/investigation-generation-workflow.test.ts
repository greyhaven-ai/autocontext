import { describe, expect, it } from "vitest";

import {
  buildInvestigationSpec,
  generateInvestigationHypotheses,
} from "../src/investigation/investigation-generation-workflow.js";
import type { LLMProvider } from "../src/types/index.js";

function mockProvider(responses: string[]): LLMProvider {
  let callIndex = 0;
  return {
    complete: async () => ({ text: responses[callIndex++] ?? responses[responses.length - 1] ?? "{}" }),
    defaultModel: () => "test-model",
  } as unknown as LLMProvider;
}

describe("investigation generation workflow", () => {
  it("builds an investigation spec from provider JSON", async () => {
    await expect(
      buildInvestigationSpec({
        provider: mockProvider([
          JSON.stringify({
            description: "Investigate anomaly",
            actions: [],
            evidence_pool: [],
            correct_diagnosis: "config drift",
          }),
        ]),
        description: "Investigate anomaly",
      }),
    ).resolves.toMatchObject({
      description: "Investigate anomaly",
      correct_diagnosis: "config drift",
    });
  });

  it("normalizes hypothesis output and falls back when parsing fails", async () => {
    await expect(
      generateInvestigationHypotheses({
        provider: mockProvider([
          JSON.stringify({
            question: "What caused the outage?",
            hypotheses: [
              { statement: "Database saturation", confidence: 1.2 },
              { statement: "Traffic spike", confidence: -1 },
            ],
          }),
        ]),
        description: "Investigate outage",
        execution: { stepsExecuted: 2, collectedEvidence: [{ content: "db saturation" }] },
        maxHypotheses: 1,
      }),
    ).resolves.toEqual({
      question: "What caused the outage?",
      hypotheses: [{ statement: "Database saturation", confidence: 1 }],
    });

    await expect(
      generateInvestigationHypotheses({
        provider: mockProvider(["not json"]),
        description: "Investigate outage",
        execution: { stepsExecuted: 0, collectedEvidence: [] },
      }),
    ).resolves.toEqual({
      question: "Investigate outage",
      hypotheses: [{ statement: "Investigate: Investigate outage", confidence: 0.5 }],
    });
  });
});
