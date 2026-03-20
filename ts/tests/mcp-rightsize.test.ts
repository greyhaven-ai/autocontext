/**
 * Tests for AC-312: Rightsize TS MCP server — add scenario discovery,
 * run control, and knowledge access tools.
 *
 * Since McpServer doesn't expose a direct callTool API, we test:
 * 1. The underlying query methods (SQLiteStore.listRuns, etc.)
 * 2. The server builds without error with new tools registered
 * 3. The tool handler functions via extracted helpers
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-mcp-rightsize-"));
}

// ---------------------------------------------------------------------------
// SQLiteStore.listRuns + getMatchesForGeneration (prerequisite queries)
// ---------------------------------------------------------------------------

describe("SQLiteStore.listRuns", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("returns empty array when no runs", async () => {
    const { SQLiteStore } = await import("../src/storage/index.js");
    const store = new SQLiteStore(join(dir, "test.db"));
    store.migrate(join(__dirname, "..", "migrations"));
    expect(store.listRuns()).toEqual([]);
    store.close();
  });

  it("returns all runs", async () => {
    const { SQLiteStore } = await import("../src/storage/index.js");
    const store = new SQLiteStore(join(dir, "test.db"));
    store.migrate(join(__dirname, "..", "migrations"));
    store.createRun("run-a", "grid_ctf", 3, "local");
    store.createRun("run-b", "grid_ctf", 5, "local");
    const runs = store.listRuns();
    expect(runs).toHaveLength(2);
    const ids = runs.map(r => r.run_id);
    expect(ids).toContain("run-a");
    expect(ids).toContain("run-b");
    store.close();
  });

  it("accepts limit parameter", async () => {
    const { SQLiteStore } = await import("../src/storage/index.js");
    const store = new SQLiteStore(join(dir, "test.db"));
    store.migrate(join(__dirname, "..", "migrations"));
    store.createRun("run-1", "grid_ctf", 1, "local");
    store.createRun("run-2", "grid_ctf", 1, "local");
    store.createRun("run-3", "grid_ctf", 1, "local");
    const runs = store.listRuns(2);
    expect(runs).toHaveLength(2);
    store.close();
  });

  it("accepts scenario filter", async () => {
    const { SQLiteStore } = await import("../src/storage/index.js");
    const store = new SQLiteStore(join(dir, "test.db"));
    store.migrate(join(__dirname, "..", "migrations"));
    store.createRun("run-a", "grid_ctf", 3, "local");
    store.createRun("run-b", "othello", 2, "local");
    const runs = store.listRuns(50, "grid_ctf");
    expect(runs).toHaveLength(1);
    expect(runs[0].scenario).toBe("grid_ctf");
    store.close();
  });
});

describe("SQLiteStore.getMatchesForGeneration", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("returns matches for specific generation", async () => {
    const { SQLiteStore } = await import("../src/storage/index.js");
    const store = new SQLiteStore(join(dir, "test.db"));
    store.migrate(join(__dirname, "..", "migrations"));
    store.createRun("run-1", "grid_ctf", 3, "local");
    store.upsertGeneration("run-1", 1, {
      meanScore: 0.65, bestScore: 0.70, elo: 1050,
      wins: 3, losses: 2, gateDecision: "advance", status: "completed",
    });
    store.upsertGeneration("run-1", 2, {
      meanScore: 0.75, bestScore: 0.80, elo: 1100,
      wins: 4, losses: 1, gateDecision: "advance", status: "completed",
    });
    store.recordMatch("run-1", 1, { seed: 42, score: 0.70, passedValidation: true, validationErrors: "" });
    store.recordMatch("run-1", 2, { seed: 43, score: 0.80, passedValidation: true, validationErrors: "" });
    const gen1Matches = store.getMatchesForGeneration("run-1", 1);
    expect(gen1Matches).toHaveLength(1);
    expect(gen1Matches[0].seed).toBe(42);
    const gen2Matches = store.getMatchesForGeneration("run-1", 2);
    expect(gen2Matches).toHaveLength(1);
    expect(gen2Matches[0].seed).toBe(43);
    store.close();
  });
});

// ---------------------------------------------------------------------------
// MCP Server structure — builds with new tools
// ---------------------------------------------------------------------------

describe("MCP server with expanded tools", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("createMcpServer builds with runsRoot and knowledgeRoot opts", async () => {
    const { SQLiteStore } = await import("../src/storage/index.js");
    const { DeterministicProvider } = await import("../src/providers/deterministic.js");
    const { createMcpServer } = await import("../src/mcp/server.js");

    const store = new SQLiteStore(join(dir, "test.db"));
    store.migrate(join(__dirname, "..", "migrations"));
    const server = createMcpServer({
      store,
      provider: new DeterministicProvider(),
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
    });
    expect(server).toBeDefined();
    store.close();
  });
});

// ---------------------------------------------------------------------------
// Tool handler logic (tested via extracted helpers)
// ---------------------------------------------------------------------------

describe("Scenario discovery helpers", () => {
  it("SCENARIO_REGISTRY has grid_ctf", async () => {
    const { SCENARIO_REGISTRY } = await import("../src/scenarios/registry.js");
    expect(SCENARIO_REGISTRY.grid_ctf).toBeDefined();
    const instance = new SCENARIO_REGISTRY.grid_ctf();
    expect(instance.name).toBe("grid_ctf");
    expect(instance.describeRules().length).toBeGreaterThan(0);
    expect(instance.scoringDimensions()).not.toBeNull();
  });

  it("scenario instance has all required methods for MCP tools", async () => {
    const { SCENARIO_REGISTRY } = await import("../src/scenarios/registry.js");
    const instance = new SCENARIO_REGISTRY.grid_ctf();
    expect(typeof instance.describeRules).toBe("function");
    expect(typeof instance.describeStrategyInterface).toBe("function");
    expect(typeof instance.describeEvaluationCriteria).toBe("function");
    expect(typeof instance.scoringDimensions).toBe("function");
  });
});

describe("Knowledge access helpers", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("ArtifactStore reads playbook for MCP get_playbook tool", async () => {
    const { ArtifactStore } = await import("../src/knowledge/artifact-store.js");
    const artifacts = new ArtifactStore({
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
    });
    artifacts.writePlaybook("grid_ctf", "# Playbook\n\nBe aggressive.");
    const content = artifacts.readPlaybook("grid_ctf");
    expect(content).toContain("Be aggressive");
  });

  it("ArtifactStore returns sentinel for missing playbook", async () => {
    const { ArtifactStore, EMPTY_PLAYBOOK_SENTINEL } = await import("../src/knowledge/artifact-store.js");
    const artifacts = new ArtifactStore({
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
    });
    expect(artifacts.readPlaybook("grid_ctf")).toBe(EMPTY_PLAYBOOK_SENTINEL);
  });
});

describe("Run control helpers", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("GenerationRunner can be instantiated for MCP run_scenario tool", async () => {
    const { GenerationRunner } = await import("../src/loop/generation-runner.js");
    const { DeterministicProvider } = await import("../src/providers/deterministic.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const { SQLiteStore } = await import("../src/storage/index.js");

    const store = new SQLiteStore(join(dir, "test.db"));
    store.migrate(join(__dirname, "..", "migrations"));

    const runner = new GenerationRunner({
      provider: new DeterministicProvider(),
      scenario: new GridCtfScenario(),
      store,
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
      matchesPerGeneration: 2,
    });
    expect(runner).toBeDefined();

    // Quick single-gen run to prove the tool would work
    const result = await runner.run("mcp-test", 1);
    expect(result.generationsCompleted).toBe(1);

    store.close();
  });

  it("run status combines run + generations for MCP get_run_status tool", async () => {
    const { SQLiteStore } = await import("../src/storage/index.js");
    const store = new SQLiteStore(join(dir, "test.db"));
    store.migrate(join(__dirname, "..", "migrations"));

    store.createRun("run-1", "grid_ctf", 3, "local");
    store.upsertGeneration("run-1", 1, {
      meanScore: 0.65, bestScore: 0.70, elo: 1050,
      wins: 3, losses: 2, gateDecision: "advance", status: "completed",
    });

    const run = store.getRun("run-1");
    const gens = store.getGenerations("run-1");
    expect(run).not.toBeNull();
    expect(gens).toHaveLength(1);
    expect(gens[0].best_score).toBeCloseTo(0.70);

    store.close();
  });

  it("generation detail combines gen + matches + outputs for MCP tool", async () => {
    const { SQLiteStore } = await import("../src/storage/index.js");
    const store = new SQLiteStore(join(dir, "test.db"));
    store.migrate(join(__dirname, "..", "migrations"));

    store.createRun("run-1", "grid_ctf", 3, "local");
    store.upsertGeneration("run-1", 1, {
      meanScore: 0.65, bestScore: 0.70, elo: 1050,
      wins: 3, losses: 2, gateDecision: "advance", status: "completed",
    });
    store.recordMatch("run-1", 1, { seed: 42, score: 0.70, passedValidation: true, validationErrors: "" });
    store.appendAgentOutput("run-1", 1, "competitor", '{"aggression": 0.6}');

    const gens = store.getGenerations("run-1");
    const gen = gens.find(g => g.generation_index === 1);
    expect(gen).toBeDefined();
    const matches = store.getMatchesForGeneration("run-1", 1);
    expect(matches).toHaveLength(1);
    const outputs = store.getAgentOutputs("run-1", 1);
    expect(outputs).toHaveLength(1);
    expect(outputs[0].role).toBe("competitor");

    store.close();
  });
});
