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
        improvement_task_contract_version: 1,
        task_prompt: "Summarize incident reports",
        rubric: "Evaluate completeness",
        output_format: "free_text",
        max_rounds: "3",
        quality_threshold: "0.85",
        reference_context: "PagerDuty timeline",
        evaluation_context: "Hidden evaluator case",
        required_concepts: ["severity", "owner"],
      },
      1,
    );

    expect(spec.taskPrompt).toBe("Summarize incident reports");
    expect(spec.improvementTaskContractVersion).toBe(1);
    expect(spec.judgeRubric).toBe("Evaluate completeness");
    expect(spec.maxRounds).toBe(3);
    expect(spec.qualityThreshold).toBe(0.85);
    expect(spec.referenceContext).toBe("PagerDuty timeline");
    expect(spec.evaluationContext).toBe("Hidden evaluator case");
    expect(spec.requiredConcepts).toEqual(["severity", "owner"]);
  });

  it("uses and closes an owned no-tools isolate for an evaluator-bearing initial draft", async () => {
    const policies: unknown[] = [];
    const prompts: string[] = [];
    let closes = 0;
    const provider: LLMProvider = {
      name: "tool-capable-runtime",
      defaultModel: () => "test-model",
      complete: async () => {
        throw new Error("base provider must not generate the private task draft");
      },
      createIsolatedProvider: (policy) => {
        policies.push(policy);
        return {
          name: "no-tools-draft-isolate",
          defaultModel: () => "test-model",
          complete: async (request) => {
            prompts.push(request.userPrompt);
            return { text: "Isolated initial response", model: "test-model", usage: {} };
          },
          close: () => {
            closes += 1;
          },
        };
      },
    };
    const stopAfterDraft = new Error("stop after isolated draft");

    await expect(
      executeAgentTaskSolve({
        provider,
        created: {
          name: "private_draft",
          spec: {
            taskPrompt: "Draft the candidate response.",
            judgeRubric: "Evaluate it.",
            evaluationContext: "DRAFT_PRIVATE_SENTINEL",
          },
        },
        generations: 1,
        deps: {
          createLoop: () => ({
            run: vi.fn(async ({ initialOutput }) => {
              expect(initialOutput).toBe("Isolated initial response");
              throw stopAfterDraft;
            }),
          }),
        },
      }),
    ).rejects.toBe(stopAfterDraft);

    expect(policies).toEqual([{ noTools: true }]);
    expect(closes).toBe(1);
    expect(prompts).toEqual(["Draft the candidate response."]);
    expect(prompts[0]).not.toContain("DRAFT_PRIVATE_SENTINEL");
  });

  it("rejects invalid structured-v1 JSON before the solve loop can judge or retain it", async () => {
    const run = vi.fn();
    const provider: LLMProvider = {
      name: "candidate",
      defaultModel: () => "candidate-model",
      complete: async () => ({ text: "not valid JSON", usage: {} }),
    };

    await expect(executeAgentTaskSolve({
      provider,
      created: {
        name: "invalid_json_solve",
        spec: {
          improvementTaskContractVersion: 1,
          taskPrompt: "Return JSON.",
          judgeRubric: "Evaluate JSON.",
          outputFormat: "json_schema",
        },
      },
      generations: 1,
      deps: { createLoop: () => ({ run }) },
    })).rejects.toThrow(/must be valid JSON because output_format is json_schema/);

    expect(run).not.toHaveBeenCalled();
  });

  it("executes the agent-task solve workflow and builds the exported package", async () => {
    const progressEvents: Array<{
      phase: string;
      status: string;
      round?: number;
    }> = [];
    const provider: LLMProvider = {
      name: "test-provider",
      defaultModel: () => "test-model",
      complete: vi.fn(async () => ({
        text: "Initial response with owner and severity",
        model: "test-model",
        usage: {},
      })),
    };

    const task: AgentTaskInterface & {
      name: string;
      spec: ReturnType<typeof buildAgentTaskSolveSpec>;
    } = {
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
        evaluatorEpoch: null,
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
          evaluatorEpoch: null,
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
      evaluatorEpoch: null,
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
      onProgress: (progress) => {
        progressEvents.push(progress);
      },
      deps: {
        createTask: () => task,
        createLoop: (opts) => {
          expect(opts.timeBudget).toBeDefined();
          return {
            run: vi.fn(async () => {
              opts.onProgress?.({ phase: "evaluation", status: "started", round: 1 });
              opts.onProgress?.({
                phase: "evaluation",
                status: "completed",
                round: 1,
                roundResult: loopResult.rounds[0],
                bestScore: loopResult.bestScore,
              });
              opts.onProgress?.({ phase: "revision", status: "started", round: 1 });
              opts.onProgress?.({ phase: "revision", status: "completed", round: 1 });
              return loopResult;
            }),
          };
        },
      },
    });

    expect(provider.complete).toHaveBeenCalledOnce();
    expect(result.progress).toBe(1);
    expect(result.result.scenario_name).toBe("incident_triage");
    expect(result.result.best_score).toBe(0.93);
    expect(result.result.skill_markdown).toContain("Best round: 1");
    expect(result.outcome).toEqual({
      schema_version: 1,
      termination_reason: "threshold_met",
      quality_threshold: 0.9,
      met_threshold: true,
      completed_iterations: 1,
      max_iterations: 2,
      best_iteration: 1,
      best_score: 0.93,
      generations: [
        {
          generation: 1,
          score: 0.93,
          reasoning: "Added owner assignment and severity classification.",
          dimension_scores: { completeness: 0.93 },
          judge_failed: false,
          evaluator_epoch: null,
        },
      ],
    });
    expect(progressEvents).toEqual([
      { phase: "context_preparation", status: "started" },
      { phase: "context_preparation", status: "completed" },
      { phase: "draft", status: "started" },
      { phase: "draft", status: "completed" },
      { phase: "evaluation", status: "started", round: 1 },
      {
        phase: "evaluation",
        status: "completed",
        round: 1,
        roundResult: loopResult.rounds[0],
        bestScore: 0.93,
      },
      { phase: "revision", status: "started", round: 1 },
      { phase: "revision", status: "completed", round: 1 },
      { phase: "finalization", status: "started", round: 1 },
      { phase: "finalization", status: "completed", round: 1 },
    ]);
  });

  it("fails structured-v1 solves with no authoritative judge result before finalization", async () => {
    const progressEvents: string[] = [];
    const provider: LLMProvider = {
      name: "candidate",
      defaultModel: () => "candidate-model",
      complete: async () => ({ text: "Candidate response", usage: {} }),
    };
    const failedResult: ImprovementResult = {
      rounds: [1, 2].map((roundNumber) => ({
        roundNumber,
        output: "Candidate response",
        score: 0,
        reasoning: "Authoritative judge output could not be parsed",
        dimensionScores: {},
        evaluatorEpoch: null,
        isRevision: roundNumber > 1,
        judgeFailed: true,
      })),
      bestOutput: "Candidate response",
      bestScore: 0,
      bestRound: 1,
      totalRounds: 2,
      metThreshold: false,
      judgeFailures: 2,
      terminationReason: "consecutive_failures",
      dimensionTrajectory: {},
      totalInternalRetries: 0,
      durationMs: 1,
      judgeCalls: 2,
      evaluatorEpoch: null,
    };

    await expect(executeAgentTaskSolve({
      provider,
      created: {
        name: "judge_failure_contract",
        spec: {
          improvementTaskContractVersion: 1,
          taskPrompt: "Produce the candidate response.",
          judgeRubric: "Evaluate it.",
        },
      },
      generations: 2,
      onProgress: ({ phase, status }) => {
        progressEvents.push(`${phase}:${status}`);
      },
      deps: {
        createLoop: () => ({ run: vi.fn(async () => failedResult) }),
      },
    })).rejects.toThrow(
      /produced no usable authoritative evaluation \(judge_failures=2, total_rounds=2/,
    );

    expect(progressEvents).not.toContain("finalization:started");
    expect(progressEvents).not.toContain("finalization:completed");
  });

  it("completes draft, judge, and revision phases with the deterministic provider", async () => {
    const { DeterministicProvider } = await import("../src/providers/deterministic.js");
    const phases: string[] = [];

    const result = await executeAgentTaskSolve({
      provider: new DeterministicProvider(),
      created: {
        name: "deterministic_task_smoke",
        spec: {
          taskPrompt: "Analyze the supplied observations and recommend one next step.",
          judgeRubric: "Score task completion and actionability.",
          sampleInput: "Observation: onboarding completion fell by 12%.",
          maxRounds: 2,
          qualityThreshold: 0.9,
        },
      },
      generations: 2,
      onProgress: (progress) => {
        phases.push(`${progress.phase}:${progress.status}`);
      },
    });

    expect(result.progress).toBe(2);
    expect(result.result.best_score).toBe(0.92);
    expect(result.result.best_strategy).toEqual(expect.objectContaining({ best_round: 2 }));
    expect(result.result.metadata).toEqual(expect.objectContaining({ judge_failures: 0 }));
    expect(result.result.example_outputs).toEqual([
      expect.objectContaining({
        output: expect.stringContaining("Deterministic revised task result"),
        score: 0.92,
      }),
    ]);
    expect(phases).toContain("draft:completed");
    expect(phases).toContain("evaluation:completed");
    expect(phases).toContain("revision:completed");
    expect(phases.at(-1)).toBe("finalization:completed");
  });

  it("keeps deterministic structured-v1 JSON valid across the initial draft and revision", async () => {
    const { DeterministicProvider } = await import("../src/providers/deterministic.js");
    const evaluatedOutputs: string[] = [];

    const result = await executeAgentTaskSolve({
      provider: new DeterministicProvider(),
      created: {
        name: "deterministic_json_task",
        spec: {
          improvementTaskContractVersion: 1,
          taskPrompt: "Analyze the observation and return a JSON recommendation.",
          judgeRubric: "Score task completion and actionability.",
          outputFormat: "json_schema",
          sampleInput: "Observation: onboarding completion fell by 12%.",
          maxRounds: 2,
          qualityThreshold: 0.9,
        },
      },
      generations: 2,
      onProgress: (progress) => {
        if (progress.phase === "evaluation" && progress.status === "completed") {
          evaluatedOutputs.push(progress.roundResult!.output);
        }
      },
    });

    expect(evaluatedOutputs).toHaveLength(2);
    expect(evaluatedOutputs.map((output) => JSON.parse(output))).toEqual([
      expect.objectContaining({ result: expect.stringContaining("Deterministic task result") }),
      expect.objectContaining({
        result: expect.stringContaining("Deterministic revised task result"),
      }),
    ]);
    const bestExample = result.result.example_outputs?.[0];
    if (!bestExample) throw new Error("Expected a deterministic solve example output");
    expect(JSON.parse(bestExample.output)).toEqual(expect.objectContaining({
      result: expect.stringContaining("Deterministic revised task result"),
    }));
    expect(result.result.best_score).toBe(0.92);
  });

  it("keeps progress observer failures from changing solve results", async () => {
    const provider: LLMProvider = {
      name: "test-provider",
      defaultModel: () => "test-model",
      complete: vi.fn(async () => ({
        text: "Initial response",
        model: "test-model",
        usage: {},
      })),
    };
    const task: AgentTaskInterface & {
      name: string;
      spec: ReturnType<typeof buildAgentTaskSolveSpec>;
    } = {
      name: "observer_safety",
      spec: buildAgentTaskSolveSpec(
        {
          taskPrompt: "Do work",
          judgeRubric: "Evaluate work",
        },
        1,
      ),
      getTaskPrompt: () => "Do work",
      getRubric: () => "Evaluate work",
      describeTask: () => "Do work",
      initialState: () => ({}),
      validateContext: () => [],
      evaluateOutput: async () => ({
        score: 1,
        reasoning: "complete",
        dimensionScores: {},
        internalRetries: 0,
        evaluatorEpoch: null,
      }),
    };

    const result = await executeAgentTaskSolve({
      provider,
      created: {
        name: "observer_safety",
        spec: { taskPrompt: "Do work", judgeRubric: "Evaluate work" },
      },
      generations: 1,
      onProgress: async () => {
        throw new Error("telemetry unavailable");
      },
      deps: { createTask: () => task },
    });

    expect(result.progress).toBe(1);
    expect(result.result.best_score).toBe(1);
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
    const taskFromSpec = vi.fn(
      (opts: {
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
          evaluatorEpoch: null,
        }),
      }),
    );

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
            run: vi.fn(async (): Promise<ImprovementResult> => ({
              rounds: [1, 2, 3].map((roundNumber) => ({
                roundNumber,
                output: "Initial response",
                score: 0.5,
                reasoning: "The response is acceptable but below threshold.",
                dimensionScores: {},
                isRevision: roundNumber > 1,
                judgeFailed: false,
                evaluatorEpoch: null,
              })),
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
              evaluatorEpoch: null,
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

    const invalidTask: AgentTaskInterface & {
      name: string;
      spec: ReturnType<typeof buildAgentTaskSolveSpec>;
    } = {
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
        evaluatorEpoch: null,
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
    ).rejects.toThrow(
      "agent_task context preparation failed: missing required context key: 'timeline'",
    );
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
    expect(result.result.skill_markdown).toContain("Initial answer rewritten by provider hook");
  });

  it("finishes a truncated saved-task draft before starting evaluation", async () => {
    const stopAfterDraft = new Error("stop after completed draft");
    const run = vi.fn(async ({ initialOutput }: { initialOutput: string }) => {
      expect(initialOutput).toBe("Part one. Part two. Part three.");
      throw stopAfterDraft;
    });
    const complete = vi
      .fn()
      .mockResolvedValueOnce({
        text: "Part one.",
        model: "test-model",
        usage: {},
        stopReason: "max_tokens",
      })
      .mockResolvedValueOnce({
        text: " Part two.",
        model: "test-model",
        usage: {},
        stopReason: "length",
      })
      .mockResolvedValueOnce({
        text: " Part three.",
        model: "test-model",
        usage: {},
        stopReason: "end_turn",
      });
    const provider: LLMProvider = {
      name: "test-provider",
      defaultModel: () => "test-model",
      complete,
    };

    await expect(
      executeAgentTaskSolve({
        provider,
        created: {
          name: "continued_saved_task",
          spec: {
            taskPrompt: "Deliver the complete artifact.",
            judgeRubric: "Evaluate completeness.",
          },
        },
        generations: 2,
        deps: { createLoop: () => ({ run }) },
      }),
    ).rejects.toBe(stopAfterDraft);

    expect(complete).toHaveBeenCalledTimes(3);
    expect(complete.mock.calls.map(([request]) => request.maxTokens)).toEqual([
      8_192, 8_192, 8_192,
    ]);
    expect(run).toHaveBeenCalledTimes(1);
  });

  it("does not evaluate a saved-task draft that remains truncated", async () => {
    const run = vi.fn();
    const complete = vi
      .fn()
      .mockResolvedValueOnce({
        text: "Part one.",
        model: "test-model",
        usage: {},
        stopReason: "max_tokens",
      })
      .mockResolvedValueOnce({
        text: " Part two.",
        model: "test-model",
        usage: {},
        stopReason: "length",
      })
      .mockResolvedValueOnce({
        text: " Part three.",
        model: "test-model",
        usage: {},
        stopReason: "max_tokens",
      });
    const provider: LLMProvider = {
      name: "test-provider",
      defaultModel: () => "test-model",
      complete,
    };

    await expect(
      executeAgentTaskSolve({
        provider,
        created: {
          name: "exhausted_saved_task",
          spec: {
            taskPrompt: "Deliver the complete artifact.",
            judgeRubric: "Evaluate completeness.",
          },
        },
        generations: 2,
        deps: { createLoop: () => ({ run }) },
      }),
    ).rejects.toThrow(/remained truncated after 2 continuation attempts/i);

    expect(complete).toHaveBeenCalledTimes(3);
    expect(run).not.toHaveBeenCalled();
  });
});
