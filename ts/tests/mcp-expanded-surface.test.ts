/**
 * Tests for the expanded TS MCP surface in this AC-365 slice.
 * These cover the tool families landed in this PR without claiming solve/sandbox parity.
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

type RegisteredToolServer = {
  _registeredTools: Record<
    string,
    {
      handler: (
        args: Record<string, unknown>,
        extra: unknown,
      ) => Promise<{ content: Array<{ text: string }> }>;
    }
  >;
};

async function createToolServer(dir: string): Promise<{
  dbPath: string;
  store: import("../src/storage/index.js").SQLiteStore;
  server: RegisteredToolServer;
}> {
  const { SQLiteStore } = await import("../src/storage/index.js");
  const { DeterministicProvider } = await import("../src/providers/deterministic.js");
  const { createMcpServer } = await import("../src/mcp/server.js");

  const dbPath = join(dir, "test.db");
  const store = new SQLiteStore(dbPath);
  store.migrate(join(__dirname, "..", "migrations"));
  const server = createMcpServer({
    store,
    provider: new DeterministicProvider(),
    dbPath,
    runsRoot: join(dir, "runs"),
    knowledgeRoot: join(dir, "knowledge"),
  }) as unknown as RegisteredToolServer;

  return { dbPath, store, server };
}

describe("Expanded MCP server tool registration", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("registers the expanded tool surface for this parity slice", async () => {
    const { store, server } = await createToolServer(dir);
    const registeredTools = server._registeredTools as Record<string, unknown>;
    const toolNames = Object.keys(registeredTools);

    // This slice meaningfully expands the MCP surface even though solve/sandbox
    // families are still tracked separately.
    expect(toolNames.length).toBeGreaterThanOrEqual(25);

    expect(toolNames).toContain("evaluate_output");
    expect(toolNames).toContain("run_improvement_loop");
    expect(toolNames).toContain("list_scenarios");
    expect(toolNames).toContain("get_scenario");
    expect(toolNames).toContain("list_runs");
    expect(toolNames).toContain("get_run_status");
    expect(toolNames).toContain("list_runtime_sessions");
    expect(toolNames).toContain("get_runtime_session");
    expect(toolNames).toContain("get_runtime_session_timeline");
    expect(toolNames).toContain("get_playbook");
    expect(toolNames).toContain("run_scenario");
    expect(toolNames).toContain("get_generation_detail");

    expect(toolNames).toContain("validate_strategy");
    expect(toolNames).toContain("run_match");
    expect(toolNames).toContain("run_tournament");

    expect(toolNames).toContain("read_trajectory");
    expect(toolNames).toContain("read_hints");
    expect(toolNames).toContain("read_analysis");
    expect(toolNames).toContain("read_tools");
    expect(toolNames).toContain("read_skills");

    expect(toolNames).toContain("export_skill");
    expect(toolNames).toContain("list_solved");
    expect(toolNames).toContain("search_strategies");

    expect(toolNames).toContain("record_feedback");
    expect(toolNames).toContain("get_feedback");
    expect(toolNames).toContain("run_replay");

    store.close();
  });
});

describe("Expanded MCP tool handlers", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("validate_strategy runs through the registered MCP handler", async () => {
    const { store, server } = await createToolServer(dir);

    const result = await server._registeredTools.validate_strategy.handler({
      scenario: "grid_ctf",
      strategy: JSON.stringify({
        aggression: 0.6,
        defense: 0.5,
        path_bias: 0.7,
      }),
    }, {});

    const payload = JSON.parse(result.content[0].text) as Record<string, unknown>;
    expect(payload.valid).toBe(true);
    expect(payload.reason).toBe("ok");

    store.close();
  });

  it("read_hints returns extracted hints through the registered MCP handler", async () => {
    const { ArtifactStore } = await import("../src/knowledge/artifact-store.js");
    const { store, server } = await createToolServer(dir);

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

    const result = await server._registeredTools.read_hints.handler({
      scenario: "grid_ctf",
    }, {});

    expect(result.content[0].text).toContain("Try flanking.");
    store.close();
  });

  it("export_skill returns persisted package data through the registered MCP handler", async () => {
    const { ArtifactStore } = await import("../src/knowledge/artifact-store.js");
    const { store, server } = await createToolServer(dir);

    const artifacts = new ArtifactStore({
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
    });
    artifacts.writePlaybook("grid_ctf", [
      "<!-- PLAYBOOK_START -->",
      "Hold the center lane.",
      "<!-- PLAYBOOK_END -->",
      "<!-- LESSONS_START -->",
      "- Keep center control",
      "<!-- LESSONS_END -->",
      "<!-- COMPETITOR_HINTS_START -->",
      "Try flanking.",
      "<!-- COMPETITOR_HINTS_END -->",
    ].join("\n"));

    store.createRun("run-1", "grid_ctf", 1, "local");
    store.upsertGeneration("run-1", 1, {
      meanScore: 0.88,
      bestScore: 0.91,
      elo: 1110,
      wins: 4,
      losses: 1,
      gateDecision: "advance",
      status: "completed",
    });
    store.recordMatch("run-1", 1, {
      seed: 42,
      score: 0.91,
      passedValidation: true,
      validationErrors: "",
      winner: "challenger",
      strategyJson: JSON.stringify({ aggression: 0.6, defense: 0.5, path_bias: 0.7 }),
    });
    store.updateRunStatus("run-1", "completed");

    const result = await server._registeredTools.export_skill.handler({
      scenario: "grid_ctf",
    }, {});

    const payload = JSON.parse(result.content[0].text) as Record<string, unknown>;
    expect(payload.best_score).toBe(0.91);
    expect(payload.best_elo).toBe(1110);
    expect(payload.best_strategy).toEqual({ aggression: 0.6, defense: 0.5, path_bias: 0.7 });
    expect(payload.hints).toBe("Try flanking.");
    expect(payload.lessons).toEqual(["Keep center control"]);
    expect(payload.suggested_filename).toBe("grid-ctf-knowledge.md");
    expect((payload.skill_markdown as string)).toContain("Best Known Strategy");

    store.close();
  });

  it("record_feedback and get_feedback work through the registered MCP handlers", async () => {
    const { store, server } = await createToolServer(dir);

    const inserted = await server._registeredTools.record_feedback.handler({
      scenario: "grid_ctf",
      agentOutput: "{\"aggression\":0.6}",
      score: 0.8,
      notes: "Strong opening.",
    }, {});
    const insertedPayload = JSON.parse(inserted.content[0].text) as Record<string, unknown>;
    expect(typeof insertedPayload.feedbackId).toBe("number");

    const fetched = await server._registeredTools.get_feedback.handler({
      scenario: "grid_ctf",
      limit: 5,
    }, {});
    const fetchedPayload = JSON.parse(fetched.content[0].text) as Array<Record<string, unknown>>;
    expect(fetchedPayload).toHaveLength(1);
    expect(fetchedPayload[0]?.human_notes).toBe("Strong opening.");

    store.close();
  });

  it("run_replay returns the persisted replay artifact through the registered MCP handler", async () => {
    const { store, server } = await createToolServer(dir);
    const { ArtifactStore } = await import("../src/knowledge/artifact-store.js");
    const artifacts = new ArtifactStore({
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
    });
    const replayDir = join(artifacts.generationDir("run-1", 1), "replays");
    mkdirSync(replayDir, { recursive: true });
    writeFileSync(
      join(replayDir, "grid_ctf_1.json"),
      JSON.stringify({
        scenario: "grid_ctf",
        narrative: "Blue team secured the center route.",
        timeline: [{ turn: 1, action: "advance" }],
      }, null, 2),
      "utf-8",
    );

    const result = await server._registeredTools.run_replay.handler({
      runId: "run-1",
      generation: 1,
    }, {});

    const payload = JSON.parse(result.content[0].text) as Record<string, unknown>;
    expect(payload.scenario).toBe("grid_ctf");
    expect(payload.narrative).toBe("Blue team secured the center route.");
    expect(payload.timeline).toEqual([{ turn: 1, action: "advance" }]);

    store.close();
  });

  it("runtime-session tools return persisted run-scoped event logs", async () => {
    const { dbPath, store, server } = await createToolServer(dir);
    const {
      RuntimeSessionEventLog,
      RuntimeSessionEventStore,
      RuntimeSessionEventType,
    } = await import("../src/session/runtime-events.js");

    const eventStore = new RuntimeSessionEventStore(dbPath);
    const log = RuntimeSessionEventLog.create({
      sessionId: "run:run-1:runtime",
      metadata: { goal: "autoctx run grid_ctf", runId: "run-1" },
    });
    log.append(RuntimeSessionEventType.PROMPT_SUBMITTED, {
      role: "architect",
      prompt: "Improve the strategy",
    });
    eventStore.save(log);
    eventStore.close();

    const listed = await server._registeredTools.list_runtime_sessions.handler({
      limit: 5,
    }, {});
    const listedPayload = JSON.parse(listed.content[0].text) as Record<string, unknown>;
    expect(listedPayload.sessions).toEqual([
      expect.objectContaining({
        session_id: "run:run-1:runtime",
        goal: "autoctx run grid_ctf",
        event_count: 1,
      }),
    ]);

    const shown = await server._registeredTools.get_runtime_session.handler({
      runId: "run-1",
    }, {});
    const shownPayload = JSON.parse(shown.content[0].text) as Record<string, unknown>;
    expect(shownPayload.sessionId).toBe("run:run-1:runtime");
    expect(shownPayload.events).toEqual([
      expect.objectContaining({
        eventType: RuntimeSessionEventType.PROMPT_SUBMITTED,
        payload: {
          role: "architect",
          prompt: "Improve the strategy",
        },
      }),
    ]);

    const timeline = await server._registeredTools.get_runtime_session_timeline.handler({
      runId: "run-1",
    }, {});
    const timelinePayload = JSON.parse(timeline.content[0].text) as Record<string, unknown>;
    expect(timelinePayload.items).toEqual([
      expect.objectContaining({
        kind: "prompt",
        status: "in_flight",
        prompt_preview: "Improve the strategy",
      }),
    ]);

    store.close();
  });
});
