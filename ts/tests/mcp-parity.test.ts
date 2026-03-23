/**
 * Tests for AC-365: MCP server package-surface parity.
 *
 * Verifies the new MCP tools are registered and the underlying
 * logic is accessible. Since McpServer doesn't expose a direct
 * callTool API, we test the helper functions that back each tool.
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
  return mkdtempSync(join(tmpdir(), "ac-mcp-parity-"));
}

// ---------------------------------------------------------------------------
// validate_strategy tool
// ---------------------------------------------------------------------------

describe("MCP validate_strategy", () => {
  it("validates a correct grid_ctf strategy", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    const [valid, reason] = scenario.validateActions(
      scenario.initialState(42),
      "challenger",
      { aggression: 0.6, defense: 0.4, path_bias: 0.5 },
    );
    expect(valid).toBe(true);
    expect(reason).toBe("ok");
  });

  it("rejects invalid strategy", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    const [valid, reason] = scenario.validateActions(
      scenario.initialState(42),
      "challenger",
      { aggression: 2.0, defense: 0.4, path_bias: 0.5 },
    );
    expect(valid).toBe(false);
    expect(reason).toContain("[0,1]");
  });
});

// ---------------------------------------------------------------------------
// run_match tool
// ---------------------------------------------------------------------------

describe("MCP run_match", () => {
  it("executes a single match and returns result", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    const result = scenario.executeMatch(
      { aggression: 0.6, defense: 0.4, path_bias: 0.5 },
      42,
    );
    expect(result.score).toBeGreaterThanOrEqual(0);
    expect(result.score).toBeLessThanOrEqual(1);
    expect(["challenger", "incumbent"]).toContain(result.winner);
  });
});

// ---------------------------------------------------------------------------
// run_tournament tool
// ---------------------------------------------------------------------------

describe("MCP run_tournament", () => {
  it("runs N matches and aggregates", async () => {
    const { TournamentRunner } = await import("../src/execution/tournament.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    const runner = new TournamentRunner(scenario, { matchCount: 3, seedBase: 1000 });
    const result = runner.run({ aggression: 0.6, defense: 0.4, path_bias: 0.5 });
    expect(result.matches).toHaveLength(3);
    expect(typeof result.meanScore).toBe("number");
    expect(typeof result.elo).toBe("number");
  });
});

// ---------------------------------------------------------------------------
// read_trajectory tool
// ---------------------------------------------------------------------------

describe("MCP read_trajectory", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("builds trajectory from store data", async () => {
    const { SQLiteStore } = await import("../src/storage/index.js");
    const { ScoreTrajectoryBuilder } = await import("../src/knowledge/trajectory.js");

    const store = new SQLiteStore(join(dir, "test.db"));
    store.migrate(join(__dirname, "..", "migrations"));
    store.createRun("run-1", "grid_ctf", 3, "local");
    store.upsertGeneration("run-1", 1, {
      meanScore: 0.5, bestScore: 0.55, elo: 1000,
      wins: 2, losses: 3, gateDecision: "retry", status: "completed",
    });
    const trajectory = store.getScoreTrajectory("run-1");
    const md = new ScoreTrajectoryBuilder(trajectory).build();
    expect(md).toContain("Score Trajectory");
    expect(md).toContain("0.5500");
    store.close();
  });
});

// ---------------------------------------------------------------------------
// read_hints tool
// ---------------------------------------------------------------------------

describe("MCP read_hints", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("reads hints from artifact store", async () => {
    const { ArtifactStore } = await import("../src/knowledge/artifact-store.js");
    const artifacts = new ArtifactStore({
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
    });
    // Write a playbook with hints section
    artifacts.writePlaybook("grid_ctf", [
      "<!-- PLAYBOOK_START -->",
      "Strategy content",
      "<!-- PLAYBOOK_END -->",
      "<!-- LESSONS_START -->",
      "Lesson 1",
      "<!-- LESSONS_END -->",
      "<!-- COMPETITOR_HINTS_START -->",
      "Try flanking maneuver",
      "<!-- COMPETITOR_HINTS_END -->",
    ].join("\n"));
    const content = artifacts.readPlaybook("grid_ctf");
    expect(content).toContain("Try flanking maneuver");
  });
});

// ---------------------------------------------------------------------------
// record_feedback / get_feedback tools
// ---------------------------------------------------------------------------

describe("MCP feedback tools", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("records and retrieves human feedback", async () => {
    const { SQLiteStore } = await import("../src/storage/index.js");
    const store = new SQLiteStore(join(dir, "test.db"));
    store.migrate(join(__dirname, "..", "migrations"));

    const id = store.insertHumanFeedback("grid_ctf", "test output", 0.85, "Good coverage");
    expect(id).toBeGreaterThan(0);

    const feedback = store.getHumanFeedback("grid_ctf", 5);
    expect(feedback).toHaveLength(1);
    expect(feedback[0].human_score).toBeCloseTo(0.85);
    expect(feedback[0].human_notes).toBe("Good coverage");
    store.close();
  });
});

// ---------------------------------------------------------------------------
// export_skill tool
// ---------------------------------------------------------------------------

describe("MCP export_skill", () => {
  it("exports skill package with markdown", async () => {
    const { SkillPackage } = await import("../src/knowledge/skill-package.js");
    const pkg = new SkillPackage({
      scenarioName: "grid_ctf",
      displayName: "Grid CTF",
      description: "Capture the flag.",
      playbook: "# Playbook",
      lessons: ["lesson 1"],
      bestStrategy: { aggression: 0.6 },
      bestScore: 0.85,
      bestElo: 1100,
      hints: "",
    });
    const dict = pkg.toDict();
    const md = pkg.toSkillMarkdown();
    expect(dict.scenario_name).toBe("grid_ctf");
    expect(md).toContain("Grid CTF");
    expect(md).toContain("Operational Lessons");
  });
});

// ---------------------------------------------------------------------------
// MCP server registration
// ---------------------------------------------------------------------------

describe("MCP server tool registration", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("registers all parity tools", async () => {
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

    // Access internal registered tools to verify count
    const registeredTools = (server as unknown as {
      _registeredTools: Record<string, unknown>;
    })._registeredTools;

    const toolNames = Object.keys(registeredTools);

    // Should have at least 18 tools (13 existing + 5 new)
    expect(toolNames.length).toBeGreaterThanOrEqual(18);

    // New parity tools should be registered
    expect(toolNames).toContain("validate_strategy");
    expect(toolNames).toContain("run_match");
    expect(toolNames).toContain("run_tournament");
    expect(toolNames).toContain("read_trajectory");
    expect(toolNames).toContain("record_feedback");
    expect(toolNames).toContain("get_feedback");
    expect(toolNames).toContain("export_skill");

    store.close();
  });
});
