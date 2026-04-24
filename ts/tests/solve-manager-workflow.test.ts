import { describe, expect, it, vi } from "vitest";

import {
  buildSolveJobId,
  executeSolveJobWorkflow,
} from "../src/knowledge/solve-manager-workflow.js";
import { createSolveJob } from "../src/knowledge/solve-workflow.js";

describe("solve manager workflow", () => {
  it("builds stable-looking solve job ids", () => {
    expect(buildSolveJobId()).toMatch(/^solve_[a-z0-9]+_[a-z0-9]{6}$/);
  });

  it("routes agent-task jobs through scenario preparation, persistence, and execution", async () => {
    const job = createSolveJob("solve_job", "Summarize outage escalations", 2);
    const executeAgentTaskSolve = vi.fn(async () => ({
      progress: 1,
      result: { scenario_name: "incident_triage", best_score: 0.93 },
    }));

    await executeSolveJobWorkflow({
      job,
      provider: { name: "mock", defaultModel: () => "mock", complete: vi.fn() } as never,
      store: {} as never,
      runsRoot: "/tmp/runs",
      knowledgeRoot: "/tmp/knowledge",
      deps: {
        createScenarioFromDescription: vi.fn(async () => ({
          name: "incident_triage",
          family: "agent_task",
          spec: { taskPrompt: "Summarize incident reports", rubric: "Evaluate completeness" },
        })),
        listBuiltinScenarioNames: vi.fn(async () => ["grid_ctf"]),
        prepareSolveScenario: vi.fn(({ created, description }) => ({ ...created, description })) as never,
        determineSolveExecutionRoute: vi.fn(() => "agent_task") as never,
        persistSolveScenarioScaffold: vi.fn(async () => ({ persisted: true, errors: [] })) as never,
        executeBuiltInGameSolve: vi.fn() as never,
        executeAgentTaskSolve: executeAgentTaskSolve as never,
        executeCodegenSolve: vi.fn() as never,
        failSolveJob: vi.fn((failedJob, error) => {
          failedJob.status = "failed";
          failedJob.error = error instanceof Error ? error.message : String(error);
        }),
      },
    });

    expect(executeAgentTaskSolve).toHaveBeenCalledWith({
      provider: expect.objectContaining({ name: "mock" }),
      created: expect.objectContaining({ name: "incident_triage" }),
      generations: 2,
    });
    expect(job).toMatchObject({
      status: "completed",
      scenarioName: "incident_triage",
      family: "agent_task",
      progress: 1,
      result: { scenario_name: "incident_triage", best_score: 0.93 },
    });
  });

  it("reports built-in scenario solves as game family even when designer metadata drifts", async () => {
    const job = createSolveJob("solve_builtin", "grid ctf", 1);
    const executeBuiltInGameSolve = vi.fn(async () => ({
      progress: 1,
      result: { scenario_name: "grid_ctf", best_score: 0.73 },
    }));

    await executeSolveJobWorkflow({
      job,
      provider: { name: "mock", defaultModel: () => "mock", complete: vi.fn() } as never,
      store: {} as never,
      runsRoot: "/tmp/runs",
      knowledgeRoot: "/tmp/knowledge",
      deps: {
        createScenarioFromDescription: vi.fn(async () => ({
          name: "grid_ctf",
          family: "agent_task",
          spec: { taskPrompt: "grid ctf", rubric: "score" },
        })),
        listBuiltinScenarioNames: vi.fn(async () => ["grid_ctf"]),
        prepareSolveScenario: vi.fn(({ created, description }) => ({ ...created, description })) as never,
        determineSolveExecutionRoute: vi.fn(() => "builtin_game") as never,
        persistSolveScenarioScaffold: vi.fn() as never,
        executeBuiltInGameSolve: executeBuiltInGameSolve as never,
        executeAgentTaskSolve: vi.fn() as never,
        executeCodegenSolve: vi.fn() as never,
        failSolveJob: vi.fn((failedJob, error) => {
          failedJob.status = "failed";
          failedJob.error = error instanceof Error ? error.message : String(error);
        }),
      },
    });

    expect(executeBuiltInGameSolve).toHaveBeenCalledWith(
      expect.objectContaining({
        scenarioName: "grid_ctf",
        generations: 1,
      }),
    );
    expect(job).toMatchObject({
      status: "completed",
      scenarioName: "grid_ctf",
      family: "game",
      result: { scenario_name: "grid_ctf", best_score: 0.73 },
    });
  });

  it("records route/materialization failures on the solve job", async () => {
    const job = createSolveJob("solve_fail", "Create a game", 1);
    const failSolveJob = vi.fn((failedJob, error) => {
      failedJob.status = "failed";
      failedJob.error = error instanceof Error ? error.message : String(error);
    });

    await executeSolveJobWorkflow({
      job,
      provider: { name: "mock", defaultModel: () => "mock", complete: vi.fn() } as never,
      store: {} as never,
      runsRoot: "/tmp/runs",
      knowledgeRoot: "/tmp/knowledge",
      deps: {
        createScenarioFromDescription: vi.fn(async () => ({
          name: "grid_ctf",
          family: "game",
          spec: {},
        })),
        listBuiltinScenarioNames: vi.fn(async () => []),
        prepareSolveScenario: vi.fn(({ created, description }) => ({ ...created, description })) as never,
        determineSolveExecutionRoute: vi.fn(() => "missing_game") as never,
        persistSolveScenarioScaffold: vi.fn(async () => ({ persisted: true, errors: [] })) as never,
        executeBuiltInGameSolve: vi.fn() as never,
        executeAgentTaskSolve: vi.fn() as never,
        executeCodegenSolve: vi.fn() as never,
        failSolveJob,
      },
    });

    expect(failSolveJob).toHaveBeenCalledOnce();
    expect(job.status).toBe("failed");
    expect(job.error).toContain("Game scenario 'grid_ctf' not found in SCENARIO_REGISTRY");
  });
});
