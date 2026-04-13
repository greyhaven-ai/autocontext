import { describe, expect, it, vi } from "vitest";

import type { AgentTaskInterface, ImprovementResult, LLMProvider } from "../src/types/index.js";
import {
  buildAgentTaskSolveSpec,
  executeAgentTaskSolve,
} from "../src/knowledge/agent-task-solve-execution.js";

describe("agent-task solve execution", () => {
  it("builds agent-task solve specs from mixed naming conventions", () => {
    const spec = buildAgentTaskSolveSpec(
      {
        task_prompt: "Summarize incident reports",
        rubric: "Evaluate completeness",
        output_format: "free_text",
        max_rounds: "3",
        quality_threshold: "0.85",
        reference_context: "PagerDuty timeline",
        required_concepts: ["severity", "owner"],
      },
      1,
    );

    expect(spec.taskPrompt).toBe("Summarize incident reports");
    expect(spec.judgeRubric).toBe("Evaluate completeness");
    expect(spec.maxRounds).toBe(3);
    expect(spec.qualityThreshold).toBe(0.85);
    expect(spec.referenceContext).toBe("PagerDuty timeline");
    expect(spec.requiredConcepts).toEqual(["severity", "owner"]);
  });

  it("executes the agent-task solve workflow and builds the exported package", async () => {
    const provider: LLMProvider = {
      name: "test-provider",
      defaultModel: () => "test-model",
      complete: vi.fn(async () => ({
        text: "Initial response with owner and severity",
        model: "test-model",
        usage: {},
      })),
    };

    const task: AgentTaskInterface & { name: string; spec: ReturnType<typeof buildAgentTaskSolveSpec> } = {
      name: "incident_triage",
      spec: buildAgentTaskSolveSpec(
        {
          taskPrompt: "Summarize incident reports",
          rubric: "Evaluate completeness",
          description: "Incident triage task",
          maxRounds: 2,
          qualityThreshold: 0.9,
        },
        2,
      ),
      getTaskPrompt: () => "Summarize incident reports",
      getRubric: () => "Evaluate completeness",
      describeTask: () => "Summarize incident reports",
      initialState: () => ({ raw: true }),
      prepareContext: async (state) => ({ ...state, prepared: true }),
      validateContext: () => [],
      evaluateOutput: async () => ({
        score: 0.9,
        reasoning: "Good output",
        dimensionScores: { completeness: 0.9 },
        internalRetries: 0,
      }),
    };

    const loopResult: ImprovementResult = {
      rounds: [
        {
          roundNumber: 1,
          output: "Initial response with owner and severity",
          score: 0.93,
          reasoning: "Added owner assignment and severity classification.",
          dimensionScores: { completeness: 0.93 },
          isRevision: false,
          judgeFailed: false,
        },
      ],
      bestOutput: "Initial response with owner and severity",
      bestScore: 0.93,
      bestRound: 1,
      totalRounds: 1,
      metThreshold: true,
      judgeFailures: 0,
      terminationReason: "threshold_met",
      dimensionTrajectory: { completeness: [0.93] },
      totalInternalRetries: 0,
      durationMs: 1,
      judgeCalls: 1,
    };

    const result = await executeAgentTaskSolve({
      provider,
      created: {
        name: "incident_triage",
        spec: {
          taskPrompt: "Summarize incident reports",
          rubric: "Evaluate completeness",
          description: "Incident triage task",
          maxRounds: 2,
          qualityThreshold: 0.9,
        },
      },
      generations: 2,
      deps: {
        createTask: () => task,
        createLoop: () => ({
          run: vi.fn(async () => loopResult),
        }),
      },
    });

    expect(provider.complete).toHaveBeenCalledOnce();
    expect(result.progress).toBe(1);
    expect(result.result.scenario_name).toBe("incident_triage");
    expect(result.result.best_score).toBe(0.93);
    expect(result.result.skill_markdown).toContain("Best round: 1");
  });

  it("fails when prepared context is invalid", async () => {
    const provider: LLMProvider = {
      name: "test-provider",
      defaultModel: () => "test-model",
      complete: vi.fn(async () => ({ text: "ignored", model: "test-model", usage: {} })),
    };

    const invalidTask: AgentTaskInterface & { name: string; spec: ReturnType<typeof buildAgentTaskSolveSpec> } = {
      name: "incident_triage",
      spec: buildAgentTaskSolveSpec(
        {
          taskPrompt: "Summarize incident reports",
          rubric: "Evaluate completeness",
          description: "Incident triage task",
        },
        1,
      ),
      getTaskPrompt: () => "Summarize incident reports",
      getRubric: () => "Evaluate completeness",
      describeTask: () => "Summarize incident reports",
      initialState: () => ({ raw: true }),
      prepareContext: async (state) => ({ ...state }),
      validateContext: () => ["missing required context key: 'timeline'"],
      evaluateOutput: async () => ({
        score: 0,
        reasoning: "unused",
        dimensionScores: {},
        internalRetries: 0,
      }),
    };

    await expect(
      executeAgentTaskSolve({
        provider,
        created: {
          name: "incident_triage",
          spec: {
            taskPrompt: "Summarize incident reports",
            rubric: "Evaluate completeness",
            description: "Incident triage task",
          },
        },
        generations: 1,
        deps: {
          createTask: () => invalidTask,
          createLoop: () => ({
            run: vi.fn(),
          }),
        },
      }),
    ).rejects.toThrow("agent_task context preparation failed: missing required context key: 'timeline'");
  });
});
