/**
 * Tests for AC-365 (remaining): Full MCP package-surface parity.
 * Covers all tool families missing from the TS MCP server.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-mcp-full-"));
}

// ---------------------------------------------------------------------------
// MCP server tool registration — comprehensive count
// ---------------------------------------------------------------------------

describe("MCP server comprehensive tool registration", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("registers all parity tools (>= 25)", async () => {
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

    const registeredTools = (server as unknown as {
      _registeredTools: Record<string, unknown>;
    })._registeredTools;
    const toolNames = Object.keys(registeredTools);

    // Must have at least 25 tools for full parity
    expect(toolNames.length).toBeGreaterThanOrEqual(25);

    // Existing core tools
    expect(toolNames).toContain("evaluate_output");
    expect(toolNames).toContain("run_improvement_loop");
    expect(toolNames).toContain("list_scenarios");
    expect(toolNames).toContain("get_scenario");
    expect(toolNames).toContain("list_runs");
    expect(toolNames).toContain("get_run_status");
    expect(toolNames).toContain("get_playbook");
    expect(toolNames).toContain("run_scenario");
    expect(toolNames).toContain("get_generation_detail");

    // Scenario execution family
    expect(toolNames).toContain("validate_strategy");
    expect(toolNames).toContain("run_match");
    expect(toolNames).toContain("run_tournament");

    // Knowledge readers
    expect(toolNames).toContain("read_trajectory");
    expect(toolNames).toContain("read_hints");
    expect(toolNames).toContain("read_analysis");
    expect(toolNames).toContain("read_tools");
    expect(toolNames).toContain("read_skills");

    // Export/search
    expect(toolNames).toContain("export_skill");
    expect(toolNames).toContain("list_solved");
    expect(toolNames).toContain("search_strategies");

    // Feedback
    expect(toolNames).toContain("record_feedback");
    expect(toolNames).toContain("get_feedback");

    // Replay
    expect(toolNames).toContain("run_replay");

    store.close();
  });
});

// ---------------------------------------------------------------------------
// Knowledge reader helpers (backing the MCP tools)
// ---------------------------------------------------------------------------

describe("Knowledge readers for MCP", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("ArtifactStore reads hints from playbook", async () => {
    const { ArtifactStore } = await import("../src/knowledge/artifact-store.js");
    const artifacts = new ArtifactStore({
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
    });
    artifacts.writePlaybook("grid_ctf", [
      "<!-- PLAYBOOK_START -->",
      "Strategy here",
      "<!-- PLAYBOOK_END -->",
      "<!-- COMPETITOR_HINTS_START -->",
      "Try flanking.",
      "<!-- COMPETITOR_HINTS_END -->",
    ].join("\n"));
    const playbook = artifacts.readPlaybook("grid_ctf");
    expect(playbook).toContain("Try flanking");
  });

  it("ArtifactStore reads analysis for generation", async () => {
    const { ArtifactStore } = await import("../src/knowledge/artifact-store.js");
    const artifacts = new ArtifactStore({
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
    });
    const genDir = artifacts.generationDir("run-1", 1);
    artifacts.writeMarkdown(join(genDir, "analyst.md"), "## Findings\n- Strong center play.");
    // Read it back via the generation dir
    const { existsSync, readFileSync } = await import("node:fs");
    const path = join(genDir, "analyst.md");
    expect(existsSync(path)).toBe(true);
    expect(readFileSync(path, "utf-8")).toContain("Strong center play");
  });

  it("ScoreTrajectoryBuilder works for MCP read_trajectory", async () => {
    const { SQLiteStore } = await import("../src/storage/index.js");
    const { ScoreTrajectoryBuilder } = await import("../src/knowledge/trajectory.js");
    const store = new SQLiteStore(join(dir, "test.db"));
    store.migrate(join(__dirname, "..", "migrations"));
    store.createRun("run-1", "grid_ctf", 2, "local");
    store.upsertGeneration("run-1", 1, {
      meanScore: 0.50, bestScore: 0.55, elo: 1000,
      wins: 2, losses: 3, gateDecision: "retry", status: "completed",
    });
    const traj = store.getScoreTrajectory("run-1");
    const md = new ScoreTrajectoryBuilder(traj).build();
    expect(md).toContain("Score Trajectory");
    store.close();
  });

  it("SkillPackage exports for MCP export_skill", async () => {
    const { SkillPackage } = await import("../src/knowledge/skill-package.js");
    const pkg = new SkillPackage({
      scenarioName: "grid_ctf",
      displayName: "Grid CTF",
      description: "CTF game.",
      playbook: "# Playbook",
      lessons: ["lesson 1"],
      bestStrategy: { aggression: 0.6 },
      bestScore: 0.85,
      bestElo: 1100,
      hints: "",
    });
    expect(pkg.toDict().scenario_name).toBe("grid_ctf");
    expect(pkg.toSkillMarkdown()).toContain("Grid CTF");
  });
});
