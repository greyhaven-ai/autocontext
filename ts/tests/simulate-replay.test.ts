/**
 * AC-450: simulate replay — re-execute saved simulations.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import {
  mkdtempSync,
  rmSync,
  existsSync,
  readFileSync,
  mkdirSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";
import {
  SimulationEngine,
  type SimulationResult,
} from "../src/simulation/engine.js";
import type { LLMProvider } from "../src/types/index.js";

const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");
const SANITIZED_KEYS = [
  "ANTHROPIC_API_KEY",
  "OPENAI_API_KEY",
  "AUTOCONTEXT_API_KEY",
  "AUTOCONTEXT_AGENT_API_KEY",
  "AUTOCONTEXT_PROVIDER",
  "AUTOCONTEXT_AGENT_PROVIDER",
  "AUTOCONTEXT_DB_PATH",
  "AUTOCONTEXT_RUNS_ROOT",
  "AUTOCONTEXT_KNOWLEDGE_ROOT",
  "AUTOCONTEXT_CONFIG_DIR",
  "AUTOCONTEXT_AGENT_DEFAULT_MODEL",
  "AUTOCONTEXT_MODEL",
];

function mockProvider(): LLMProvider {
  const spec = JSON.stringify({
    description: "Test simulation",
    environment_description: "Test env",
    initial_state_description: "Start",
    success_criteria: ["done"],
    failure_modes: ["timeout"],
    max_steps: 10,
    actions: [
      {
        name: "step_a",
        description: "A",
        parameters: {},
        preconditions: [],
        effects: ["a_done"],
      },
      {
        name: "step_b",
        description: "B",
        parameters: {},
        preconditions: ["step_a"],
        effects: ["b_done"],
      },
    ],
  });
  return {
    complete: async () => ({ text: spec }),
    defaultModel: () => "test-model",
  } as unknown as LLMProvider;
}

function buildEnv(overrides: Record<string, string> = {}): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env, NODE_NO_WARNINGS: "1" };
  for (const key of SANITIZED_KEYS) delete env[key];
  return { ...env, ...overrides };
}

function writeSimulationFixture(
  root: string,
  name: string,
  {
    report,
    spec,
    source,
  }: {
    report: SimulationResult;
    spec: Record<string, unknown>;
    source: string;
  },
): string {
  const dir = join(root, "_simulations", name);
  mkdirSync(dir, { recursive: true });
  writeFileSync(
    join(dir, "report.json"),
    JSON.stringify(report, null, 2),
    "utf-8",
  );
  writeFileSync(
    join(dir, "spec.json"),
    JSON.stringify({ name, family: report.family, ...spec }, null, 2),
    "utf-8",
  );
  writeFileSync(join(dir, "scenario.js"), source, "utf-8");
  return dir;
}

function seedSensitiveScenarioSource(name: string): string {
  return `const scenario = {
  name: ${JSON.stringify(name)},
  initialState(seed) { return { seed: seed || 0, step: 0 }; },
  isTerminal(state) { return (state.step || 0) >= 1; },
  getAvailableActions(state) { return (state.step || 0) >= 1 ? [] : [{ name: "step" }]; },
  executeAction(state, action) {
    return { result: { success: true, output: action.name }, state: { ...state, step: (state.step || 0) + 1 } };
  },
  getResult(state) { return { score: (state.seed || 0) / 10, reasoning: "seed " + state.seed, dimensionScores: { completion: (state.seed || 0) / 10 } }; },
};
module.exports = { scenario };
`;
}

let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac-450-test-"));
});
afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// Replay from saved simulation
// ---------------------------------------------------------------------------

describe("simulate replay", () => {
  it("replays a previously saved simulation", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    // First: run and save
    const original = await engine.run({
      description: "Deploy pipeline simulation",
      saveAs: "deploy_test",
    });
    expect(original.status).toBe("completed");
    expect(
      existsSync(join(original.artifacts.scenarioDir, "report.json")),
    ).toBe(true);

    // Replay
    const replay = await engine.replay({ id: "deploy_test" });
    expect(replay.status).toBe("completed");
    expect(replay.name).toBe("deploy_test");
    expect(replay.family).toBe(original.family);
    expect(typeof replay.summary.score).toBe("number");
  });

  it("replay produces same score with same seed (deterministic)", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    const original = await engine.run({
      description: "Deterministic test",
      saveAs: "determ_test",
    });

    const replay = await engine.replay({ id: "determ_test" });

    // Same generated code + same seed = same score
    expect(replay.summary.score).toBe(original.summary.score);
  });

  it("replay with variable overrides changes the run", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    const original = await engine.run({
      description: "Override test",
      saveAs: "override_test",
      variables: { max_steps: 1 },
    });

    const replay = await engine.replay({
      id: "override_test",
      variables: { max_steps: 2 },
    });

    expect(replay.status).toBe("completed");
    expect(replay.variables.max_steps).toBe(2);
    expect(replay.summary.score).toBeGreaterThan(original.summary.score);
  });

  it("replay with different maxSteps", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    await engine.run({
      description: "Steps test",
      saveAs: "steps_test",
    });

    const replay = await engine.replay({
      id: "steps_test",
      maxSteps: 3,
    });

    expect(replay.status).toBe("completed");
  });

  it("replays a saved sweep instead of collapsing to a single rerun", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    const original = await engine.run({
      description: "Sweep replay test",
      saveAs: "sweep_test",
      sweep: [{ name: "max_steps", values: [1, 2] }],
      runs: 2,
    });

    expect(original.sweep?.results).toHaveLength(2);

    const replay = await engine.replay({ id: "sweep_test" });

    expect(replay.status).toBe("completed");
    expect(replay.sweep?.results).toHaveLength(2);
    expect(replay.execution?.runs).toBe(2);
    expect(replay.execution?.sweep?.map((dim) => dim.name)).toEqual([
      "max_steps",
    ]);
  });

  it("replays the saved run count instead of forcing one run", async () => {
    const report: SimulationResult = {
      id: "sim_original",
      name: "seeded_test",
      family: "simulation",
      status: "completed",
      description: "Seed-sensitive replay fixture",
      assumptions: ["fixture"],
      variables: {},
      summary: {
        score: 0.1,
        reasoning: "Average across 3 runs",
        dimensionScores: { completion: 0.1 },
      },
      execution: { runs: 3 },
      artifacts: { scenarioDir: join(tmpDir, "_simulations", "seeded_test") },
      warnings: [],
    };

    writeSimulationFixture(tmpDir, "seeded_test", {
      report,
      spec: {
        description: "Seed-sensitive replay fixture",
        environment_description: "Fixture",
        initial_state_description: "Start",
        success_criteria: ["done"],
        failure_modes: ["timeout"],
        max_steps: 1,
        actions: [
          {
            name: "step",
            description: "step",
            parameters: {},
            preconditions: [],
            effects: [],
          },
        ],
      },
      source: seedSensitiveScenarioSource("seeded_test"),
    });

    const engine = new SimulationEngine(mockProvider(), tmpDir);
    const replay = await engine.replay({ id: "seeded_test" });

    expect(["completed", "degraded"]).toContain(replay.status);
    expect(replay.execution?.runs).toBe(3);
    expect(replay.summary.score).toBe(0.1);
  });

  it("replay persists its own report", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    await engine.run({
      description: "Persist test",
      saveAs: "persist_test",
    });

    const replay = await engine.replay({ id: "persist_test" });

    // Replay should have its own report
    expect(replay.artifacts.reportPath).toBeTruthy();
    expect(existsSync(replay.artifacts.reportPath!)).toBe(true);

    const saved = JSON.parse(
      readFileSync(replay.artifacts.reportPath!, "utf-8"),
    );
    expect(saved.name).toBe("persist_test");
  });

  it("fails with clear error for nonexistent simulation", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    const result = await engine.replay({ id: "nonexistent" });
    expect(result.status).toBe("failed");
    expect(result.error).toContain("not found");
  });

  it("includes original vs replay comparison data", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    await engine.run({
      description: "Compare test",
      saveAs: "compare_test",
    });

    const replay = await engine.replay({ id: "compare_test" });

    expect(replay.replayOf).toBe("compare_test");
    expect(replay.originalScore).toBeDefined();
    expect(typeof replay.originalScore).toBe("number");
    expect(typeof replay.scoreDelta).toBe("number");
  });
});

describe("simulate replay CLI integration", () => {
  it("replays saved simulations without requiring provider credentials", () => {
    const cwd = mkdtempSync(join(tmpdir(), "ac-450-cli-"));
    try {
      const knowledgeRoot = join(cwd, "knowledge");
      const scenarioDir = join(knowledgeRoot, "_simulations", "cli_replay");
      const report: SimulationResult = {
        id: "sim_cli",
        name: "cli_replay",
        family: "simulation",
        status: "completed",
        description: "CLI replay fixture",
        assumptions: ["fixture"],
        variables: {},
        summary: {
          score: 0.4,
          reasoning: "fixture",
          dimensionScores: { completion: 0.4 },
        },
        execution: { runs: 1 },
        artifacts: {
          scenarioDir,
          reportPath: join(scenarioDir, "report.json"),
        },
        warnings: [],
      };

      writeSimulationFixture(knowledgeRoot, "cli_replay", {
        report,
        spec: {
          description: "CLI replay fixture",
          environment_description: "Fixture",
          initial_state_description: "Start",
          success_criteria: ["done"],
          failure_modes: ["timeout"],
          max_steps: 1,
          actions: [
            {
              name: "step",
              description: "step",
              parameters: {},
              preconditions: [],
              effects: [],
            },
          ],
        },
        source: seedSensitiveScenarioSource("cli_replay"),
      });

      const result = spawnSync(
        "npx",
        ["tsx", CLI, "simulate", "--replay", "cli_replay", "--json"],
        {
          cwd,
          encoding: "utf-8",
          env: buildEnv(),
          timeout: 15000,
        },
      );

      expect(result.status).toBe(0);
      const parsed = JSON.parse(result.stdout) as SimulationResult;
      expect(["completed", "degraded"]).toContain(parsed.status);
      expect(parsed.replayOf).toBe("cli_replay");
    } finally {
      rmSync(cwd, { recursive: true, force: true });
    }
  });
});
