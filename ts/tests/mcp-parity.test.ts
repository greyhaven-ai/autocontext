/**
 * Tests for AC-365: MCP server package-surface parity.
 *
 * These exercise the registered MCP tool handlers directly so we cover
 * the actual shipped server surface rather than only the underlying helpers.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";
import { SQLiteStore } from "../src/storage/index.js";
import { DeterministicProvider } from "../src/providers/deterministic.js";
import { createMcpServer } from "../src/mcp/server.js";
import { ArtifactStore } from "../src/knowledge/artifact-store.js";
import { HarnessStore } from "../src/knowledge/harness-store.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-mcp-parity-"));
}

type HandlerResult = Promise<{ content: Array<{ text: string }> }>;
type RegisteredTool = { handler: (args: Record<string, unknown>, extra: unknown) => HandlerResult };
type ToolServer = { _registeredTools: Record<string, RegisteredTool> };

function createToolServer(dir: string): { store: SQLiteStore; server: ToolServer; runsRoot: string; knowledgeRoot: string } {
  const store = new SQLiteStore(join(dir, "test.db"));
  store.migrate(join(__dirname, "..", "migrations"));
  const runsRoot = join(dir, "runs");
  const knowledgeRoot = join(dir, "knowledge");
  const server = createMcpServer({
    store,
    provider: new DeterministicProvider(),
    runsRoot,
    knowledgeRoot,
  }) as unknown as ToolServer;
  return { store, server, runsRoot, knowledgeRoot };
}

// ---------------------------------------------------------------------------
// validate_strategy / run_match / run_tournament
// ---------------------------------------------------------------------------

describe("MCP strategy execution tools", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("validate_strategy runs through the registered MCP handler", async () => {
    const { store, server } = createToolServer(dir);
    try {
      const result = await server._registeredTools.validate_strategy.handler({
        scenario: "grid_ctf",
        strategy: JSON.stringify({ aggression: 0.6, defense: 0.4, path_bias: 0.5 }),
      }, {});
      const payload = JSON.parse(result.content[0].text);
      expect(payload.valid).toBe(true);
      expect(payload.reason).toBe("ok");
    } finally {
      store.close();
    }
  });

  it("run_match executes through the registered MCP handler", async () => {
    const { store, server } = createToolServer(dir);
    try {
      const result = await server._registeredTools.run_match.handler({
        scenario: "grid_ctf",
        strategy: JSON.stringify({ aggression: 0.6, defense: 0.4, path_bias: 0.5 }),
        seed: 42,
      }, {});
      const payload = JSON.parse(result.content[0].text);
      expect(payload.score).toBeGreaterThanOrEqual(0);
      expect(payload.score).toBeLessThanOrEqual(1);
      expect(["challenger", "incumbent"]).toContain(payload.winner);
    } finally {
      store.close();
    }
  });

  it("run_tournament aggregates through the registered MCP handler", async () => {
    const { store, server } = createToolServer(dir);
    try {
      const result = await server._registeredTools.run_tournament.handler({
        scenario: "grid_ctf",
        strategy: JSON.stringify({ aggression: 0.6, defense: 0.4, path_bias: 0.5 }),
        matches: 3,
        seedBase: 1000,
      }, {});
      const payload = JSON.parse(result.content[0].text);
      expect(typeof payload.meanScore).toBe("number");
      expect(typeof payload.bestScore).toBe("number");
      expect(typeof payload.elo).toBe("number");
      expect(payload.wins + payload.losses).toBe(3);
    } finally {
      store.close();
    }
  });
});

// ---------------------------------------------------------------------------
// read_trajectory
// ---------------------------------------------------------------------------

describe("MCP read_trajectory", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("renders a trajectory from persisted run data via the MCP handler", async () => {
    const { store, server } = createToolServer(dir);
    try {
      store.createRun("run-1", "grid_ctf", 3, "local");
      store.upsertGeneration("run-1", 1, {
        meanScore: 0.5,
        bestScore: 0.55,
        elo: 1000,
        wins: 2,
        losses: 3,
        gateDecision: "retry",
        status: "completed",
      });

      const result = await server._registeredTools.read_trajectory.handler({ runId: "run-1" }, {});
      expect(result.content[0].text).toContain("Score Trajectory");
      expect(result.content[0].text).toContain("0.5500");
    } finally {
      store.close();
    }
  });
});

// ---------------------------------------------------------------------------
// record_feedback / get_feedback
// ---------------------------------------------------------------------------

describe("MCP feedback tools", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("records and retrieves human feedback through the MCP handlers", async () => {
    const { store, server } = createToolServer(dir);
    try {
      const record = await server._registeredTools.record_feedback.handler({
        scenario: "grid_ctf",
        agentOutput: "test output",
        score: 0.85,
        notes: "Good coverage",
      }, {});
      const recorded = JSON.parse(record.content[0].text);
      expect(recorded.feedbackId).toBeGreaterThan(0);

      const result = await server._registeredTools.get_feedback.handler({
        scenario: "grid_ctf",
        limit: 5,
      }, {});
      const payload = JSON.parse(result.content[0].text);
      expect(payload).toHaveLength(1);
      expect(payload[0].human_score).toBeCloseTo(0.85);
      expect(payload[0].human_notes).toBe("Good coverage");
    } finally {
      store.close();
    }
  });
});

// ---------------------------------------------------------------------------
// export_skill
// ---------------------------------------------------------------------------

describe("MCP export_skill", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("exports the real persisted package surface via the MCP handler", async () => {
    const { store, server, runsRoot, knowledgeRoot } = createToolServer(dir);
    try {
      store.createRun("run-1", "grid_ctf", 1, "local", "deterministic");
      store.upsertGeneration("run-1", 1, {
        meanScore: 0.71,
        bestScore: 0.83,
        elo: 1112.5,
        wins: 2,
        losses: 1,
        gateDecision: "advance",
        status: "completed",
      });
      store.recordMatch("run-1", 1, {
        seed: 1000,
        score: 0.83,
        passedValidation: true,
        validationErrors: "",
        winner: "challenger",
        strategyJson: JSON.stringify({ aggression: 0.8, flank_bias: 0.4 }),
        replayJson: JSON.stringify([{ turn: 1, lane: "center" }]),
      });
      store.updateRunStatus("run-1", "completed");

      const artifacts = new ArtifactStore({ runsRoot, knowledgeRoot });
      artifacts.writePlaybook(
        "grid_ctf",
        [
          "<!-- PLAYBOOK_START -->",
          "## Strategy Updates",
          "",
          "- Pressure center first.",
          "<!-- PLAYBOOK_END -->",
          "",
          "<!-- LESSONS_START -->",
          "- Stable wins came from balanced pressure.",
          "<!-- LESSONS_END -->",
          "",
          "<!-- COMPETITOR_HINTS_START -->",
          "- Keep defender coverage above 0.5.",
          "<!-- COMPETITOR_HINTS_END -->",
        ].join("\n"),
      );
      const harnessStore = new HarnessStore(knowledgeRoot, "grid_ctf");
      harnessStore.writeVersioned("validator", "def validate():\n    return True\n", 1);

      const result = await server._registeredTools.export_skill.handler({
        scenario: "grid_ctf",
      }, {});
      const payload = JSON.parse(result.content[0].text);

      expect(payload.best_score).toBeCloseTo(0.83);
      expect(payload.best_elo).toBeCloseTo(1112.5);
      expect(payload.best_strategy).toEqual({ aggression: 0.8, flank_bias: 0.4 });
      expect(payload.lessons).toEqual(["Stable wins came from balanced pressure."]);
      expect(payload.hints).toContain("Keep defender coverage above 0.5.");
      expect(payload.harness.validator).toContain("def validate()");
      expect(payload.skill_markdown).toContain("## Best Known Strategy");
      expect(payload.suggested_filename).toBe("grid-ctf-knowledge.md");
    } finally {
      store.close();
    }
  });
});

// ---------------------------------------------------------------------------
// MCP server registration
// ---------------------------------------------------------------------------

describe("MCP server tool registration", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("registers all parity tools", () => {
    const { store, server } = createToolServer(dir);
    try {
      const toolNames = Object.keys(server._registeredTools);
      expect(toolNames.length).toBeGreaterThanOrEqual(18);
      expect(toolNames).toContain("validate_strategy");
      expect(toolNames).toContain("run_match");
      expect(toolNames).toContain("run_tournament");
      expect(toolNames).toContain("read_trajectory");
      expect(toolNames).toContain("record_feedback");
      expect(toolNames).toContain("get_feedback");
      expect(toolNames).toContain("export_skill");
    } finally {
      store.close();
    }
  });
});
