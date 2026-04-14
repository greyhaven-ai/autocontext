import { describe, expect, it, beforeEach, afterEach } from "vitest";
import {
  existsSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  loadPersistedSimulationSpec,
  loadSimulationReport,
  persistSimulationArtifacts,
  resolveSimulationArtifact,
} from "../src/simulation/artifact-store.js";
import type { SimulationResult } from "../src/simulation/types.js";

let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac-sim-artifacts-"));
});

afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

describe("simulation artifact store", () => {
  it("persists simulation spec, source, and family marker", () => {
    const scenarioDir = persistSimulationArtifacts({
      knowledgeRoot: tmpDir,
      name: "deploy_test",
      family: "simulation",
      spec: {
        description: "Deploy test",
        max_steps: 3,
        actions: [{ name: "step_a" }],
      },
      source: "module.exports = { scenario: {} };",
    });

    expect(existsSync(scenarioDir)).toBe(true);
    expect(existsSync(join(scenarioDir, "spec.json"))).toBe(true);
    expect(existsSync(join(scenarioDir, "scenario.js"))).toBe(true);
    expect(existsSync(join(scenarioDir, "scenario_type.txt"))).toBe(true);

    const persisted = JSON.parse(readFileSync(join(scenarioDir, "spec.json"), "utf-8")) as Record<string, unknown>;
    expect(persisted.name).toBe("deploy_test");
    expect(persisted.family).toBe("simulation");
  });

  it("loads a persisted simulation spec without artifact metadata wrappers", () => {
    const specPath = join(tmpDir, "spec.json");
    writeFileSync(
      specPath,
      JSON.stringify({
        name: "deploy_test",
        family: "simulation",
        description: "Deploy test",
        max_steps: 5,
      }),
      "utf-8",
    );

    expect(loadPersistedSimulationSpec(specPath)).toEqual({
      description: "Deploy test",
      max_steps: 5,
    });
  });

  it("loads a saved simulation report by simulation name", () => {
    const report: SimulationResult = {
      id: "sim_base",
      name: "deploy_test",
      family: "simulation",
      status: "completed",
      description: "Deploy test",
      assumptions: [],
      variables: {},
      summary: { score: 0.8, reasoning: "ok", dimensionScores: { completion: 0.8 } },
      artifacts: { scenarioDir: join(tmpDir, "_simulations", "deploy_test") },
      warnings: [],
    };

    mkdirSync(join(tmpDir, "_simulations", "deploy_test"), { recursive: true });
    writeFileSync(
      join(tmpDir, "_simulations", "deploy_test", "report.json"),
      JSON.stringify(report, null, 2),
      "utf-8",
    );

    expect(loadSimulationReport(tmpDir, "deploy_test")).toEqual(report);
  });

  it("resolves replay reports by replay id", () => {
    const scenarioDir = join(tmpDir, "_simulations", "deploy_test");
    mkdirSync(scenarioDir, { recursive: true });

    const replay: SimulationResult = {
      id: "sim_replay",
      name: "deploy_test",
      family: "simulation",
      status: "completed",
      description: "Replay",
      assumptions: [],
      variables: { max_steps: 1 },
      summary: { score: 0.4, reasoning: "replay", dimensionScores: { completion: 0.4 } },
      artifacts: { scenarioDir },
      warnings: [],
      replayOf: "deploy_test",
    };

    writeFileSync(
      join(scenarioDir, "replay_sim_replay.json"),
      JSON.stringify(replay, null, 2),
      "utf-8",
    );

    const resolved = resolveSimulationArtifact(tmpDir, "sim_replay");
    expect(resolved).toMatchObject({
      scenarioDir,
      reportPath: join(scenarioDir, "replay_sim_replay.json"),
      report: replay,
    });
  });
});
