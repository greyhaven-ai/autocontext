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

  it("retains structured identity and evaluator-only context in an improve plan", () => {
    const plan = planImproveCommand(
      {
        scenario: "structured_task",
        prompt: undefined,
        rubric: undefined,
        output: "Initial candidate",
        rlm: false,
      },
      {
        name: "structured_task",
        taskPrompt: "Rendered candidate prompt",
        rubric: "Saved rubric",
        referenceContext: "VISIBLE_REFERENCE",
        agentTaskSpec: {
          improvementTaskContractVersion: 1,
          taskPrompt: "Raw candidate prompt",
          judgeRubric: "Saved rubric",
          outputFormat: "free_text",
          judgeModel: "",
          referenceContext: "VISIBLE_REFERENCE",
          evaluationContext: "EVALUATOR_ONLY_CASE",
          maxRounds: 3,
          qualityThreshold: 0.9,
        },
      },
      (raw) => Number.parseInt(raw, 10),
    );

    expect(plan.nativeAgentTask).toMatchObject({
      name: "structured_task",
      spec: {
        improvementTaskContractVersion: 1,
        taskPrompt: "Raw candidate prompt",
        referenceContext: "VISIBLE_REFERENCE",
        evaluationContext: "EVALUATOR_ONLY_CASE",
      },
    });
    expect(plan.taskPrompt).not.toContain("EVALUATOR_ONLY_CASE");
  });

  it("rejects RLM for a saved structured task instead of bypassing native revisions", () => {
    expect(() =>
      planImproveCommand(
        {
          scenario: "structured_task",
          output: "Initial candidate",
          rlm: true,
        },
        {
          name: "structured_task",
          taskPrompt: "Candidate prompt",
          rubric: "Rubric",
          agentTaskSpec: {
            improvementTaskContractVersion: 1,
            taskPrompt: "Candidate prompt",
            judgeRubric: "Rubric",
            outputFormat: "free_text",
            judgeModel: "",
            evaluationContext: "EVALUATOR_ONLY_CASE",
            maxRounds: 2,
            qualityThreshold: 0.9,
          },
        },
        (raw) => Number.parseInt(raw, 10),
      ),
    ).toThrow(/--rlm is not supported for saved structured tasks/i);
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
      true,
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

  it("keeps legacy saved-spec references judge-only during improve generation", async () => {
    const savedScenario = {
      name: "legacy-task",
      taskPrompt: "Legacy public task",
      rubric: "Legacy judge rubric",
      referenceContext: "JUDGE_ONLY_REFERENCE",
      requiredConcepts: ["JUDGE_ONLY_CONCEPT"],
      agentTaskSpec: {
        taskPrompt: "Legacy public task",
        judgeRubric: "Legacy judge rubric",
        outputFormat: "free_text" as const,
        judgeModel: "",
        referenceContext: "JUDGE_ONLY_REFERENCE",
        requiredConcepts: ["JUDGE_ONLY_CONCEPT"],
        maxRounds: 1,
        qualityThreshold: 0.9,
      },
    };
    const plan = planImproveCommand(
      { scenario: "legacy-task" },
      savedScenario,
      (raw) => Number.parseInt(raw, 10),
    );
    const generateOutput = vi.fn(async () => "generated");
    const createTask = vi.fn(() => ({ generateOutput, getRlmSessions: () => [] }));
    const run = vi.fn(async () => ({
      totalRounds: 1,
      metThreshold: false,
      bestScore: 0.5,
      bestRound: 1,
      judgeFailures: 0,
      terminationReason: "max_rounds",
      totalInternalRetries: 0,
      dimensionTrajectory: {},
      bestOutput: "generated",
      rounds: [],
    }));

    await executeImproveCommandWorkflow({
      plan,
      provider: { name: "candidate" },
      model: "candidate-model",
      savedScenario,
      createTask,
      createLoop: () => ({ run }),
      now: () => 0,
    });

    expect(createTask).toHaveBeenCalledWith(
      "Legacy public task",
      "Legacy judge rubric",
      { name: "candidate" },
      "candidate-model",
      undefined,
      expect.any(Object),
      false,
    );
    expect(generateOutput).toHaveBeenCalledWith({
      referenceContext: undefined,
      requiredConcepts: undefined,
    });
    expect(run).toHaveBeenCalledWith(expect.objectContaining({
      referenceContext: "JUDGE_ONLY_REFERENCE",
      requiredConcepts: ["JUDGE_ONLY_CONCEPT"],
    }));
  });

  it("executes a saved structured task through native context preparation and improvement", async () => {
    const generateOutput = vi.fn().mockResolvedValue("generated output");
    const structuredTask = {
      initialState: vi.fn(() => ({ structured: true })),
      prepareContext: vi.fn(async (state: Record<string, unknown>) => ({ ...state, ready: true })),
      validateContext: vi.fn(() => []),
      generateOutput,
      getRlmSessions: vi.fn(() => []),
    };
    const createStructuredTask = vi.fn(() => structuredTask);
    const createTask = vi.fn(() => {
      throw new Error("generic task must not be created");
    });
    const run = vi.fn().mockResolvedValue({
      totalRounds: 1,
      metThreshold: true,
      bestScore: 0.95,
      bestRound: 1,
      judgeFailures: 0,
      terminationReason: "threshold_met",
      totalInternalRetries: 0,
      dimensionTrajectory: {},
      bestOutput: "generated output",
      rounds: [],
    });
    const spec = {
      improvementTaskContractVersion: 1 as const,
      taskPrompt: "Task",
      judgeRubric: "Rubric",
      outputFormat: "free_text" as const,
      judgeModel: "",
      referenceContext: "VISIBLE_REFERENCE",
      evaluationContext: "EVALUATOR_ONLY_CASE",
      requiredConcepts: ["accuracy"],
      maxRounds: 2,
      qualityThreshold: 0.9,
    };

    await executeImproveCommandWorkflow({
      plan: {
        taskPrompt: "Task",
        rubric: "Rubric",
        maxRounds: 2,
        qualityThreshold: 0.9,
        minRounds: 1,
        verbose: false,
        rlmConfig: { enabled: false },
        nativeAgentTask: { name: "structured", spec },
      },
      provider: { name: "provider" },
      model: "model",
      savedScenario: {
        referenceContext: "VISIBLE_REFERENCE",
        requiredConcepts: ["accuracy"],
      },
      createTask,
      createStructuredTask,
      createLoop: vi.fn(() => ({ run })),
      now: vi.fn().mockReturnValueOnce(100).mockReturnValueOnce(150),
    });

    expect(createTask).not.toHaveBeenCalled();
    expect(createStructuredTask).toHaveBeenCalledWith({
      name: "structured",
      spec,
      provider: { name: "provider" },
      model: "model",
    });
    expect(generateOutput).toHaveBeenCalledWith({
      referenceContext: "VISIBLE_REFERENCE",
      requiredConcepts: ["accuracy"],
      state: { structured: true, ready: true },
    });
    expect(run).toHaveBeenCalledWith({
      initialOutput: "generated output",
      state: { structured: true, ready: true },
      referenceContext: "VISIBLE_REFERENCE",
      requiredConcepts: ["accuracy"],
      calibrationExamples: undefined,
    });
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
