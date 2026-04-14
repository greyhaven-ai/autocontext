import { describe, expect, it, vi } from "vitest";

import {
  executeJudgeCommandWorkflow,
  getJudgeUsageExitCode,
  JUDGE_HELP_TEXT,
  parseDelegatedJudgeInput,
  planJudgeCommand,
  renderJudgeResult,
} from "../src/cli/judge-command-workflow.js";

describe("judge command workflow", () => {
  it("exposes stable help text", () => {
    expect(JUDGE_HELP_TEXT).toContain("autoctx judge");
    expect(JUDGE_HELP_TEXT).toContain("--from-stdin");
    expect(JUDGE_HELP_TEXT).toContain("--prompt");
    expect(JUDGE_HELP_TEXT).toContain("--rubric");
  });

  it("returns usage exit codes for help and missing required args", () => {
    expect(
      getJudgeUsageExitCode({
        help: true,
        "from-stdin": false,
        scenario: undefined,
        prompt: undefined,
        rubric: undefined,
        output: undefined,
      }),
    ).toBe(0);

    expect(
      getJudgeUsageExitCode({
        help: false,
        "from-stdin": false,
        scenario: undefined,
        prompt: undefined,
        rubric: undefined,
        output: undefined,
      }),
    ).toBe(1);
  });

  it("parses delegated judge stdin payloads", () => {
    expect(
      parseDelegatedJudgeInput(
        JSON.stringify({
          score: 0.85,
          reasoning: "Good",
          dimensions: { clarity: 0.9 },
        }),
      ),
    ).toEqual({
      score: 0.85,
      reasoning: "Good",
      dimensionScores: { clarity: 0.9 },
      source: "delegated",
    });
  });

  it("rejects invalid delegated judge stdin payloads", () => {
    expect(() => parseDelegatedJudgeInput("not-json")).toThrow("Invalid JSON on stdin");
    expect(() => parseDelegatedJudgeInput(JSON.stringify({ score: 2 }))).toThrow(
      "Invalid score: must be a number between 0 and 1",
    );
  });

  it("plans judge command inputs from saved scenario defaults", () => {
    expect(
      planJudgeCommand(
        {
          scenario: "saved_task",
          prompt: undefined,
          rubric: undefined,
          output: "Agent output",
          "from-stdin": false,
          help: false,
        },
        {
          taskPrompt: "Saved prompt",
          rubric: "Saved rubric",
          referenceContext: "Context",
          requiredConcepts: ["A"],
          calibrationExamples: [{ score: 0.9 }],
        },
      ),
    ).toEqual({
      taskPrompt: "Saved prompt",
      rubric: "Saved rubric",
      agentOutput: "Agent output",
      referenceContext: "Context",
      requiredConcepts: ["A"],
      calibrationExamples: [{ score: 0.9 }],
    });
  });

  it("executes judge workflow with provider/model and judge request shaping", async () => {
    const evaluate = vi.fn().mockResolvedValue({
      score: 0.91,
      reasoning: "Great",
      dimensionScores: { clarity: 0.95 },
    });
    const createJudge = vi.fn(() => ({ evaluate }));

    const result = await executeJudgeCommandWorkflow({
      plan: {
        taskPrompt: "Task",
        rubric: "Rubric",
        agentOutput: "Output",
        referenceContext: "Context",
        requiredConcepts: ["A"],
        calibrationExamples: [{ score: 0.9 }],
      },
      provider: { name: "provider" },
      model: "claude-sonnet",
      createJudge,
    });

    expect(createJudge).toHaveBeenCalledWith({
      provider: { name: "provider" },
      model: "claude-sonnet",
      rubric: "Rubric",
    });
    expect(evaluate).toHaveBeenCalledWith({
      taskPrompt: "Task",
      agentOutput: "Output",
      referenceContext: "Context",
      requiredConcepts: ["A"],
      calibrationExamples: [{ score: 0.9 }],
    });
    expect(result).toEqual({
      score: 0.91,
      reasoning: "Great",
      dimensionScores: { clarity: 0.95 },
    });
  });

  it("renders judge results as json", () => {
    expect(
      renderJudgeResult({
        score: 0.91,
        reasoning: "Great",
        dimensionScores: { clarity: 0.95 },
      }),
    ).toBe(
      JSON.stringify(
        {
          score: 0.91,
          reasoning: "Great",
          dimensionScores: { clarity: 0.95 },
        },
        null,
        2,
      ),
    );
  });
});
