import { describe, expect, it } from "vitest";

import {
  isAgentTask,
  isArtifactEditing,
  isGameScenario,
} from "../src/scenarios/primary-family-registry.js";

describe("primary family registry", () => {
  it("exports the primary family guards", async () => {
    const { createAgentTask } = await import("../src/scenarios/agent-task-factory.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");

    const agentTask = createAgentTask({
      name: "saved_task",
      spec: {
        taskPrompt: "Summarize the incident.",
        judgeRubric: "Score clarity and correctness.",
        outputFormat: "free_text",
        judgeModel: "",
        maxRounds: 1,
        qualityThreshold: 0.9,
      },
    });
    const artifactEditing = {
      describeTask: () => "task",
      getRubric: () => "rubric",
      initialArtifacts: () => [],
      getEditPrompt: () => "prompt",
      validateArtifact: () => ({}),
      evaluateEdits: () => ({}),
    };

    expect(isAgentTask(agentTask)).toBe(true);
    expect(isGameScenario(new GridCtfScenario())).toBe(true);
    expect(isArtifactEditing(artifactEditing)).toBe(true);
  });
});
