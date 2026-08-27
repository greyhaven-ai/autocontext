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
      authoritativeParseFailed: false,
      judgeFailed: false,
      source: "delegated",
    });
  });

  it("rejects invalid delegated judge stdin payloads", () => {
    expect(() => parseDelegatedJudgeInput("not-json")).toThrow("Invalid JSON on stdin");
    expect(() => parseDelegatedJudgeInput(JSON.stringify({ score: 2 }))).toThrow(
      "Invalid score: must be a number between 0 and 1",
    );
    expect(() => parseDelegatedJudgeInput(JSON.stringify({
      score: 0.5,
      authoritativeParseFailed: "false",
    }))).toThrow("Invalid authoritativeParseFailed: must be a boolean");
    expect(() => parseDelegatedJudgeInput(JSON.stringify({
      score: 0.5,
      parseMethod: "unknown",
    }))).toThrow("Invalid parseMethod");
    expect(() => parseDelegatedJudgeInput(JSON.stringify({
      score: 0.5,
      dimensions: { accuracy: "high" },
    }))).toThrow("Invalid dimension score 'accuracy'");
  });

  it.each([
    {
      input: { score: 0, parseMethod: "none" },
      expected: { authoritativeParseFailed: true, judgeFailed: true, parseMethod: "none" },
    },
    {
      input: {
        score: 0,
        authoritativeParseFailed: true,
        judgeFailed: false,
        parseMethod: "markers",
      },
      expected: {
        authoritativeParseFailed: true,
        judgeFailed: true,
        parseMethod: "markers",
      },
    },
    {
      input: { score: 0.4, authoritativeParseFailed: false, judgeFailed: true },
      expected: { authoritativeParseFailed: false, judgeFailed: true },
    },
    {
      input: { score: 0.8, authoritativeParseFailed: false, judgeFailed: false },
      expected: { authoritativeParseFailed: false, judgeFailed: false },
    },
  ])("preserves delegated failure provenance monotonically", ({ input, expected }) => {
    expect(parseDelegatedJudgeInput(JSON.stringify(input))).toMatchObject(expected);
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

  it("retains the structured marker and evaluator-only context for native judging", () => {
    const plan = planJudgeCommand(
      {
        scenario: "structured_task",
        prompt: undefined,
        rubric: undefined,
        output: "Candidate output",
      },
      {
        name: "structured_task",
        taskPrompt: "Rendered candidate prompt",
        rubric: "Saved rubric",
        agentTaskSpec: {
          improvementTaskContractVersion: 1,
          taskPrompt: "Raw candidate prompt",
          judgeRubric: "Saved rubric",
          outputFormat: "free_text",
          judgeModel: "",
          evaluationContext: "EVALUATOR_ONLY_CASE",
          maxRounds: 2,
          qualityThreshold: 0.9,
        },
      },
    );

    expect(plan.nativeAgentTask).toMatchObject({
      name: "structured_task",
      spec: {
        improvementTaskContractVersion: 1,
        taskPrompt: "Raw candidate prompt",
        evaluationContext: "EVALUATOR_ONLY_CASE",
      },
    });
    expect(plan.taskPrompt).not.toContain("EVALUATOR_ONLY_CASE");
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
      authoritativeParseFailed: false,
      judgeFailed: false,
    });
  });

  it("marks generic parseMethod none as an authoritative judge failure", async () => {
    const result = await executeJudgeCommandWorkflow({
      plan: {
        taskPrompt: "Task",
        rubric: "Rubric",
        agentOutput: "Output",
      },
      provider: { name: "provider" },
      createJudge: () => ({
        evaluate: async () => ({
          score: 0,
          reasoning: "Unable to parse evaluator output",
          dimensionScores: {},
          authoritativeParseFailed: false,
          judgeFailed: false,
          parseMethod: "none",
        }),
      }),
    });

    expect(result).toEqual({
      score: 0,
      reasoning: "Unable to parse evaluator output",
      dimensionScores: {},
      authoritativeParseFailed: true,
      judgeFailed: true,
      parseMethod: "none",
    });
  });

  it("routes structured saved scenarios through the native dual-context task", async () => {
    const nativeResult = {
      score: 0.72,
      reasoning: "Candidate-safe feedback",
      dimensionScores: { accuracy: 0.72 },
    };
    const evaluateOutput = vi.fn().mockResolvedValue(nativeResult);
    const prepareContext = vi.fn(async (state: Record<string, unknown>) => ({
      ...state,
      contextPrepared: true,
    }));
    const validateContext = vi.fn(() => []);
    const createAgentTask = vi.fn(() => ({
      initialState: () => ({ structured: true }),
      prepareContext,
      validateContext,
      evaluateOutput,
    }));
    const createJudge = vi.fn(() => {
      throw new Error("generic judge must not be created");
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
      calibrationExamples: [{ score: 1 }],
      maxRounds: 2,
      qualityThreshold: 0.9,
    };

    await expect(
      executeJudgeCommandWorkflow({
        plan: {
          taskPrompt: "Task",
          rubric: "Rubric",
          agentOutput: "Candidate",
          nativeAgentTask: { name: "structured", spec },
        },
        provider: { name: "provider" },
        model: "model",
        createJudge,
        createAgentTask,
      }),
    ).resolves.toEqual({
      ...nativeResult,
      authoritativeParseFailed: false,
      judgeFailed: false,
    });

    expect(createJudge).not.toHaveBeenCalled();
    expect(createAgentTask).toHaveBeenCalledWith({
      name: "structured",
      spec,
      provider: { name: "provider" },
      model: "model",
    });
    expect(prepareContext).toHaveBeenCalledWith({ structured: true });
    expect(validateContext).toHaveBeenCalledWith({
      structured: true,
      contextPrepared: true,
    });
    expect(evaluateOutput).toHaveBeenCalledWith(
      "Candidate",
      { structured: true, contextPrepared: true },
      {
        referenceContext: "VISIBLE_REFERENCE",
        requiredConcepts: ["accuracy"],
        calibrationExamples: [{ score: 1 }],
      },
    );
  });

  it("rejects native judging when prepared task context is invalid", async () => {
    const evaluateOutput = vi.fn();

    await expect(
      executeJudgeCommandWorkflow({
        plan: {
          taskPrompt: "Task",
          rubric: "Rubric",
          agentOutput: "Candidate",
          nativeAgentTask: {
            name: "structured",
            spec: {
              improvementTaskContractVersion: 1,
              taskPrompt: "Task",
              judgeRubric: "Rubric",
              outputFormat: "free_text",
              judgeModel: "",
              requiredContextKeys: ["preparedEvidence"],
              maxRounds: 1,
              qualityThreshold: 0.9,
            },
          },
        },
        provider: { name: "provider" },
        createJudge: vi.fn(() => {
          throw new Error("generic judge must not be created");
        }),
        createAgentTask: () => ({
          initialState: () => ({}),
          prepareContext: async (state) => state,
          validateContext: () => ["Missing required context key: preparedEvidence"],
          evaluateOutput,
        }),
      }),
    ).rejects.toThrow(
      "agent_task context preparation failed: Missing required context key: preparedEvidence",
    );
    expect(evaluateOutput).not.toHaveBeenCalled();
  });

  it("surfaces authoritative native parse failures as judge failures", async () => {
    const result = await executeJudgeCommandWorkflow({
      plan: {
        taskPrompt: "Task",
        rubric: "Rubric",
        agentOutput: "Candidate",
        nativeAgentTask: {
          name: "structured",
          spec: {
            improvementTaskContractVersion: 1,
            taskPrompt: "Task",
            judgeRubric: "Rubric",
            outputFormat: "free_text",
            judgeModel: "",
            maxRounds: 1,
            qualityThreshold: 0.9,
          },
        },
      },
      provider: { name: "provider" },
      createJudge: vi.fn(() => {
        throw new Error("generic judge must not be created");
      }),
      createAgentTask: () => ({
        initialState: () => ({}),
        evaluateOutput: async () => ({
          score: 0,
          reasoning: "Authoritative evaluator response could not be parsed",
          dimensionScores: {},
          authoritativeParseFailed: true,
          judgeFailed: false,
        }),
      }),
    });

    expect(result).toMatchObject({
      score: 0,
      authoritativeParseFailed: true,
      judgeFailed: true,
    });
    expect(JSON.parse(renderJudgeResult(result))).toMatchObject({
      score: 0,
      authoritativeParseFailed: true,
      judgeFailed: true,
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
          authoritativeParseFailed: false,
          judgeFailed: false,
        },
        null,
        2,
      ),
    );
  });
});
