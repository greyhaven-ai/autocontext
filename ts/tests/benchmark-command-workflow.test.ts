import { describe, expect, it, vi } from "vitest";

import {
  BENCHMARK_HELP_TEXT,
  executeBenchmarkCommandWorkflow,
  planBenchmarkCommand,
  renderBenchmarkResult,
} from "../src/cli/benchmark-command-workflow.js";

describe("benchmark command workflow", () => {
  it("exposes stable help text", () => {
    expect(BENCHMARK_HELP_TEXT).toContain("autoctx benchmark");
    expect(BENCHMARK_HELP_TEXT).toContain("--scenario");
    expect(BENCHMARK_HELP_TEXT).toContain("--runs");
    expect(BENCHMARK_HELP_TEXT).toContain("--gens");
  });

  it("plans benchmark command values with resolved scenario and defaults", async () => {
    await expect(
      planBenchmarkCommand(
        {
          scenario: undefined,
          runs: undefined,
          gens: undefined,
          provider: undefined,
          json: false,
        },
        async () => undefined,
      ),
    ).resolves.toEqual({
      scenarioName: "grid_ctf",
      numRuns: 3,
      numGens: 1,
      providerType: undefined,
      json: false,
    });
  });

  it("executes benchmark runs with migrated stores and runner inputs", async () => {
    const migrate = vi.fn();
    const close = vi.fn();
    const run = vi
      .fn()
      .mockResolvedValueOnce({ bestScore: 0.75 })
      .mockResolvedValueOnce({ bestScore: 0.85 });
    const createStore = vi.fn(() => ({ migrate, close }));
    const createRunner = vi.fn(() => ({ run }));
    const assertFamilyContract = vi.fn();
    class FakeScenario {}

    const result = await executeBenchmarkCommandWorkflow({
      dbPath: "/tmp/autocontext.db",
      migrationsDir: "/tmp/migrations",
      runsRoot: "/tmp/runs",
      knowledgeRoot: "/tmp/knowledge",
      plan: {
        scenarioName: "grid_ctf",
        numRuns: 2,
        numGens: 3,
        providerType: "anthropic",
        json: false,
      },
      providerBundle: {
        defaultProvider: { name: "provider" },
        roleProviders: { judge: { name: "judge" } },
        roleModels: { judge: "claude" },
        defaultConfig: { providerType: "anthropic" },
      },
      ScenarioClass: FakeScenario,
      assertFamilyContract,
      createStore,
      createRunner,
      now: () => 12345,
    });

    expect(createStore).toHaveBeenCalledTimes(2);
    expect(createStore).toHaveBeenNthCalledWith(1, "/tmp/autocontext.db");
    expect(migrate).toHaveBeenCalledWith("/tmp/migrations");
    expect(assertFamilyContract).toHaveBeenCalledTimes(2);
    expect(createRunner).toHaveBeenNthCalledWith(1, {
      provider: { name: "provider" },
      roleProviders: { judge: { name: "judge" } },
      roleModels: { judge: "claude" },
      scenario: expect.any(FakeScenario),
      store: { migrate, close },
      runsRoot: "/tmp/runs",
      knowledgeRoot: "/tmp/knowledge",
    });
    expect(run).toHaveBeenNthCalledWith(1, "bench_12345_0", 3);
    expect(run).toHaveBeenNthCalledWith(2, "bench_12345_1", 3);
    expect(close).toHaveBeenCalledTimes(2);
    expect(result).toEqual({
      scenario: "grid_ctf",
      runs: 2,
      generations: 3,
      scores: [0.75, 0.85],
      meanBestScore: 0.8,
      provider: "anthropic",
    });
  });

  it("renders benchmark results as json", () => {
    const rendered = renderBenchmarkResult(
      {
        scenario: "grid_ctf",
        runs: 2,
        generations: 3,
        scores: [0.75, 0.85],
        meanBestScore: 0.8,
        provider: "anthropic",
      },
      true,
    );

    expect(rendered).toEqual({
      stdout: JSON.stringify(
        {
          scenario: "grid_ctf",
          runs: 2,
          generations: 3,
          scores: [0.75, 0.85],
          meanBestScore: 0.8,
          provider: "anthropic",
        },
        null,
        2,
      ),
    });
  });

  it("renders synthetic benchmark note and human-readable summary", () => {
    const rendered = renderBenchmarkResult(
      {
        scenario: "grid_ctf",
        runs: 2,
        generations: 3,
        scores: [0.75, 0.85],
        meanBestScore: 0.8,
        provider: "deterministic",
        synthetic: true,
      },
      false,
    );

    expect(rendered).toEqual({
      stderr: "Note: Running with deterministic provider — results are synthetic.",
      stdout: [
        "Benchmark: grid_ctf, 2 runs x 3 gens",
        "Scores: 0.7500, 0.8500",
        "Mean best score: 0.8000",
      ].join("\n"),
    });
  });
});
