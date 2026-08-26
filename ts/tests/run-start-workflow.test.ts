import { describe, expect, it, vi } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type { AppSettings } from "../src/config/index.js";
import { LoopController } from "../src/loop/controller.js";
import type { CustomScenarioEntry } from "../src/scenarios/custom-loader.js";
import {
  TASK_DATA_METADATA_MARKER,
  TASK_DATA_TRUNCATION_WARNING,
} from "../src/scenarios/improvement-task-contract.js";
import type { ScenarioFamilyName } from "../src/scenarios/families.js";
import type { RoleProviderBundle } from "../src/providers/index.js";
import { HookBus } from "../src/extensions/index.js";
import type { AgentTaskSolveProgress } from "../src/knowledge/agent-task-solve-execution.js";
import {
  executeAgentTaskCustomStartRun,
  executeBuiltInGameStartRun,
  executeGeneratedCustomStartRun,
  resolveRunStartPlan,
} from "../src/server/run-start-workflow.js";

// Array.prototype.findLastIndex is ES2023; the compiler targets ES2022 lib.
function lastIndexOfEvent(entries: readonly { event: string }[], event: string): number {
  for (let i = entries.length - 1; i >= 0; i -= 1) {
    if (entries[i]?.event === event) {
      return i;
    }
  }
  return -1;
}

function makeSettings(): AppSettings {
  return {
    ...({} as AppSettings),
    matchesPerGeneration: 3,
    maxRetries: 2,
    backpressureMinDelta: 0.01,
    playbookMaxVersions: 5,
    contextBudgetTokens: 32000,
    curatorEnabled: true,
    curatorConsolidateEveryNGens: 3,
    skillMaxLessons: 30,
    deadEndTrackingEnabled: true,
    deadEndMaxEntries: 25,
    stagnationResetEnabled: true,
    stagnationRollbackThreshold: 5,
    stagnationPlateauWindow: 3,
    stagnationPlateauEpsilon: 0.01,
    stagnationDistillTopLessons: 5,
    explorationMode: "linear",
    notifyWebhookUrl: null,
    notifyOn: "completion",
  };
}

