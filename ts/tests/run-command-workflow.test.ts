import { describe, expect, it, vi } from "vitest";

import {
  executeAgentTaskRunCommandWorkflow,
  executeRunCommandWorkflow,
  planRunCommand,
  renderRunResult,
  resolveRunScenario,
  RUN_HELP_TEXT,
} from "../src/cli/run-command-workflow.js";

describe("run command workflow", () => {
  it("exposes stable help text", () => {
    expect(RUN_HELP_TEXT).toContain("autoctx run");
    expect(RUN_HELP_TEXT).toContain("--scenario");
    expect(RUN_HELP_TEXT).toContain("--gens");
    expect(RUN_HELP_TEXT).toContain("--iterations");
    expect(RUN_HELP_TEXT).toContain("--matches");
  });

  it("requires a resolved scenario", async () => {
    await expect(
      planRunCommand(
        {
          scenario: undefined,
          gens: undefined,
          "run-id": undefined,
          provider: undefined,
          matches: undefined,
          json: false,
        },
        async () => undefined,
        {
          defaultGenerations: 2,
          matchesPerGeneration: 3,
        },
        () => 12345,
        vi.fn((raw: string) => Number.parseInt(raw, 10)),
      ),
    ).rejects.toThrow(
      "Error: no scenario configured. Run `autoctx init` or pass <scenario> / --scenario <name>.",
    );
  });

  it("plans run command values with parsed generations, matches, and run id", async () => {
    const parsePositiveInteger = vi.fn((raw: string) => Number.parseInt(raw, 10));

    await expect(
      planRunCommand(
        {
          scenario: "grid_ctf",
          gens: "5",
          "run-id": "run-custom",
          provider: "anthropic",
          matches: "7",
          json: true,
        },
        async (value: string | undefined) => value,
        {
          defaultGenerations: 2,
          matchesPerGeneration: 3,
        },
        () => 12345,
        parsePositiveInteger,
      ),
    ).resolves.toEqual({
      scenarioName: "grid_ctf",
      gens: 5,
      runId: "run-custom",
      providerType: "anthropic",
      matches: 7,
      json: true,
    });

    expect(parsePositiveInteger).toHaveBeenNthCalledWith(1, "5", "--gens");
    expect(parsePositiveInteger).toHaveBeenNthCalledWith(2, "7", "--matches");
  });

  it("accepts a positional scenario and iterations alias", async () => {
    const parsePositiveInteger = vi.fn((raw: string) => Number.parseInt(raw, 10));

    await expect(
      planRunCommand(
        {
          positionals: ["grid_ctf"],
          iterations: "4",
          matches: "2",
        },
        async (value: string | undefined) => value,
        {
          defaultGenerations: 1,
          matchesPerGeneration: 3,
        },
        () => 12345,
        parsePositiveInteger,
      ),
    ).resolves.toMatchObject({
      scenarioName: "grid_ctf",
      gens: 4,
      matches: 2,
    });

    expect(parsePositiveInteger).toHaveBeenNthCalledWith(1, "4", "--iterations");
    expect(parsePositiveInteger).toHaveBeenNthCalledWith(2, "2", "--matches");
  });

  it("prefers precise --scenario and --gens flags over positional aliases", async () => {
    await expect(
      planRunCommand(
        {
          scenario: "support_triage",
          positionals: ["grid_ctf"],
          gens: "5",
          iterations: "4",
        },
        async (value: string | undefined) => value,
        {
          defaultGenerations: 1,
          matchesPerGeneration: 3,
        },
        () => 12345,
        (raw: string) => Number.parseInt(raw, 10),
      ),
    ).resolves.toMatchObject({
      scenarioName: "support_triage",
      gens: 5,
    });
  });

  it("resolves known run scenarios and rejects unknown ones with available names", () => {
    class GridScenario {}
    expect(
      resolveRunScenario("grid_ctf", { grid_ctf: GridScenario }),
    ).toBe(GridScenario);

    expect(() =>
      resolveRunScenario("missing", { grid_ctf: GridScenario, othello: class Othello {} }),
    ).toThrow("Unknown scenario: missing. Available: grid_ctf, othello");
  });

  it("executes a run with provider bundle, settings-derived runner options, and game contract assertion", async () => {
    class FakeScenario {}
    const migrate = vi.fn();
    const close = vi.fn();
    const closeProviderBundle = vi.fn();
    const store = { migrate, close };
    const run = vi.fn().mockResolvedValue({
      runId: "run-custom",
      generationsCompleted: 3,
      bestScore: 0.8123,
      currentElo: 1112.4,
    });
    const createRunner = vi.fn(() => ({ run }));
    const assertFamilyContract = vi.fn();

    const result = await executeRunCommandWorkflow({
      dbPath: "/tmp/autocontext.db",
      migrationsDir: "/tmp/migrations",
      runsRoot: "/tmp/runs",
      knowledgeRoot: "/tmp/knowledge",
      settings: {
        maxRetries: 2,
        backpressureMinDelta: 0.1,
        playbookMaxVersions: 5,
        contextBudgetTokens: 1024,
        curatorEnabled: true,
        curatorConsolidateEveryNGens: 2,
        skillMaxLessons: 6,
        deadEndTrackingEnabled: true,
        deadEndMaxEntries: 10,
        stagnationResetEnabled: true,
        stagnationRollbackThreshold: 0.05,
        stagnationPlateauWindow: 4,
        stagnationPlateauEpsilon: 0.01,
        stagnationDistillTopLessons: 3,
        explorationMode: "balanced",
        notifyWebhookUrl: "https://example.test/hook",
        notifyOn: ["completed"],
      },
      plan: {
        scenarioName: "grid_ctf",
        gens: 3,
        runId: "run-custom",
        providerType: "deterministic",
        matches: 4,
        json: false,
      },
      providerBundle: {
        defaultProvider: { name: "provider" },
        roleProviders: { judge: { name: "judge" } },
        roleModels: { judge: "claude" },
        defaultConfig: { providerType: "deterministic" },
        close: closeProviderBundle,
      },
      ScenarioClass: FakeScenario,
      assertFamilyContract,
      createStore: vi.fn(() => store),
      createRunner,
    });

    expect(migrate).toHaveBeenCalledWith("/tmp/migrations");
    expect(assertFamilyContract).toHaveBeenCalledWith(
      expect.any(FakeScenario),
      "game",
      "scenario 'grid_ctf'",
    );
    expect(createRunner).toHaveBeenCalledWith({
      provider: { name: "provider" },
      roleProviders: { judge: { name: "judge" } },
      roleModels: { judge: "claude" },
      scenario: expect.any(FakeScenario),
      store,
      runsRoot: "/tmp/runs",
      knowledgeRoot: "/tmp/knowledge",
      matchesPerGeneration: 4,
      maxRetries: 2,
      minDelta: 0.1,
      playbookMaxVersions: 5,
      contextBudgetTokens: 1024,
      curatorEnabled: true,
      curatorConsolidateEveryNGens: 2,
      skillMaxLessons: 6,
      deadEndTrackingEnabled: true,
      deadEndMaxEntries: 10,
      stagnationResetEnabled: true,
      stagnationRollbackThreshold: 0.05,
      stagnationPlateauWindow: 4,
      stagnationPlateauEpsilon: 0.01,
      stagnationDistillTopLessons: 3,
      explorationMode: "balanced",
      notifyWebhookUrl: "https://example.test/hook",
      notifyOn: ["completed"],
    });
    expect(run).toHaveBeenCalledWith("run-custom", 3);
    expect(close).toHaveBeenCalled();
    expect(closeProviderBundle).toHaveBeenCalledOnce();
    expect(result).toEqual({
      runId: "run-custom",
      generationsCompleted: 3,
      bestScore: 0.8123,
      currentElo: 1112.4,
      provider: "deterministic",
      synthetic: true,
    });
  });

  it("executes saved agent-task scenarios through the task solve runner", async () => {
    const executeAgentTaskSolve = vi.fn(async () => ({
      progress: 2,
      result: {
        scenario_name: "saved_task",
        best_score: 0.91,
      },
    }));

    const result = await executeAgentTaskRunCommandWorkflow({
      plan: {
        scenarioName: "saved_task",
        gens: 2,
        runId: "run-task",
        providerType: "deterministic",
        matches: 1,
        json: true,
      },
      providerBundle: {
        defaultProvider: { name: "provider" },
        defaultConfig: { providerType: "deterministic" },
      },
      spec: { taskPrompt: "Do work", judgeRubric: "Do it well" },
      executeAgentTaskSolve,
      dbPath: "/tmp/run.db",
      migrationsDir: "/tmp/migrations",
      createStore: vi.fn(() => ({
        migrate: vi.fn(),
        createRun: vi.fn(),
        updateRunStatus: vi.fn(),
        upsertGeneration: vi.fn(),
        close: vi.fn(),
      })),
    });

    expect(executeAgentTaskSolve).toHaveBeenCalledWith({
      provider: { name: "provider" },
      created: {
        name: "saved_task",
        spec: { taskPrompt: "Do work", judgeRubric: "Do it well" },
      },
      generations: 2,
    });
    expect(result).toEqual({
      runId: "run-task",
      generationsCompleted: 2,
      bestScore: 0.91,
      currentElo: 1000,
      provider: "deterministic",
      skillPackage: {
        scenario_name: "saved_task",
        best_score: 0.91,
      },
      synthetic: true,
    });
  });

  it("persists saved agent-task runs and completed generations", async () => {
    const closeProviderBundle = vi.fn();
    const store = {
      migrate: vi.fn(),
      createRun: vi.fn(),
      updateRunStatus: vi.fn(),
      upsertGeneration: vi.fn(),
      close: vi.fn(),
    };

    await executeAgentTaskRunCommandWorkflow({
      plan: {
        scenarioName: "saved_task",
        gens: 2,
        runId: "run-task",
        providerType: "deterministic",
        matches: 1,
        json: true,
      },
      providerBundle: {
        defaultProvider: { name: "provider" },
        defaultConfig: { providerType: "deterministic" },
        close: closeProviderBundle,
      },
      spec: { taskPrompt: "Do work", judgeRubric: "Do it well" },
      executeAgentTaskSolve: vi.fn(async () => ({
        progress: 2,
        result: { scenario_name: "saved_task", best_score: 0.91 },
      })),
      dbPath: "/tmp/run.db",
      migrationsDir: "/tmp/migrations",
      createStore: vi.fn(() => store),
    });

    expect(store.migrate).toHaveBeenCalledWith("/tmp/migrations");
    expect(store.createRun).toHaveBeenCalledWith(
      "run-task",
      "saved_task",
      2,
      "agent_task",
      "deterministic",
    );
    expect(store.upsertGeneration).toHaveBeenCalledTimes(2);
    expect(store.upsertGeneration).toHaveBeenNthCalledWith(2, "run-task", 2, {
      meanScore: 0.91,
      bestScore: 0.91,
      elo: 1000,
      wins: 0,
      losses: 0,
      gateDecision: "advance",
      status: "completed",
      scoringBackend: "agent_task",
    });
    expect(store.updateRunStatus).toHaveBeenCalledWith("run-task", "completed");
    expect(store.close).toHaveBeenCalledOnce();
    expect(closeProviderBundle).toHaveBeenCalledOnce();
  });

  it("closes provider bundles when run execution fails", async () => {
    class FakeScenario {}
    const closeProviderBundle = vi.fn();
    const store = {
      migrate: vi.fn(),
      close: vi.fn(),
    };
    const runError = new Error("runner failed");
    const createRunner = vi.fn(() => ({
      run: vi.fn().mockRejectedValue(runError),
    }));

    await expect(
      executeRunCommandWorkflow({
        dbPath: "/tmp/autocontext.db",
        migrationsDir: "/tmp/migrations",
        runsRoot: "/tmp/runs",
        knowledgeRoot: "/tmp/knowledge",
        settings: {
          maxRetries: 2,
          backpressureMinDelta: 0.1,
          playbookMaxVersions: 5,
          contextBudgetTokens: 1024,
          curatorEnabled: true,
          curatorConsolidateEveryNGens: 2,
          skillMaxLessons: 6,
          deadEndTrackingEnabled: true,
          deadEndMaxEntries: 10,
          stagnationResetEnabled: true,
          stagnationRollbackThreshold: 0.05,
          stagnationPlateauWindow: 4,
          stagnationPlateauEpsilon: 0.01,
          stagnationDistillTopLessons: 3,
          explorationMode: "balanced",
          notifyWebhookUrl: "",
          notifyOn: [],
        },
        plan: {
          scenarioName: "grid_ctf",
          gens: 3,
          runId: "run-failed",
          providerType: "deterministic",
          matches: 4,
          json: false,
        },
        providerBundle: {
          defaultProvider: { name: "provider" },
          roleProviders: {},
          roleModels: {},
          defaultConfig: { providerType: "deterministic" },
          close: closeProviderBundle,
        },
        ScenarioClass: FakeScenario,
        assertFamilyContract: vi.fn(),
        createStore: vi.fn(() => store),
        createRunner,
      }),
    ).rejects.toThrow(runError);

    expect(store.close).toHaveBeenCalledOnce();
    expect(closeProviderBundle).toHaveBeenCalledOnce();
  });

  it("renders json and human-readable run results", () => {
    expect(
      renderRunResult(
        {
          runId: "run-123",
          generationsCompleted: 2,
          bestScore: 0.8123,
          currentElo: 1112.4,
          provider: "deterministic",
          synthetic: true,
        },
        true,
      ),
    ).toEqual({
      stdout: JSON.stringify(
        {
          runId: "run-123",
          generationsCompleted: 2,
          bestScore: 0.8123,
          currentElo: 1112.4,
          provider: "deterministic",
          synthetic: true,
        },
        null,
        2,
      ),
    });

    expect(
      renderRunResult(
        {
          runId: "run-123",
          generationsCompleted: 2,
          bestScore: 0.8123,
          currentElo: 1112.4,
          provider: "deterministic",
          synthetic: true,
        },
        false,
      ),
    ).toEqual({
      stderr: "Note: Running with deterministic provider — results are synthetic.",
      stdout: "Run run-123: 2 generations, best score 0.8123, Elo 1112.4",
    });
  });
});
