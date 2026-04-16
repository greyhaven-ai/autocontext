import { describe, expect, it } from "vitest";

import {
  buildInvestigationSpec,
  generateInvestigationHypotheses,
} from "../src/investigation/investigation-generation-workflow.js";
import type { LLMProvider } from "../src/types/index.js";

function mockProvider(responses: string[]): LLMProvider {
  let callIndex = 0;
  return {
    complete: async () => ({
      text: responses[callIndex++] ?? responses[responses.length - 1] ?? "{}",
    }),
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

  it("falls back to the investigation designer prompt when strict JSON parsing fails", async () => {
    await expect(
      buildInvestigationSpec({
        provider: mockProvider([
          "not json",
          [
            "<!-- INVESTIGATION_SPEC_START -->",
            JSON.stringify({
              description: "Investigate anomaly",
              environment_description: "Production environment",
              initial_state_description: "Anomaly detected",
              evidence_pool_description: "System logs and a red herring cron alert",
              diagnosis_target: "config drift",
              success_criteria: ["identify root cause", "avoid the red herring"],
              failure_modes: ["follow the cron alert"],
              max_steps: 6,
              actions: [
                {
                  name: "inspect_logs",
                  description: "Inspect logs",
                  parameters: {},
                  preconditions: [],
                  effects: ["log_evidence_collected"],
                },
                {
                  name: "record_diagnosis",
                  description: "Record diagnosis",
                  parameters: { diagnosis: "string" },
                  preconditions: ["inspect_logs"],
                  effects: ["diagnosis_recorded"],
                },
              ],
            }),
            "<!-- INVESTIGATION_SPEC_END -->",
          ].join("\n"),
        ]),
        description: "Investigate anomaly",
      }),
    ).resolves.toMatchObject({
      description: "Investigate anomaly",
      diagnosis_target: "config drift",
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
