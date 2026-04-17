import { describe, expect, it, vi } from "vitest";

import {
  executeImproveCommandWorkflow,
  getImproveUsageExitCode,
  IMPROVE_HELP_TEXT,
  planImproveCommand,
  renderImproveResult,
} from "../src/cli/improve-command-workflow.js";

describe("improve command workflow", () => {
  it("exposes stable help text", () => {
    expect(IMPROVE_HELP_TEXT).toContain("autoctx improve");
    expect(IMPROVE_HELP_TEXT).toContain("--prompt");
    expect(IMPROVE_HELP_TEXT).toContain("--output");
    expect(IMPROVE_HELP_TEXT).toContain("--rlm");
  });

  it("returns usage exit codes for help and missing required inputs", () => {
    expect(
      getImproveUsageExitCode({
        help: true,
        scenario: undefined,
        prompt: undefined,
        rubric: undefined,
        output: undefined,
        rlm: false,
      }),
    ).toBe(0);

    expect(
      getImproveUsageExitCode({
        help: false,
        scenario: undefined,
        prompt: undefined,
        rubric: undefined,
        output: undefined,
        rlm: false,
      }),
    ).toBe(1);
  });

  it("accepts prompt and rubric without requiring an initial output", () => {
    expect(
      getImproveUsageExitCode({
        help: false,
        scenario: undefined,
        prompt: "Write a haiku about distributed systems",
        rubric: "Score syllable accuracy and relevance",
        output: undefined,
        rlm: false,
      }),
    ).toBeNull();
  });

  it("plans improve command inputs from saved scenario defaults", () => {
    const parsePositiveInteger = vi.fn((raw: string) => Number.parseInt(raw, 10));
    expect(
      planImproveCommand(
        {
          scenario: "saved_task",
          prompt: undefined,
          rubric: undefined,
          output: undefined,
          rounds: undefined,
          threshold: undefined,
          "min-rounds": undefined,
          rlm: true,
          "rlm-model": "gpt-4.1",
          "rlm-turns": "8",
          "rlm-max-tokens": "4096",
          "rlm-temperature": "0.3",
          "rlm-max-stdout": "12000",
          "rlm-timeout-ms": "15000",
          "rlm-memory-mb": "128",
          verbose: true,
          help: false,
        },
        {
          taskPrompt: "Saved prompt",
          rubric: "Saved rubric",
          maxRounds: 6,
          qualityThreshold: 0.92,
          revisionPrompt: "Revise carefully",
        },
        parsePositiveInteger,
      ),
    ).toEqual({
      taskPrompt: "Saved prompt",
      rubric: "Saved rubric",
      maxRounds: 6,
      qualityThreshold: 0.92,
      minRounds: 1,
      initialOutput: undefined,
      verbose: true,
      revisionPrompt: "Revise carefully",
      rlmConfig: {
        enabled: true,
        model: "gpt-4.1",
        maxTurns: 8,
        maxTokensPerTurn: 4096,
        temperature: 0.3,
        maxStdoutChars: 12000,
        codeTimeoutMs: 15000,
        memoryLimitMb: 128,
      },
    });
  });

  it("executes improve workflow and generates initial output when not provided", async () => {
    const generateOutput = vi.fn().mockResolvedValue("generated output");
    const getRlmSessions = vi.fn(() => [{ round: 1 }]);
    const task = { generateOutput, getRlmSessions };
    const createTask = vi.fn(() => task);
    const run = vi.fn().mockResolvedValue({
      totalRounds: 2,
      metThreshold: true,
      bestScore: 0.95,
      bestRound: 2,
      judgeFailures: 0,
      terminationReason: "threshold_met",
      totalInternalRetries: 1,
      dimensionTrajectory: [{ round: 1, dimensions: { clarity: 0.7 } }],
      bestOutput: "improved output",
      rounds: [
        {
          roundNumber: 1,
          score: 0.8,
          dimensionScores: { clarity: 0.8 },
          reasoning: "Improved clarity",
          isRevision: true,
          judgeFailed: false,
        },
      ],
    });
    const createLoop = vi.fn(() => ({ run }));

    const result = await executeImproveCommandWorkflow({
      plan: {
        taskPrompt: "Task",
        rubric: "Rubric",
        maxRounds: 3,
        qualityThreshold: 0.9,
        minRounds: 1,
        initialOutput: undefined,
        verbose: true,
        revisionPrompt: "Revise",
        rlmConfig: { enabled: true },
      },
      provider: { name: "provider" },
      model: "claude-sonnet",
      savedScenario: {
        referenceContext: "Context",
        requiredConcepts: ["A"],
        calibrationExamples: [{ output: "x", score: 0.9, reasoning: "good" }],
      },
      createTask,
      createLoop,
      now: vi.fn().mockReturnValueOnce(100).mockReturnValueOnce(350),
    });

    expect(createTask).toHaveBeenCalledWith(
      "Task",
      "Rubric",
      { name: "provider" },
      "claude-sonnet",
      "Revise",
      { enabled: true },
    );
    expect(generateOutput).toHaveBeenCalledWith({
      referenceContext: "Context",
      requiredConcepts: ["A"],
    });
    expect(createLoop).toHaveBeenCalledWith({
      task,
      maxRounds: 3,
      qualityThreshold: 0.9,
      minRounds: 1,
    });
    expect(run).toHaveBeenCalledWith({
      initialOutput: "generated output",
      state: {},
      referenceContext: "Context",
      requiredConcepts: ["A"],
      calibrationExamples: [{ output: "x", score: 0.9, reasoning: "good" }],
    });
    expect(result.durationMs).toBe(250);
    expect(result.rlmSessions).toEqual([{ round: 1 }]);
  });

  it("renders verbose rounds to stderr and final json to stdout", () => {
    const rendered = renderImproveResult(
      {
        totalRounds: 2,
        metThreshold: true,
        bestScore: 0.95,
        bestRound: 2,
        judgeFailures: 0,
        terminationReason: "threshold_met",
        totalInternalRetries: 1,
        dimensionTrajectory: [{ round: 1, dimensions: { clarity: 0.7 } }],
        bestOutput: "improved output",
        durationMs: 250,
        rlmSessions: [{ round: 1 }],
        rounds: [
          {
            roundNumber: 1,
            score: 0.8,
            dimensionScores: { clarity: 0.8 },
            reasoning: "Improved clarity and completeness across the whole answer.",
            isRevision: true,
            judgeFailed: false,
          },
        ],
      },
      true,
    );

    expect(rendered.stderrLines).toHaveLength(1);
    expect(JSON.parse(rendered.stderrLines[0] ?? "{}")).toMatchObject({
      round: 1,
      score: 0.8,
      isRevision: true,
    });
    expect(JSON.parse(rendered.stdout)).toMatchObject({
      totalRounds: 2,
      metThreshold: true,
      bestScore: 0.95,
      durationMs: 250,
      rlmSessions: [{ round: 1 }],
    });
  });
});
