import { describe, expect, it, vi } from "vitest";

import type { AgentTaskInterface, ImprovementResult, LLMProvider } from "../src/types/index.js";
import { HookBus, HookEvents } from "../src/extensions/index.js";
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
      generationTimeBudgetSeconds: 11,
      deps: {
        createTask: () => task,
        createLoop: (opts) => {
          expect(opts.timeBudget).toBeDefined();
          return {
            run: vi.fn(async () => loopResult),
          };
        },
      },
    });

    expect(provider.complete).toHaveBeenCalledOnce();
    expect(result.progress).toBe(1);
    expect(result.result.scenario_name).toBe("incident_triage");
    expect(result.result.best_score).toBe(0.93);
    expect(result.result.skill_markdown).toContain("Best round: 1");
  });

  it("lets the requested generation count override saved maxRounds", async () => {
    const provider: LLMProvider = {
      name: "test-provider",
      defaultModel: () => "test-model",
      complete: vi.fn(async () => ({
        text: "Initial response",
        model: "test-model",
        usage: {},
      })),
    };
    const taskFromSpec = vi.fn((opts: {
      spec: ReturnType<typeof buildAgentTaskSolveSpec>;
      name: string;
      provider: LLMProvider;
    }) => ({
      name: "saved_task",
      spec: opts.spec,
      getTaskPrompt: () => "Do work",
      getRubric: () => "Do it well",
      describeTask: () => "Do work",
      initialState: () => ({}),
      validateContext: () => [],
      evaluateOutput: async () => ({
        score: 0.5,
        reasoning: "ok",
        dimensionScores: {},
        internalRetries: 0,
      }),
    }));

    await executeAgentTaskSolve({
      provider,
      created: {
        name: "saved_task",
        spec: {
          taskPrompt: "Do work",
          judgeRubric: "Do it well",
          maxRounds: 1,
        },
      },
      generations: 3,
      deps: {
        createTask: taskFromSpec,
        createLoop: ({ maxRounds }) => {
          expect(maxRounds).toBe(3);
          return {
            run: vi.fn(async () => ({
              rounds: [],
              bestOutput: "Initial response",
              bestScore: 0.5,
              bestRound: 1,
              totalRounds: 3,
              metThreshold: false,
              judgeFailures: 0,
              terminationReason: "max_rounds",
              dimensionTrajectory: {},
              totalInternalRetries: 0,
              durationMs: 1,
              judgeCalls: 1,
            })),
          };
        },
      },
    });

    expect(taskFromSpec.mock.calls[0]?.[0].spec.maxRounds).toBe(3);
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

  it("threads provider hooks through saved agent-task initial generation", async () => {
    const providerPrompts: string[] = [];
    const provider: LLMProvider = {
      name: "test-provider",
      defaultModel: () => "test-model",
      complete: vi.fn(async (opts) => {
        providerPrompts.push(opts.userPrompt);
        if (opts.userPrompt.includes("## Agent Output")) {
          return {
            text:
              "<!-- JUDGE_RESULT_START -->\n" +
              JSON.stringify({
                score: 0.8,
                reasoning: "Good",
                dimensions: { clarity: 0.8 },
              }) +
              "\n<!-- JUDGE_RESULT_END -->",
            model: "test-model",
            usage: {},
          };
        }
        return {
          text: "Initial provider answer",
          model: "test-model",
          usage: {},
        };
      }),
    };
    const bus = new HookBus();
    const seen: string[] = [];
    bus.on(HookEvents.BEFORE_PROVIDER_REQUEST, (event) => {
      if (event.payload.role === "agent_task_initial") {
        seen.push("before_initial");
        return { userPrompt: `${event.payload.userPrompt}\nhook provider request` };
      }
      return undefined;
    });
    bus.on(HookEvents.AFTER_PROVIDER_RESPONSE, (event) => {
      if (event.payload.role === "agent_task_initial") {
        seen.push("after_initial");
        return { text: "Initial answer rewritten by provider hook" };
      }
      return undefined;
    });

    const result = await executeAgentTaskSolve({
      provider,
      hookBus: bus,
      created: {
        name: "hooked_task",
        spec: {
          taskPrompt: "Write a concise answer.",
          judgeRubric: "Score clarity.",
          outputFormat: "free_text",
        },
      },
      generations: 1,
    });

    expect(seen).toEqual(["before_initial", "after_initial"]);
    expect(providerPrompts[0]).toContain("hook provider request");
    expect(result.result.skill_markdown).toContain(
      "Initial answer rewritten by provider hook",
    );
  });
});
