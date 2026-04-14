import { describe, expect, it, vi } from "vitest";

import {
  prepareInvestigationScenario,
} from "../src/investigation/investigation-scenario-preparation-workflow.js";
import type { LLMProvider } from "../src/types/index.js";

describe("investigation scenario preparation workflow", () => {
  it("returns invalid preparation results when generated investigation source fails validation", async () => {
    const result = await prepareInvestigationScenario(
      {
        provider: {} as LLMProvider,
        description: "Investigate checkout regression",
        knowledgeRoot: "/tmp/knowledge",
        name: "checkout_rca",
      },
      {
        buildInvestigationSpec: vi.fn(async () => ({ diagnosis_target: "config regression" })),
        healSpec: vi.fn((spec) => spec),
        generateScenarioSource: vi.fn(() => "module.exports = { scenario: {} }"),
        validateGeneratedScenario: vi.fn(async () => ({ valid: false, errors: ["spec invalid"] })) as any,
        persistInvestigationArtifacts: vi.fn(),
      },
    );

    expect(result).toEqual({
      status: "invalid",
      errors: ["spec invalid"],
    });
  });

  it("returns prepared scenario details after healing, validation, and persistence", async () => {
    const persistInvestigationArtifacts = vi.fn(() => "/tmp/knowledge/_investigations/incident_rca");

    const result = await prepareInvestigationScenario(
      {
        provider: {} as LLMProvider,
        description: "Investigate incident",
        knowledgeRoot: "/tmp/knowledge",
        name: "incident_rca",
      },
      {
        buildInvestigationSpec: vi.fn(async () => ({ diagnosis_target: "config drift" })),
        healSpec: vi.fn((spec) => ({ ...spec, healed: true })),
        generateScenarioSource: vi.fn(() => "module.exports = { scenario: {} }"),
        validateGeneratedScenario: vi.fn(async () => ({ valid: true, errors: [] })) as any,
        persistInvestigationArtifacts,
      },
    );

    expect(result).toEqual({
      status: "prepared",
      healedSpec: { diagnosis_target: "config drift", healed: true },
      source: "module.exports = { scenario: {} }",
      investigationDir: "/tmp/knowledge/_investigations/incident_rca",
    });
    expect(persistInvestigationArtifacts).toHaveBeenCalledWith(
      "/tmp/knowledge",
      "incident_rca",
      { diagnosis_target: "config drift", healed: true },
      "module.exports = { scenario: {} }",
    );
  });
});
