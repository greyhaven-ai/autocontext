import { describe, expect, it } from "vitest";

import {
  buildAgentTaskMaterializeInput,
  buildAgentTaskPersistedSpecFields,
} from "../src/scenarios/materialize-agent-task-planning.js";

describe("materialize agent-task planning", () => {
  it("builds normalized agent-task schema input from healed specs", () => {
    expect(
      buildAgentTaskMaterializeInput({
        improvement_task_contract_version: 1,
        task_prompt: "Write a poem",
        rubric: "Judge creativity",
        max_rounds: 2,
        quality_threshold: 0.8,
        reference_sources: ["docs"],
      }),
    ).toMatchObject({
      improvementTaskContractVersion: 1,
      taskPrompt: "Write a poem",
      judgeRubric: "Judge creativity",
      maxRounds: 2,
      qualityThreshold: 0.8,
      referenceSources: ["docs"],
    });
  });

  it("builds persisted camelCase agent-task fields from a parsed spec", () => {
    expect(
      buildAgentTaskPersistedSpecFields({
        improvementTaskContractVersion: 1,
        taskPrompt: "Write a poem",
        judgeRubric: "Judge creativity",
        outputFormat: "free_text",
        judgeModel: "",
        difficultyTiers: null,
        referenceContext: null,
        referenceSources: ["docs"],
        requiredConcepts: null,
        calibrationExamples: null,
        contextPreparation: null,
        requiredContextKeys: null,
        maxRounds: 2,
        qualityThreshold: 0.8,
        revisionPrompt: null,
        sampleInput: null,
      }),
    ).toMatchObject({
      improvementTaskContractVersion: 1,
      taskPrompt: "Write a poem",
      judgeRubric: "Judge creativity",
      rubric: "Judge creativity",
      referenceSources: ["docs"],
      maxRounds: 2,
      qualityThreshold: 0.8,
    });
  });
});