describe("run start workflow", () => {
  it("resolves built-in game runs from the registry", () => {
    const plan = resolveRunStartPlan({
      scenario: "grid_ctf",
      builtinScenarioNames: ["grid_ctf"],
    });

    expect(plan).toEqual({ kind: "builtin_game", scenarioName: "grid_ctf" });
  });

  it("resolves generated custom runs when saved source and a runnable family exist", () => {
    const entry: CustomScenarioEntry = {
      name: "saved_sim",
      type: "simulation",
      spec: { description: "Saved simulation" },
      path: "/tmp/saved_sim",
      hasGeneratedSource: true,
    };

    const plan = resolveRunStartPlan({
      scenario: "saved_sim",
      builtinScenarioNames: ["grid_ctf"],
      customScenario: entry,
      customScenarioFamily: "simulation",
    });

    expect(plan).toEqual({
      kind: "generated_custom",
      scenarioName: "saved_sim",
      entry,
      family: "simulation",
    });
  });

  it("resolves saved custom agent-task scenarios for /run", () => {
    const entry: CustomScenarioEntry = {
      name: "saved_task",
      type: "agent_task",
      spec: { description: "Saved task" },
      path: "/tmp/saved_task",
      hasGeneratedSource: false,
    };

    expect(
      resolveRunStartPlan({
        scenario: "saved_task",
        builtinScenarioNames: ["grid_ctf"],
        customScenario: entry,
        customScenarioFamily: "agent_task",
      }),
    ).toEqual({
      kind: "agent_task_custom",
      scenarioName: "saved_task",
      entry,
    });
  });

  it("executes built-in game runs through the generation runner boundary", async () => {
    class FakeScenario {
      readonly name = "grid_ctf";
      describeRules() {
        return "Rules";
      }
      describeStrategyInterface() {
        return "Strategy";
      }
      describeEvaluationCriteria() {
        return "Criteria";
      }
      initialState() {
        return {};
      }
      getObservation() {
        return { narrative: "obs", state: {}, constraints: [] };
      }
      validateActions() {
        return [true, "ok"] as [boolean, string];
      }
      step() {
        return {};
      }
      isTerminal() {
        return true;
      }
      getResult() {
        return {
          score: 1,
          winner: null,
          summary: "done",
          replay: [],
          metrics: {},
          validationErrors: [],
          get passedValidation() {
            return true;
          },
        };
      }
      replayToNarrative() {
        return "narrative";
      }
      renderFrame() {
        return {};
      }
      enumerateLegalActions() {
        return null;
      }
      scoringDimensions() {
        return null;
      }
      executeMatch() {
        return {
          score: 1,
          winner: null,
          summary: "done",
          replay: [],
          metrics: {},
          validationErrors: [],
          get passedValidation() {
            return true;
          },
        };
      }
    }

    const migrate = vi.fn();
    const close = vi.fn();
    const store = { migrate, close };
    const run = vi.fn(async () => ({ generationsCompleted: 2 }));
    const createRunner = vi.fn(() => ({ run }));
    const closeProviderBundle = vi.fn();
    const bundle: RoleProviderBundle = {
      defaultProvider: { name: "test", defaultModel: () => "test", complete: vi.fn() },
      defaultConfig: { providerType: "deterministic", apiKey: "", baseUrl: "", model: "test" },
      roleProviders: {},
      roleModels: {},
      close: closeProviderBundle,
    };

    const result = await executeBuiltInGameStartRun({
      runId: "run_1",
      scenarioName: "grid_ctf",
      generations: 2,
      settings: makeSettings(),
      providerBundle: bundle,
      opts: {
        dbPath: "/tmp/test.db",
        migrationsDir: "/tmp/migrations",
        runsRoot: "/tmp/runs",
        knowledgeRoot: "/tmp/knowledge",
      },
      controller: new LoopController(),
      events: {} as never,
      deps: {
        resolveScenarioClass: () => FakeScenario as never,
        createStore: () => store as never,
        createRunner,
      },
    });

    expect(migrate).toHaveBeenCalledWith("/tmp/migrations");
    expect(createRunner).toHaveBeenCalledWith(
      expect.objectContaining({ agentProvider: "deterministic" }),
    );
    expect(run).toHaveBeenCalledWith("run_1", 2);
    expect(close).toHaveBeenCalledOnce();
    expect(closeProviderBundle).toHaveBeenCalledOnce();
    expect(result).toBeUndefined();
  });

  it("executes generated custom runs and emits generation lifecycle events", async () => {
    const emitted: Array<{ event: string; payload: Record<string, unknown> }> = [];
    const events = {
      emit: (event: string, payload: Record<string, unknown>) => {
        emitted.push({ event, payload });
      },
    };

    await executeGeneratedCustomStartRun({
      runId: "run_2",
      scenarioName: "saved_sim",
      entry: {
        name: "saved_sim",
        type: "simulation",
        spec: { max_steps: 3 },
        path: "/tmp/saved_sim",
        hasGeneratedSource: true,
      },
      family: "simulation",
      generations: 2,
      knowledgeRoot: "/tmp/knowledge",
      controller: new LoopController(),
      events: events as never,
      deps: {
        executeGeneratedScenarioEntry: vi
          .fn()
          .mockResolvedValueOnce({
            family: "simulation" as ScenarioFamilyName,
            stepsExecuted: 2,
            finalState: {},
            records: [],
            score: 0.6,
            reasoning: "first generation",
            dimensionScores: {},
          })
          .mockResolvedValueOnce({
            family: "simulation" as ScenarioFamilyName,
            stepsExecuted: 3,
            finalState: {},
            records: [],
            score: 0.9,
            reasoning: "second generation",
            dimensionScores: {},
          }),
      },
    });

    expect(emitted[0]?.event).toBe("run_started");
    expect(emitted.filter((entry) => entry.event === "generation_started")).toHaveLength(2);
    expect(emitted.filter((entry) => entry.event === "generation_completed")).toHaveLength(2);
    const generatedPlanEvents = emitted.filter((entry) => entry.event === "task_plan_updated");
    expect(generatedPlanEvents.at(0)?.payload.update_kind).toBe("initial");
    expect(generatedPlanEvents.at(-1)?.payload.active_step_id).toBeNull();
    expect(generatedPlanEvents.at(-1)?.payload.steps).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "execute_scenario", status: "completed" }),
        expect.objectContaining({ id: "finalize_run", status: "completed" }),
      ]),
    );
    expect(emitted.findIndex((entry) => entry.event === "task_plan_updated")).toBeGreaterThan(
      emitted.findIndex((entry) => entry.event === "run_started"),
    );
    expect(lastIndexOfEvent(emitted, "task_plan_updated")).toBeLessThan(
      emitted.findIndex((entry) => entry.event === "run_completed"),
    );
    const progressNotes = emitted.filter((entry) => entry.event === "agent_progress_note");
    expect(progressNotes.map((entry) => entry.payload.kind)).toEqual([
      "intent",
      "discovery",
      "discovery",
      "verification",
    ]);
    expect(progressNotes.map((entry) => entry.payload.generation)).toEqual([0, 1, 2, 2]);
    expect(JSON.stringify(progressNotes)).not.toContain("first generation");
    expect(JSON.stringify(progressNotes)).not.toContain("second generation");
    expect(lastIndexOfEvent(emitted, "agent_progress_note")).toBeLessThan(
      emitted.findIndex((entry) => entry.event === "run_completed"),
    );
    const completed = emitted.find((entry) => entry.event === "run_completed");
    expect(completed?.payload.best_score).toBe(0.9);
    expect(completed?.payload.completed_generations).toBe(2);
    expect(completed?.payload.elo).toBe(1000);
    expect(completed?.payload.session_report_path).toBeNull();
    expect(completed?.payload.dead_ends_found).toBe(0);
  });

  it("retains a completed generated-custom checkpoint when stop races natural completion", async () => {
    const emitted: Array<{ event: string; payload: Record<string, unknown> }> = [];
    const controller = new LoopController();
    const executeGeneratedScenarioEntry = vi.fn(async () => {
      controller.requestStop("run_generated_stop", "stop-generated-1");
      return {
        family: "simulation" as ScenarioFamilyName,
        stepsExecuted: 4,
        finalState: {},
        records: [],
        score: 0.73,
        reasoning: "durable first checkpoint",
        dimensionScores: {},
      };
    });

    await expect(
      executeGeneratedCustomStartRun({
        runId: "run_generated_stop",
        scenarioName: "saved_sim",
        entry: {
          name: "saved_sim",
          type: "simulation",
          spec: { max_steps: 4 },
          path: "/tmp/saved_sim",
          hasGeneratedSource: true,
        },
        family: "simulation",
        generations: 2,
        knowledgeRoot: "/tmp/knowledge",
        controller,
        events: {
          emit: (event: string, payload: Record<string, unknown>) => {
            emitted.push({ event, payload });
          },
        } as never,
        deps: { executeGeneratedScenarioEntry },
      }),
    ).rejects.toMatchObject({
      name: "RunStopRequestedError",
      runId: "run_generated_stop",
      commandId: "stop-generated-1",
      completedGenerations: 1,
      bestScore: 0.73,
    });

    expect(executeGeneratedScenarioEntry).toHaveBeenCalledOnce();
    expect(emitted.filter((entry) => entry.event === "generation_completed")).toEqual([
      {
        event: "generation_completed",
        payload: expect.objectContaining({
          run_id: "run_generated_stop",
          generation: 1,
          best_score: 0.73,
          steps_executed: 4,
        }),
      },
    ]);
    expect(emitted.some((entry) => entry.event === "run_completed")).toBe(false);
    expect(emitted.some((entry) => entry.event === "run_failed")).toBe(false);
    const interruptedPlan = emitted
      .filter((entry) => entry.event === "task_plan_updated")
      .at(-1)?.payload;
    expect(interruptedPlan?.active_step_id).toBeNull();
    expect(interruptedPlan?.steps).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "execute_scenario", status: "interrupted" }),
      ]),
    );
    expect(
      emitted
        .filter((entry) => entry.event === "agent_progress_note")
        .map((entry) => entry.payload.kind),
    ).toEqual(["intent", "discovery"]);
  });

  it("publishes a static generated-custom blocker without raw failure details", async () => {
    const emitted: Array<{ event: string; payload: Record<string, unknown> }> = [];

    await expect(
      executeGeneratedCustomStartRun({
        runId: "run_generated_failed",
        scenarioName: "saved_sim",
        entry: {
          name: "saved_sim",
          type: "simulation",
          spec: { max_steps: 4 },
          path: "/tmp/saved_sim",
          hasGeneratedSource: true,
        },
        family: "simulation",
        generations: 1,
        knowledgeRoot: "/tmp/knowledge",
        controller: new LoopController(),
        events: {
          emit: (event: string, payload: Record<string, unknown>) => {
            emitted.push({ event, payload });
          },
        } as never,
        deps: {
          executeGeneratedScenarioEntry: vi.fn(async () => {
            throw new Error(
              "provider failed at https://private.example.test with selector=#secret",
            );
          }),
        },
      }),
    ).rejects.toThrow("provider failed");

    const progressNotes = emitted.filter((entry) => entry.event === "agent_progress_note");
    expect(progressNotes.map((entry) => entry.payload.kind)).toEqual(["intent", "blocker"]);
    expect(JSON.stringify(progressNotes)).not.toContain("private.example.test");
    expect(JSON.stringify(progressNotes)).not.toContain("#secret");
  });

  it("executes saved agent-task runs and emits lifecycle events", async () => {
    const emitted: Array<{ event: string; payload: Record<string, unknown> }> = [];
    const events = {
      emit: (event: string, payload: Record<string, unknown>) => {
        emitted.push({ event, payload });
      },
    };
    const executeAgentTaskSolve = vi.fn(async () => ({
      progress: 2,
      result: { best_score: 0.82, scenario_name: "saved_task" },
    }));

    await executeAgentTaskCustomStartRun({
      runId: "run_task",
      scenarioName: "saved_task",
      entry: {
        name: "saved_task",
        type: "agent_task",
        spec: { taskPrompt: "Do work", judgeRubric: "Do it well" },
        path: "/tmp/saved_task",
        hasGeneratedSource: false,
      },
      generations: 2,
      provider: { name: "test", defaultModel: () => "test", complete: vi.fn() },
      settings: makeSettings(),
      controller: new LoopController(),
      events: events as never,
      deps: { executeAgentTaskSolve: executeAgentTaskSolve as never },
    });

    expect(executeAgentTaskSolve).toHaveBeenCalledWith({
      provider: expect.objectContaining({ name: "test" }),
      created: {
        name: "saved_task",
        spec: { taskPrompt: "Do work", judgeRubric: "Do it well" },
      },
      generations: 2,
      hookBus: expect.any(HookBus),
      onProgress: expect.any(Function),
    });
    expect(emitted[0]?.event).toBe("run_started");
    expect(emitted.filter((entry) => entry.event === "generation_started")).toEqual([
      { event: "generation_started", payload: { run_id: "run_task", generation: 1 } },
      { event: "generation_started", payload: { run_id: "run_task", generation: 2 } },
    ]);
    expect(emitted.filter((entry) => entry.event === "generation_completed")).toHaveLength(2);
    expect(
      emitted.find((entry) => entry.event === "generation_completed")?.payload.best_score,
    ).toBe(0.82);
    const completed = emitted.find((entry) => entry.event === "run_completed");
    expect(completed?.payload.completed_generations).toBe(2);
    expect(completed?.payload.best_score).toBe(0.82);
    expect(completed?.payload.elo).toBe(1000);
    expect(completed?.payload.session_report_path).toBeNull();
    expect(completed?.payload.dead_ends_found).toBe(0);
    const savedTaskPlan = emitted
      .filter((entry) => entry.event === "task_plan_updated")
      .at(-1)?.payload;
    expect(savedTaskPlan?.active_step_id).toBeNull();
    expect(savedTaskPlan?.steps).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "finalize_result", status: "completed" }),
      ]),
    );
    expect(
      emitted
        .filter((entry) => entry.event === "agent_progress_note")
        .map((entry) => entry.payload.kind),
    ).toEqual(["intent", "decision"]);
  });

  it("retains a run warning when structured mission data was truncated", async () => {
    const emitted: Array<{ event: string; payload: Record<string, unknown> }> = [];

    await executeAgentTaskCustomStartRun({
      runId: "run_truncated_task_data",
      scenarioName: "saved_task",
      entry: {
        name: "saved_task",
        type: "agent_task",
        spec: {
          taskPrompt: "Do work",
          judgeRubric: "Do it well",
          sampleInput:
            `${TASK_DATA_METADATA_MARKER}: input: issues.csv (source id: input)]\n` +
            `${TASK_DATA_TRUNCATION_WARNING} The task received 100 of 200 source bytes.`,
        },
        path: "/tmp/saved_task",
        hasGeneratedSource: false,
      },
      generations: 1,
      provider: { name: "test", defaultModel: () => "test", complete: vi.fn() },
      controller: new LoopController(),
      events: {
        emit: (event: string, payload: Record<string, unknown>) => {
          emitted.push({ event, payload });
        },
      } as never,
      deps: {
        executeAgentTaskSolve: vi.fn(async () => ({
          progress: 1,
          result: { best_score: 0.7, scenario_name: "saved_task" },
        })) as never,
      },
    });

    expect(emitted.find((entry) => entry.event === "monitor_alert")?.payload).toMatchObject({
      condition_id: "structured_task_data_truncated",
      condition_name: "Mission data was truncated",
      condition_type: "data_integrity",
      scope: "run:run_truncated_task_data",
    });
  });

  it("streams each saved task evaluation with its round score and running best", async () => {
    const emitted: Array<{ event: string; payload: Record<string, unknown> }> = [];

    await executeAgentTaskCustomStartRun({
      runId: "run_task_live",
      scenarioName: "saved_task",
      entry: {
        name: "saved_task",
        type: "agent_task",
        spec: { taskPrompt: "Do work", judgeRubric: "Do it well" },
        path: "/tmp/saved_task",
        hasGeneratedSource: false,
      },
      generations: 2,
      provider: { name: "test", defaultModel: () => "test", complete: vi.fn() },
      controller: new LoopController(),
      events: {
        emit: (event: string, payload: Record<string, unknown>) => {
          emitted.push({ event, payload });
        },
      } as never,
      deps: {
        now: () => 0,
        executeAgentTaskSolve: vi.fn(
          async (solveOpts: { onProgress?: (progress: AgentTaskSolveProgress) => void }) => {
            expect(emitted.filter((entry) => entry.event === "generation_started")).toEqual([]);
            solveOpts.onProgress?.({
              phase: "context_preparation",
              status: "started",
            });
            solveOpts.onProgress?.({
              phase: "context_preparation",
              status: "completed",
            });
            expect(emitted.filter((entry) => entry.event === "generation_started")).toEqual([]);
            solveOpts.onProgress?.({ phase: "draft", status: "started" });
            expect(
              emitted
                .filter((entry) => entry.event === "generation_started")
                .map((entry) => entry.payload.generation),
            ).toEqual([]);
            solveOpts.onProgress?.({ phase: "draft", status: "completed" });
            solveOpts.onProgress?.({ phase: "evaluation", status: "started", round: 1 });
            expect(
              emitted
                .filter((entry) => entry.event === "generation_started")
                .map((entry) => entry.payload.generation),
            ).toEqual([1]);
            solveOpts.onProgress?.({
              phase: "evaluation",
              status: "completed",
              round: 1,
              bestScore: 0.41,
              roundResult: {
                roundNumber: 1,
                output: "first attempt",
                score: 0.41,
                reasoning: "Needs stronger evidence.",
                dimensionScores: { evidence: 0.31, clarity: 0.51 },
                evaluatorEpoch: null,
                isRevision: false,
                judgeFailed: false,
                roundDurationMs: 12,
              },
            });
            solveOpts.onProgress?.({ phase: "revision", status: "started", round: 1 });
            expect(
              emitted
                .filter((entry) => entry.event === "generation_started")
                .map((entry) => entry.payload.generation),
            ).toEqual([1]);
            solveOpts.onProgress?.({ phase: "revision", status: "completed", round: 1 });
            solveOpts.onProgress?.({ phase: "evaluation", status: "started", round: 2 });
            expect(
              emitted
                .filter((entry) => entry.event === "generation_started")
                .map((entry) => entry.payload.generation),
            ).toEqual([1, 2]);
            solveOpts.onProgress?.({
              phase: "evaluation",
              status: "completed",
              round: 2,
              bestScore: 0.84,
              roundResult: {
                roundNumber: 2,
                output: "second attempt",
                score: 0.84,
                reasoning: "Evidence is now grounded and clear.",
                dimensionScores: { evidence: 0.82, clarity: 0.86 },
                evaluatorEpoch: null,
                isRevision: true,
                judgeFailed: false,
                roundDurationMs: 18,
              },
            });
            return {
              progress: 2,
              result: { best_score: 0.84, scenario_name: "saved_task" },
            };
          },
        ) as never,
      },
    });

    expect(
      emitted
        .filter(
          (entry) => entry.event === "generation_started" || entry.event === "generation_completed",
        )
        .map((entry) => ({ event: entry.event, generation: entry.payload.generation })),
    ).toEqual([
      { event: "generation_started", generation: 1 },
      { event: "generation_completed", generation: 1 },
      { event: "generation_started", generation: 2 },
      { event: "generation_completed", generation: 2 },
    ]);
    expect(emitted.filter((entry) => entry.event === "generation_completed")).toEqual([
      {
        event: "generation_completed",
        payload: expect.objectContaining({
          generation: 1,
          mean_score: 0.41,
          best_score: 0.41,
          reasoning: "Needs stronger evidence.",
          dimension_scores: { evidence: 0.31, clarity: 0.51 },
          judge_failed: false,
          round_duration_ms: 12,
          rounds_completed: 1,
        }),
      },
      {
        event: "generation_completed",
        payload: expect.objectContaining({
          generation: 2,
          mean_score: 0.84,
          best_score: 0.84,
          reasoning: "Evidence is now grounded and clear.",
          dimension_scores: { evidence: 0.82, clarity: 0.86 },
          judge_failed: false,
          round_duration_ms: 18,
          rounds_completed: 2,
        }),
      },
    ]);
    expect(emitted.find((entry) => entry.event === "run_completed")?.payload).toMatchObject({
      completed_generations: 2,
      best_score: 0.84,
    });
  });

  it("includes candidate drafting and revision work in saved-task generation timing", async () => {
    const emitted: Array<{ event: string; payload: Record<string, unknown> }> = [];
    const upsertGeneration = vi.fn();
    const createStore = vi.fn(() => ({
      migrate: vi.fn(),
      createRun: vi.fn(),
      updateRunStatus: vi.fn(),
      upsertGeneration,
      appendAgentOutput: vi.fn(),
      close: vi.fn(),
    }));
    const now = vi
      .fn()
      .mockReturnValueOnce(1_000)
      .mockReturnValueOnce(4_000)
      .mockReturnValueOnce(5_000)
      .mockReturnValueOnce(11_000);

    await executeAgentTaskCustomStartRun({
      runId: "run_task_full_round_timing",
      scenarioName: "saved_task",
      entry: {
        name: "saved_task",
        type: "agent_task",
        spec: { taskPrompt: "Do work", judgeRubric: "Do it well" },
        path: "/tmp/saved_task",
        hasGeneratedSource: false,
      },
      generations: 2,
      provider: { name: "test", defaultModel: () => "test", complete: vi.fn() },
      persistence: {
        dbPath: "/tmp/agent-task-timing.db",
        migrationsDir: "/tmp/migrations",
      },
      controller: new LoopController(),
      events: {
        emit: (event: string, payload: Record<string, unknown>) => {
          emitted.push({ event, payload });
        },
      } as never,
      deps: {
        now,
        createStore: createStore as never,
        executeAgentTaskSolve: vi.fn(
          async (solveOpts: { onProgress?: (progress: AgentTaskSolveProgress) => void }) => {
            solveOpts.onProgress?.({ phase: "draft", status: "started" });
            solveOpts.onProgress?.({ phase: "draft", status: "completed" });
            solveOpts.onProgress?.({ phase: "evaluation", status: "started", round: 1 });
            solveOpts.onProgress?.({
              phase: "evaluation",
              status: "completed",
              round: 1,
              bestScore: 0.4,
              roundResult: {
                roundNumber: 1,
                output: "first attempt",
                score: 0.4,
                reasoning: "Revise it.",
                dimensionScores: {},
                evaluatorEpoch: null,
                isRevision: false,
                judgeFailed: false,
                roundDurationMs: 25,
              },
            });
            solveOpts.onProgress?.({ phase: "revision", status: "started", round: 1 });
            solveOpts.onProgress?.({ phase: "revision", status: "completed", round: 1 });
            solveOpts.onProgress?.({ phase: "evaluation", status: "started", round: 2 });
            solveOpts.onProgress?.({
              phase: "evaluation",
              status: "completed",
              round: 2,
              bestScore: 0.9,
              roundResult: {
                roundNumber: 2,
                output: "second attempt",
                score: 0.9,
                reasoning: "Improved.",
                dimensionScores: {},
                evaluatorEpoch: null,
                isRevision: true,
                judgeFailed: false,
                roundDurationMs: 30,
              },
            });
            return {
              progress: 2,
              result: { best_score: 0.9, scenario_name: "saved_task" },
            };
          },
        ) as never,
      },
    });

    expect(now).toHaveBeenCalledTimes(4);
    expect(upsertGeneration).toHaveBeenNthCalledWith(
      1,
      "run_task_full_round_timing",
      1,
      expect.objectContaining({ durationSeconds: 3 }),
    );
    expect(upsertGeneration).toHaveBeenNthCalledWith(
      2,
      "run_task_full_round_timing",
      2,
      expect.objectContaining({ durationSeconds: 6 }),
    );
    expect(
      emitted
        .filter((entry) => entry.event === "generation_timing")
        .map((entry) => entry.payload.elapsed_seconds),
    ).toEqual([3, 6]);
  });

  it("does not start a dangling generation when an unchanged revision ends the solve", async () => {
    const emitted: Array<{ event: string; payload: Record<string, unknown> }> = [];

    await executeAgentTaskCustomStartRun({
      runId: "run_task_unchanged_revision",
      scenarioName: "saved_task",
      entry: {
        name: "saved_task",
        type: "agent_task",
        spec: { taskPrompt: "Do work", judgeRubric: "Do it well" },
        path: "/tmp/saved_task",
        hasGeneratedSource: false,
      },
      generations: 2,
      provider: { name: "test", defaultModel: () => "test", complete: vi.fn() },
      controller: new LoopController(),
      events: {
        emit: (event: string, payload: Record<string, unknown>) => {
          emitted.push({ event, payload });
        },
      } as never,
      deps: {
        executeAgentTaskSolve: vi.fn(
          async (solveOpts: { onProgress?: (progress: AgentTaskSolveProgress) => void }) => {
            solveOpts.onProgress?.({ phase: "draft", status: "started" });
            solveOpts.onProgress?.({ phase: "draft", status: "completed" });
            solveOpts.onProgress?.({ phase: "evaluation", status: "started", round: 1 });
            solveOpts.onProgress?.({
              phase: "evaluation",
              status: "completed",
              round: 1,
              bestScore: 0.61,
              roundResult: {
                roundNumber: 1,
                output: "unchanged response",
                score: 0.61,
                reasoning: "The revision should preserve this response.",
                dimensionScores: { quality: 0.61 },
                evaluatorEpoch: null,
                isRevision: false,
                judgeFailed: false,
                roundDurationMs: 10,
              },
            });
            solveOpts.onProgress?.({ phase: "revision", status: "started", round: 1 });
            solveOpts.onProgress?.({ phase: "revision", status: "completed", round: 1 });
            return {
              progress: 1,
              result: {
                best_score: 0.61,
                scenario_name: "saved_task",
                termination_reason: "unchanged_output",
              },
            };
          },
        ) as never,
      },
    });

    const starts = emitted
      .filter((entry) => entry.event === "generation_started")
      .map((entry) => entry.payload.generation);
    const completions = emitted
      .filter((entry) => entry.event === "generation_completed")
      .map((entry) => entry.payload.generation);

    expect(starts).toEqual([1]);
    expect(completions).toEqual(starts);
    expect(starts).not.toContain(2);
    expect(emitted.find((entry) => entry.event === "run_completed")?.payload).toMatchObject({
      completed_generations: 1,
      best_score: 0.61,
    });
  });

  it("persists saved task rounds and projects the retained output as an artifact", async () => {
    const emitted: Array<{ event: string; payload: Record<string, unknown> }> = [];
    const migrate = vi.fn();
    const createRun = vi.fn();
    const updateRunStatus = vi.fn();
    const upsertGeneration = vi.fn();
    const appendAgentOutput = vi.fn();
    const close = vi.fn();
    const now = vi.fn().mockReturnValueOnce(1_000).mockReturnValueOnce(2_500);
    const createStore = vi.fn(() => ({
      migrate,
      createRun,
      updateRunStatus,
      upsertGeneration,
      appendAgentOutput,
      close,
    }));

    await executeAgentTaskCustomStartRun({
      runId: "run_saved_artifact",
      scenarioName: "saved_task",
      entry: {
        name: "saved_task",
        type: "agent_task",
        spec: { taskPrompt: "Do work", judgeRubric: "Do it well" },
        path: "/tmp/saved_task",
        hasGeneratedSource: false,
      },
      generations: 1,
      provider: { name: "test", defaultModel: () => "test", complete: vi.fn() },
      persistence: {
        dbPath: "/tmp/agent-task.db",
        migrationsDir: "/tmp/migrations",
        agentProvider: "anthropic",
      },
      controller: new LoopController(),
      events: {
        emit: (event: string, payload: Record<string, unknown>) => {
          emitted.push({ event, payload });
        },
      } as never,
      deps: {
        now,
        createStore: createStore as never,
        executeAgentTaskSolve: vi.fn(
          async (solveOpts: { onProgress?: (progress: AgentTaskSolveProgress) => void }) => {
            solveOpts.onProgress?.({ phase: "evaluation", status: "started", round: 1 });
            solveOpts.onProgress?.({
              phase: "evaluation",
              status: "completed",
              round: 1,
              bestScore: 0.84,
              roundResult: {
                roundNumber: 1,
                output: "# Final recommendation\n\nShip the grounded change.",
                score: 0.84,
                reasoning: "The recommendation is grounded and actionable.",
                dimensionScores: { grounding: 0.82, actionability: 0.86 },
                evaluatorEpoch: "epoch-4",
                isRevision: false,
                judgeFailed: false,
                roundDurationMs: 120,
              },
            });
            return {
              progress: 1,
              result: {
                scenario_name: "saved_task",
                best_score: 0.84,
                best_strategy: { best_round: 1 },
                example_outputs: [
                  {
                    output: "# Final recommendation\n\nShip the grounded change.",
                    score: 0.84,
                    reasoning: "The recommendation is grounded and actionable.",
                  },
                ],
                playbook: "Retain grounded claims and explicit next steps.",
              },
            };
          },
        ) as never,
      },
    });

    expect(createStore).toHaveBeenCalledWith("/tmp/agent-task.db");
    expect(migrate).toHaveBeenCalledWith("/tmp/migrations");
    expect(createRun).toHaveBeenCalledWith(
      "run_saved_artifact",
      "saved_task",
      1,
      "agent_task",
      "anthropic",
    );
    expect(upsertGeneration).toHaveBeenCalledWith(
      "run_saved_artifact",
      1,
      expect.objectContaining({
        meanScore: 0.84,
        bestScore: 0.84,
        status: "completed",
        durationSeconds: 1.5,
        dimensionSummaryJson: JSON.stringify({ grounding: 0.82, actionability: 0.86 }),
        scoringBackend: "agent_task",
        evaluatorEpoch: "epoch-4",
      }),
    );
    expect(appendAgentOutput.mock.calls).toEqual(
      expect.arrayContaining([
        [
          "run_saved_artifact",
          1,
          "competitor",
          "# Final recommendation\n\nShip the grounded change.",
        ],
        ["run_saved_artifact", 1, "analyst", "The recommendation is grounded and actionable."],
        ["run_saved_artifact", 1, "coach", "Retain grounded claims and explicit next steps."],
      ]),
    );
    expect(updateRunStatus).toHaveBeenCalledWith("run_saved_artifact", "completed");
    expect(close).toHaveBeenCalledOnce();
    expect(emitted.find((entry) => entry.event === "generation_timing")?.payload).toMatchObject({
      generation: 1,
      elapsed_seconds: 1.5,
    });
    expect(emitted.find((entry) => entry.event === "action_detail")?.payload).toMatchObject({
      action_id: "agent-final-result",
      status: "completed",
      generation: 1,
      output: {
        score: 0.84,
        _autowork: {
          artifacts: [
            expect.objectContaining({
              id: "agent-final-output",
              previewKind: "markdown",
              preview: "# Final recommendation\n\nShip the grounded change.",
              previewTruncated: false,
            }),
          ],
        },
      },
      artifacts: [
        expect.objectContaining({
          id: "agent-final-output",
          name: "Final result.md",
          media_type: "text/markdown",
        }),
      ],
    });
    expect(
      emitted.filter((entry) => entry.event === "agent_progress_note").at(-1)?.payload
        .evidence_targets,
    ).toEqual([
      {
        kind: "artifact",
        action_id: "agent-final-result",
        artifact_id: "agent-final-output",
      },
    ]);
    expect(lastIndexOfEvent(emitted, "action_detail")).toBeLessThan(
      emitted.findIndex((entry) => entry.event === "run_completed"),
    );
  });

  it("publishes saved task evaluation and revision progress as a semantic replan", async () => {
    const emitted: Array<{ event: string; payload: Record<string, unknown> }> = [];

    await executeAgentTaskCustomStartRun({
      runId: "run_task_plan",
      scenarioName: "saved_task",
      entry: {
        name: "saved_task",
        type: "agent_task",
        spec: { taskPrompt: "Do work", judgeRubric: "Do it well" },
        path: "/tmp/saved_task",
        hasGeneratedSource: false,
      },
      generations: 2,
      provider: { name: "test", defaultModel: () => "test", complete: vi.fn() },
      controller: new LoopController(),
      events: {
        emit: (event: string, payload: Record<string, unknown>) => {
          emitted.push({ event, payload });
        },
      } as never,
      deps: {
        executeAgentTaskSolve: vi.fn(
          async (solveOpts: { onProgress?: (progress: AgentTaskSolveProgress) => void }) => {
            solveOpts.onProgress?.({ phase: "context_preparation", status: "completed" });
            solveOpts.onProgress?.({ phase: "draft", status: "completed" });
            solveOpts.onProgress?.({ phase: "evaluation", status: "started", round: 1 });
            solveOpts.onProgress?.({ phase: "evaluation", status: "completed", round: 1 });
            solveOpts.onProgress?.({ phase: "revision", status: "started", round: 1 });
            solveOpts.onProgress?.({ phase: "revision", status: "completed", round: 1 });
            solveOpts.onProgress?.({ phase: "finalization", status: "started", round: 1 });
            return {
              progress: 1,
              result: { best_score: 0.9, scenario_name: "saved_task" },
            };
          },
        ) as never,
      },
    });

    const planEvents = emitted.filter((entry) => entry.event === "task_plan_updated");
    expect(planEvents.at(0)?.payload).toMatchObject({
      update_kind: "initial",
      plan_revision: 1,
      active_step_id: "prepare_context",
    });
    expect(
      planEvents.find((entry) => entry.payload.update_kind === "replan")?.payload,
    ).toMatchObject({
      plan_revision: 2,
      active_step_id: "improve_response",
      summary: "Revising the response after evaluation round 1.",
    });
    expect(planEvents.at(-1)?.payload).toMatchObject({
      update_kind: "progress",
      plan_revision: 2,
      active_step_id: null,
    });
    expect(lastIndexOfEvent(emitted, "task_plan_updated")).toBeLessThan(
      emitted.findIndex((entry) => entry.event === "run_completed"),
    );
    const progressNotes = emitted.filter((entry) => entry.event === "agent_progress_note");
    expect(progressNotes.map((entry) => entry.payload.kind)).toEqual([
      "intent",
      "discovery",
      "discovery",
      "verification",
      "discovery",
      "decision",
      "discovery",
      "decision",
      "decision",
    ]);
    expect(progressNotes.at(3)?.payload.text).toContain("Evaluating the current response");
    expect(progressNotes.at(4)?.payload.text).toContain("Evaluation round 1");
    expect(progressNotes.at(5)?.payload.text).toContain("Revising the response");
    expect(progressNotes.at(7)?.payload).toMatchObject({
      generation: 1,
      text: "Packaging the best scored response for retention.",
    });
    expect(lastIndexOfEvent(emitted, "agent_progress_note")).toBeLessThan(
      emitted.findIndex((entry) => entry.event === "run_completed"),
    );
  });

  it("turns a blocked saved-task completion hook into a failed terminal plan", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-agent-task-plan-hook-"));
    const emitted: Array<{ event: string; payload: Record<string, unknown> }> = [];
    try {
      const extensionPath = join(root, "block-completion.mjs");
      writeFileSync(
        extensionPath,
        `
          export function register(api) {
            api.on("run_end", (event) => {
              if (event.payload.status === "completed") {
                throw new Error("completion policy rejected");
              }
            });
          }
        `,
        "utf-8",
      );

      await expect(
        executeAgentTaskCustomStartRun({
          runId: "blocked-completion-run",
          scenarioName: "saved_task",
          entry: {
            name: "saved_task",
            type: "agent_task",
            spec: { taskPrompt: "Do work", judgeRubric: "Do it well" },
            path: "/tmp/saved_task",
            hasGeneratedSource: false,
          },
          generations: 1,
          provider: { name: "test", defaultModel: () => "test", complete: vi.fn() },
          settings: {
            ...makeSettings(),
            extensions: extensionPath,
            extensionFailFast: true,
          },
          controller: new LoopController(),
          events: {
            emit: (event: string, payload: Record<string, unknown>) => {
              emitted.push({ event, payload });
            },
          } as never,
          deps: {
            executeAgentTaskSolve: vi.fn(
              async (solveOpts: { onProgress?: (progress: AgentTaskSolveProgress) => void }) => {
                solveOpts.onProgress?.({ phase: "finalization", status: "started" });
                return {
                  progress: 1,
                  result: { best_score: 0.9, scenario_name: "saved_task" },
                };
              },
            ) as never,
          },
        }),
      ).rejects.toThrow("completion policy rejected");

      const terminalPlan = emitted
        .filter((entry) => entry.event === "task_plan_updated")
        .at(-1)?.payload;
      expect(terminalPlan?.active_step_id).toBeNull();
      expect(terminalPlan?.steps).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ id: "finalize_result", status: "failed" }),
        ]),
      );
      expect(emitted.some((entry) => entry.event === "run_completed")).toBe(false);
      const progressNotes = emitted.filter((entry) => entry.event === "agent_progress_note");
      expect(progressNotes.map((entry) => entry.payload.kind)).toEqual([
        "intent",
        "decision",
        "blocker",
      ]);
      expect(JSON.stringify(progressNotes)).not.toContain("completion policy rejected");
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("retains completed agent-task rounds when stop races natural completion", async () => {
    const emitted: Array<{ event: string; payload: Record<string, unknown> }> = [];
    const controller = new LoopController();
    const executeAgentTaskSolve = vi.fn(async () => {
      controller.requestStop("run_task_stop", "stop-task-1");
      return {
        progress: 2,
        result: { best_score: 0.88, scenario_name: "saved_task" },
      };
    });

    await expect(
      executeAgentTaskCustomStartRun({
        runId: "run_task_stop",
        scenarioName: "saved_task",
        entry: {
          name: "saved_task",
          type: "agent_task",
          spec: { taskPrompt: "Do work", judgeRubric: "Do it well" },
          path: "/tmp/saved_task",
          hasGeneratedSource: false,
        },
        generations: 2,
        provider: { name: "test", defaultModel: () => "test", complete: vi.fn() },
        settings: makeSettings(),
        controller,
        events: {
          emit: (event: string, payload: Record<string, unknown>) => {
            emitted.push({ event, payload });
          },
        } as never,
        deps: { executeAgentTaskSolve: executeAgentTaskSolve as never },
      }),
    ).rejects.toMatchObject({
      name: "RunStopRequestedError",
      runId: "run_task_stop",
      commandId: "stop-task-1",
      completedGenerations: 2,
      bestScore: 0.88,
    });

    expect(emitted.filter((entry) => entry.event === "generation_completed")).toHaveLength(2);
    expect(emitted.some((entry) => entry.event === "run_completed")).toBe(false);
    expect(emitted.some((entry) => entry.event === "run_failed")).toBe(false);
    const interruptedPlan = emitted
      .filter((entry) => entry.event === "task_plan_updated")
      .at(-1)?.payload;
    expect(interruptedPlan?.steps).toEqual(
      expect.arrayContaining([expect.objectContaining({ status: "interrupted" })]),
    );
    expect(
      emitted
        .filter((entry) => entry.event === "agent_progress_note")
        .map((entry) => entry.payload.kind),
    ).toEqual(["intent"]);
  });

  it("prefers an agent-task stop request over a concurrent provider failure", async () => {
    const emitted: Array<{ event: string; payload: Record<string, unknown> }> = [];
    const controller = new LoopController();

    await expect(
      executeAgentTaskCustomStartRun({
        runId: "run_task_stop_failure",
        scenarioName: "saved_task",
        entry: {
          name: "saved_task",
          type: "agent_task",
          spec: { taskPrompt: "Do work", judgeRubric: "Do it well" },
          path: "/tmp/saved_task",
          hasGeneratedSource: false,
        },
        generations: 1,
        provider: { name: "test", defaultModel: () => "test", complete: vi.fn() },
        settings: makeSettings(),
        controller,
        events: {
          emit: (event: string, payload: Record<string, unknown>) => {
            emitted.push({ event, payload });
          },
        } as never,
        deps: {
          executeAgentTaskSolve: vi.fn(async () => {
            controller.requestStop("run_task_stop_failure", "stop-task-failure-1");
            throw new Error("provider disconnected");
          }) as never,
        },
      }),
    ).rejects.toMatchObject({
      name: "RunStopRequestedError",
      runId: "run_task_stop_failure",
      commandId: "stop-task-failure-1",
      completedGenerations: 0,
    });

    expect(emitted.some((entry) => entry.event === "generation_completed")).toBe(false);
    expect(emitted.some((entry) => entry.event === "run_completed")).toBe(false);
    expect(emitted.some((entry) => entry.event === "run_failed")).toBe(false);
  });

  it("emits extension lifecycle hooks for saved agent-task runs", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-agent-task-hooks-"));
    vi.stubGlobal("__autoctxLifecycleEvents", []);
    try {
      const extensionPath = join(root, "lifecycle.mjs");
      writeFileSync(
        extensionPath,
        `
          export function register(api) {
            api.on("*", (event) => {
              globalThis.__autoctxLifecycleEvents.push({
                name: event.name,
                status: event.payload.status ?? "",
                generation: event.payload.generation ?? 0
              });
            });
          }
        `,
        "utf-8",
      );
      const executeAgentTaskSolve = vi.fn(async () => ({
        progress: 1,
        result: { best_score: 0.82, scenario_name: "saved_task" },
      }));

      await executeAgentTaskCustomStartRun({
        runId: "run_task_hooks",
        scenarioName: "saved_task",
        entry: {
          name: "saved_task",
          type: "agent_task",
          spec: { taskPrompt: "Do work", judgeRubric: "Do it well" },
          path: "/tmp/saved_task",
          hasGeneratedSource: false,
        },
        generations: 1,
        provider: { name: "test", defaultModel: () => "test", complete: vi.fn() },
        settings: {
          ...makeSettings(),
          extensions: extensionPath,
          extensionFailFast: true,
        },
        controller: new LoopController(),
        events: { emit: vi.fn() } as never,
        deps: { executeAgentTaskSolve: executeAgentTaskSolve as never },
      });

      expect(readLifecycleEventNames()).toEqual([
        "run_start",
        "generation_start",
        "generation_end",
        "run_end",
      ]);
      expect(readLifecycleStatuses()).toEqual(["", "", "completed", "completed"]);
    } finally {
      vi.unstubAllGlobals();
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("emits failure lifecycle hooks for saved agent-task runs", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-agent-task-hooks-"));
    vi.stubGlobal("__autoctxLifecycleEvents", []);
    try {
      const extensionPath = join(root, "lifecycle-failure.mjs");
      writeFileSync(
        extensionPath,
        `
          export function register(api) {
            api.on("*", (event) => {
              globalThis.__autoctxLifecycleEvents.push({
                name: event.name,
                status: event.payload.status ?? "",
                generation: event.payload.generation ?? 0
              });
            });
          }
        `,
        "utf-8",
      );

      await expect(
        executeAgentTaskCustomStartRun({
          runId: "run_task_hooks_failed",
          scenarioName: "saved_task",
          entry: {
            name: "saved_task",
            type: "agent_task",
            spec: { taskPrompt: "Do work", judgeRubric: "Do it well" },
            path: "/tmp/saved_task",
            hasGeneratedSource: false,
          },
          generations: 1,
          provider: { name: "test", defaultModel: () => "test", complete: vi.fn() },
          settings: {
            ...makeSettings(),
            extensions: extensionPath,
            extensionFailFast: true,
          },
          controller: new LoopController(),
          events: { emit: vi.fn() } as never,
          deps: {
            executeAgentTaskSolve: vi.fn(async () => {
              throw new Error("agent task failed");
            }) as never,
          },
        }),
      ).rejects.toThrow("agent task failed");

      expect(readLifecycleEventNames()).toEqual(["run_start", "run_end"]);
      expect(readLifecycleStatuses()).toEqual(["", "failed"]);
    } finally {
      vi.unstubAllGlobals();
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("pairs stopped lifecycle hooks when an agent-task provider fails after stop", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-agent-task-hooks-"));
    vi.stubGlobal("__autoctxLifecycleEvents", []);
    try {
      const extensionPath = join(root, "lifecycle-stop.mjs");
      writeFileSync(
        extensionPath,
        `
          export function register(api) {
            api.on("*", (event) => {
              globalThis.__autoctxLifecycleEvents.push({
                name: event.name,
                status: event.payload.status ?? "",
                generation: event.payload.generation ?? 0
              });
            });
            api.on("run_end", (event) => {
              if (event.payload.status === "stopped") {
                globalThis.__autoctxLifecycleEvents.push({
                  name: event.name,
                  status: event.payload.status,
                  generation: event.payload.generation ?? 0
                });
                event.blocked = true;
                event.blockReason = "stop policy cannot replace the resolved stop";
              }
            });
          }
        `,
        "utf-8",
      );
      const controller = new LoopController();

      await expect(
        executeAgentTaskCustomStartRun({
          runId: "run_task_hooks_stopped",
          scenarioName: "saved_task",
          entry: {
            name: "saved_task",
            type: "agent_task",
            spec: { taskPrompt: "Do work", judgeRubric: "Do it well" },
            path: "/tmp/saved_task",
            hasGeneratedSource: false,
          },
          generations: 1,
          provider: { name: "test", defaultModel: () => "test", complete: vi.fn() },
          settings: {
            ...makeSettings(),
            extensions: extensionPath,
            extensionFailFast: true,
          },
          controller,
          events: { emit: vi.fn() } as never,
          deps: {
            executeAgentTaskSolve: vi.fn(async () => {
              controller.requestStop("run_task_hooks_stopped", "stop-task-hooks-1");
              throw new Error("provider disconnected");
            }) as never,
          },
        }),
      ).rejects.toMatchObject({
        name: "RunStopRequestedError",
        commandId: "stop-task-hooks-1",
      });

      expect(readLifecycleEventNames()).toEqual(["run_start", "run_end"]);
      expect(readLifecycleStatuses()).toEqual(["", "stopped"]);
    } finally {
      vi.unstubAllGlobals();
      rmSync(root, { recursive: true, force: true });
    }
  });
});

function readLifecycleEventNames(): string[] {
  return readLifecycleEvents().map((event) => event.name);
}

function readLifecycleStatuses(): string[] {
  return readLifecycleEvents().map((event) => event.status);
}

function readLifecycleEvents(): Array<{ name: string; status: string }> {
  const raw = Reflect.get(globalThis, "__autoctxLifecycleEvents");
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw.flatMap((entry) => {
    if (!isRecord(entry)) {
      return [];
    }
    const name = typeof entry.name === "string" ? entry.name : "";
    const status = typeof entry.status === "string" ? entry.status : "";
    return name ? [{ name, status }] : [];
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
