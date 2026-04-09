import { describe, expect, it } from "vitest";

import { generateAgentTaskSource } from "../src/scenarios/codegen/agent-task-codegen.js";

describe("template-backed agent-task codegen", () => {
  it("generates agent-task code with all placeholders resolved", () => {
    const source = generateAgentTaskSource(
      {
        taskPrompt: "Write a poem about clouds",
        rubric: "Evaluate creativity and imagery",
        description: "Poetry task",
        outputFormat: "markdown",
        maxRounds: 2,
        qualityThreshold: 0.8,
      },
      "poetry_task",
    );

    expect(source).toContain("poetry_task");
    expect(source).toContain("Write a poem about clouds");
    expect(source).not.toMatch(/__[A-Z0-9_]+__/);
    expect(() => new Function(source)).not.toThrow();
  });
});
