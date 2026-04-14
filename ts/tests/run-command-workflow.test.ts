import { describe, expect, it, vi } from "vitest";

import {
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
      "Error: no scenario configured. Run `autoctx init` or pass --scenario <name>.",
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
    expect(result).toEqual({
      runId: "run-custom",
      generationsCompleted: 3,
      bestScore: 0.8123,
      currentElo: 1112.4,
      provider: "deterministic",
      synthetic: true,
    });
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
