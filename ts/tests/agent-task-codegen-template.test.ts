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

  it("preserves placeholder-like text inside task fields", () => {
    const source = generateAgentTaskSource(
      {
        taskPrompt: "__QUALITY_THRESHOLD__ marker",
        rubric: "Evaluate clarity and structure",
        description: "__MAX_ROUNDS__ desc",
        outputFormat: "markdown",
        maxRounds: 7,
        qualityThreshold: 0.8,
      },
      "poetry_task",
    );

    expect(source).toContain('return "__QUALITY_THRESHOLD__ marker";');
    expect(source).toContain('return "__MAX_ROUNDS__ desc";');
    expect(source).not.toContain('return "0.8 marker";');
    expect(source).not.toContain('return "7 desc";');
  });

  it("does not reject placeholder-like rubric text from user data", () => {
    expect(() =>
      generateAgentTaskSource(
        {
          taskPrompt: "Write a poem about clouds",
          rubric: "__SAFE_MODE__",
          description: "Poetry task",
          outputFormat: "markdown",
          maxRounds: 2,
          qualityThreshold: 0.8,
        },
        "poetry_task",
      ),
    ).not.toThrow();
  });
});
