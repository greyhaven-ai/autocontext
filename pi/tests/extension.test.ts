/**
 * Tests for AC-427: Official Pi package/extension for autocontext.
 *
 * Validates:
 * - Extension entry point registers expected tools
 * - Tool handlers execute correctly with mock Pi API
 * - Package manifest has correct Pi configuration
 * - SKILL.md has valid frontmatter
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// ---------------------------------------------------------------------------
// Mock Pi ExtensionAPI
// ---------------------------------------------------------------------------

interface RegisteredTool {
  name: string;
  label: string;
  description: string;
  parameters: unknown;
  execute: (...args: unknown[]) => Promise<unknown>;
}

interface RegisteredCommand {
  name: string;
  handler: (...args: unknown[]) => Promise<unknown>;
}

function createMockPiAPI() {
  const tools: RegisteredTool[] = [];
  const commands: RegisteredCommand[] = [];
  const events: Map<string, Array<(...args: unknown[]) => void>> = new Map();

  return {
    tools,
    commands,
    events,

    registerTool(def: RegisteredTool) {
      tools.push(def);
    },

    registerCommand(name: string, opts: { handler: (...args: unknown[]) => Promise<unknown> }) {
      commands.push({ name, handler: opts.handler });
    },

    on(event: string, handler: (...args: unknown[]) => void) {
      const handlers = events.get(event) ?? [];
      handlers.push(handler);
      events.set(event, handlers);
    },
  };
}

type MockState = {
  providerConfig: { providerType: string; apiKey?: string; model?: string; baseUrl?: string };
  settings: { dbPath: string; runsRoot: string; knowledgeRoot: string; eventStreamPath: string };
  runs: Array<{ id?: string; run_id?: string; scenario?: string; status: string }>;
  generations: Array<{ run_id: string; generation_index: number; best_score: number; mean_score: number }>;
  trajectory: Array<{ generation_index: number; best_score: number; delta: number }>;
  agentOutputs: Array<{ run_id: string; generation_index: number; role: string; content: string }>;
  matches: Array<{ run_id: string; generation_index: number; seed: number; score: number }>;
  hubPackages: Array<{ package_id: string; scenario_name: string; source_run_id: string; metadata: Record<string, unknown> }>;
  hubResults: Array<{ result_id: string; run_id: string; package_id: string | null }>;
  hubPromotions: Array<{ event_id: string; package_id: string; source_run_id: string; action: string }>;
  sessions: Array<{
    sessionId: string;
    goal: string;
    status: string;
    activeBranchId: string;
    turnCount: number;
    totalTokens: number;
    branches: Array<{ branchId: string; label: string; parentTurnId: string; summary: string }>;
    turns: Array<{ turnId: string; branchId: string; parentTurnId: string; role: string }>;
    events: Array<{ eventId: string; eventType: string }>;
    branchPath: (branchId?: string) => Array<{ turnId: string }>;
  }>;
  providerOpts: Record<string, unknown> | null;
  storeDbPath: string | null;
  sessionStoreDbPath: string | null;
  simpleTaskArgs: unknown[] | null;
  loopOpts: Record<string, unknown> | null;
  loopInput: Record<string, unknown> | null;
  enqueueArgs: { specName: string; opts?: Record<string, unknown> } | null;
};

let mockState: MockState;

function resetMockState(): void {
  mockState = {
    providerConfig: {
      providerType: "openai",
      apiKey: "test-key",
      model: "gpt-4o-mini",
      baseUrl: "https://example.test/v1",
    },
    settings: {
      dbPath: "runs/autocontext.sqlite3",
      runsRoot: "runs",
      knowledgeRoot: "knowledge",
      eventStreamPath: "runs/events.ndjson",
    },
    runs: [{ id: "run-1", run_id: "run-1", scenario: "grid_ctf", status: "completed" }],
    generations: [
      { run_id: "run-1", generation_index: 0, best_score: 0.72, mean_score: 0.61 },
      { run_id: "run-1", generation_index: 1, best_score: 0.88, mean_score: 0.74 },
    ],
    trajectory: [
      { generation_index: 0, best_score: 0.72, delta: 0 },
      { generation_index: 1, best_score: 0.88, delta: 0.16 },
    ],
    agentOutputs: [
      { run_id: "run-1", generation_index: 1, role: "competitor", content: "candidate strategy body" },
    ],
    matches: [
      { run_id: "run-1", generation_index: 1, seed: 42, score: 0.88 },
    ],
    hubPackages: [
      { package_id: "pkg-1", scenario_name: "grid_ctf", source_run_id: "run-1", metadata: { promotion: "candidate" } },
    ],
    hubResults: [
      { result_id: "result-1", run_id: "run-1", package_id: "pkg-1" },
    ],
    hubPromotions: [
      { event_id: "promo-1", package_id: "pkg-1", source_run_id: "run-1", action: "promote" },
    ],
    sessions: [
      {
        sessionId: "sess-1",
        goal: "Explore strategy branches",
        status: "active",
        activeBranchId: "alt",
        turnCount: 2,
        totalTokens: 33,
        branches: [
          { branchId: "main", label: "Main", parentTurnId: "", summary: "" },
          { branchId: "alt", label: "Alternate", parentTurnId: "t1", summary: "try alternate route" },
        ],
        turns: [
          { turnId: "t1", branchId: "main", parentTurnId: "", role: "competitor" },
          { turnId: "t2", branchId: "alt", parentTurnId: "t1", role: "analyst" },
        ],
        events: [{ eventId: "e1", eventType: "branch_created" }],
        branchPath: (branchId?: string) => (branchId === "alt" ? [{ turnId: "t1" }, { turnId: "t2" }] : [{ turnId: "t1" }]),
      },
    ],
    providerOpts: null,
    storeDbPath: null,
    sessionStoreDbPath: null,
    simpleTaskArgs: null,
    loopOpts: null,
    loopInput: null,
    enqueueArgs: null,
  };
}

function installAutoctxMock(): void {
  vi.doMock("autoctx", () => {
    class SQLiteStore {
      constructor(dbPath: string) {
        mockState.storeDbPath = dbPath;
      }

      listRuns() {
        return mockState.runs;
      }

      getRun(runId: string) {
        return mockState.runs.find((run) => run.id === runId || run.run_id === runId) ?? null;
      }

      getGenerations(runId: string) {
        return mockState.generations.filter((generation) => generation.run_id === runId);
      }

      getScoreTrajectory(runId: string) {
        return mockState.trajectory.filter((entry) => mockState.runs.some((run) => (run.run_id ?? run.id) === runId));
      }

      getAgentOutputs(runId: string, generationIndex: number) {
        return mockState.agentOutputs.filter((output) => output.run_id === runId && output.generation_index === generationIndex);
      }

      getMatchesForRun(runId: string) {
        return mockState.matches.filter((match) => match.run_id === runId);
      }

      listHubPackageRecords() {
        return mockState.hubPackages;
      }

      listHubResultRecords() {
        return mockState.hubResults;
      }

      listHubPromotionRecords() {
        return mockState.hubPromotions;
      }

      close() {
        // no-op in tests
      }
    }

    class SessionStore {
      constructor(dbPath: string) {
        mockState.sessionStoreDbPath = dbPath;
      }

      load(sessionId: string) {
        return mockState.sessions.find((session) => session.sessionId === sessionId) ?? null;
      }

      list(_status?: string, limit = 50) {
        return mockState.sessions.slice(0, limit);
      }

      close() {
        // no-op in tests
      }
    }

    class SimpleAgentTask {
      constructor(...args: unknown[]) {
        mockState.simpleTaskArgs = args;
      }
    }

    class ImprovementLoop {
      constructor(opts: Record<string, unknown>) {
        mockState.loopOpts = opts;
      }

      async run(input: Record<string, unknown>) {
        mockState.loopInput = input;
        return {
          bestScore: 0.93,
          rounds: [{ roundNumber: 1 }, { roundNumber: 2 }],
          bestOutput: "improved output",
        };
      }
    }

    class LLMJudge {
      async evaluate() {
        return {
          score: 0.8,
          reasoning: "Looks good",
          dimensionScores: { quality: 0.8 },
        };
      }
    }

    return {
      loadSettings: () => mockState.settings,
      resolveProviderConfig: () => mockState.providerConfig,
      createProvider: (opts: Record<string, unknown>) => {
        mockState.providerOpts = opts;
        return {
          name: String(opts.providerType ?? "mock"),
          defaultModel: () => String(opts.model ?? "mock-model"),
        };
      },
      LLMJudge,
      SimpleAgentTask,
      ImprovementLoop,
      SQLiteStore,
      SessionStore,
      enqueueTask: (
        _store: unknown,
        specName: string,
        opts?: Record<string, unknown>,
      ) => {
        mockState.enqueueArgs = { specName, opts };
      },
      SCENARIO_REGISTRY: {
        grid_ctf: { family: "simulation" },
        writing_task: { family: "agent_task" },
      },
    };
  });
}

async function loadExtension() {
  const mod = await import("../src/index.js");
  const api = createMockPiAPI();
  mod.default(api as unknown);
  return api;
}

// ---------------------------------------------------------------------------
// Package manifest
// ---------------------------------------------------------------------------

describe("Package manifest", () => {
  const pkgPath = join(import.meta.dirname, "..", "package.json");

  it("has pi-package keyword", () => {
    const pkg = JSON.parse(readFileSync(pkgPath, "utf-8"));
    expect(pkg.keywords).toContain("pi-package");
  });

  it("has pi.extensions pointing to entry point", () => {
    const pkg = JSON.parse(readFileSync(pkgPath, "utf-8"));
    expect(pkg.pi).toBeDefined();
    expect(pkg.pi.extensions).toContain("./src/index.ts");
  });

  it("has pi.skills pointing to skills dir", () => {
    const pkg = JSON.parse(readFileSync(pkgPath, "utf-8"));
    expect(pkg.pi.skills).toContain("./skills");
  });

  it("lists Pi core packages as peerDependencies", () => {
    const pkg = JSON.parse(readFileSync(pkgPath, "utf-8"));
    expect(pkg.peerDependencies["@mariozechner/pi-coding-agent"]).toBe("*");
    expect(pkg.peerDependencies["@mariozechner/pi-ai"]).toBe("*");
  });

  it("depends on the current autoctx toolkit line", () => {
    const pkg = JSON.parse(readFileSync(pkgPath, "utf-8"));
    expect(pkg.dependencies.autoctx).toBe("^0.4.7");
  });
});

beforeEach(() => {
  vi.resetModules();
  vi.doUnmock("autoctx");
  resetMockState();
  installAutoctxMock();
});

// ---------------------------------------------------------------------------
// SKILL.md
// ---------------------------------------------------------------------------

describe("SKILL.md", () => {
  const skillPath = join(import.meta.dirname, "..", "skills", "autocontext", "SKILL.md");

  it("exists at skills/autocontext/SKILL.md", () => {
    expect(existsSync(skillPath)).toBe(true);
  });

  it("has valid frontmatter with required fields", () => {
    const content = readFileSync(skillPath, "utf-8");
    expect(content).toMatch(/^---\n/);
    expect(content).toMatch(/name:\s*autocontext/);
    expect(content).toMatch(/description:/);
  });

  it("skill name matches directory name", () => {
    const content = readFileSync(skillPath, "utf-8");
    const nameMatch = content.match(/name:\s*(\S+)/);
    expect(nameMatch).not.toBeNull();
    expect(nameMatch![1]).toBe("autocontext");
  });

  it("has allowed-tools for pre-approval", () => {
    const content = readFileSync(skillPath, "utf-8");
    expect(content).toMatch(/allowed-tools:/);
    expect(content).toContain("autocontext_judge");
    expect(content).toContain("autocontext_improve");
    expect(content).toContain("autocontext_status");
    expect(content).toContain("autocontext_runtime_snapshot");
  });
});

// ---------------------------------------------------------------------------
// Prompt templates
// ---------------------------------------------------------------------------

describe("Prompt templates", () => {
  const promptsDir = join(import.meta.dirname, "..", "prompts");

  it("has a status prompt template", () => {
    expect(existsSync(join(promptsDir, "autoctx-status.md"))).toBe(true);
  });

  it("status prompt references autoctx tools", () => {
    const content = readFileSync(join(promptsDir, "autoctx-status.md"), "utf-8");
    expect(content).toContain("autocontext");
  });

  it("has a judge prompt template", () => {
    expect(existsSync(join(promptsDir, "autoctx-judge.md"))).toBe(true);
    const content = readFileSync(join(promptsDir, "autoctx-judge.md"), "utf-8");
    expect(content).toMatch(/^---/);
    expect(content).toContain("autocontext_judge");
  });

  it("has an improve prompt template", () => {
    expect(existsSync(join(promptsDir, "autoctx-improve.md"))).toBe(true);
    const content = readFileSync(join(promptsDir, "autoctx-improve.md"), "utf-8");
    expect(content).toMatch(/^---/);
    expect(content).toContain("autocontext_improve");
  });
});

// ---------------------------------------------------------------------------
// Extension entry point
// ---------------------------------------------------------------------------

describe("Extension entry point", () => {
  it("exports a default function", async () => {
    const mod = await import("../src/index.js");
    expect(typeof mod.default).toBe("function");
  });

  it("registers autocontext tools when called", async () => {
    const api = await loadExtension();
    expect(api.tools.length).toBeGreaterThanOrEqual(4);
  });

  it("registers autocontext_judge tool", async () => {
    const api = await loadExtension();
    const judge = api.tools.find((t) => t.name === "autocontext_judge");
    expect(judge).toBeDefined();
    expect(judge!.description.toLowerCase()).toContain("evaluat");
  });

  it("registers autocontext_improve tool", async () => {
    const api = await loadExtension();
    const improve = api.tools.find((t) => t.name === "autocontext_improve");
    expect(improve).toBeDefined();
  });

  it("registers autocontext_status tool", async () => {
    const api = await loadExtension();
    const status = api.tools.find((t) => t.name === "autocontext_status");
    expect(status).toBeDefined();
  });

  it("registers autocontext_scenarios tool", async () => {
    const api = await loadExtension();
    const scenarios = api.tools.find((t) => t.name === "autocontext_scenarios");
    expect(scenarios).toBeDefined();
  });

  it("registers autocontext_queue tool", async () => {
    const api = await loadExtension();
    const queue = api.tools.find((t) => t.name === "autocontext_queue");
    expect(queue).toBeDefined();
  });

  it("registers autocontext_runtime_snapshot tool", async () => {
    const api = await loadExtension();
    const runtime = api.tools.find((t) => t.name === "autocontext_runtime_snapshot");
    expect(runtime).toBeDefined();
  });

  it("registers /autocontext slash command", async () => {
    const api = await loadExtension();
    const cmd = api.commands.find((c) => c.name === "autocontext");
    expect(cmd).toBeDefined();
  });

  it("subscribes to session_start event", async () => {
    const api = await loadExtension();
    expect(api.events.has("session_start")).toBe(true);
  });

  it("all tools have promptGuidelines", async () => {
    const api = await loadExtension();
    for (const tool of api.tools) {
      expect((tool as any).promptGuidelines, `${tool.name} missing promptGuidelines`).toBeDefined();
      expect((tool as any).promptGuidelines.length).toBeGreaterThanOrEqual(1);
    }
  });

  it("all tools have renderCall", async () => {
    const api = await loadExtension();
    for (const tool of api.tools) {
      expect((tool as any).renderCall, `${tool.name} missing renderCall`).toBeDefined();
    }
  });

  it("tool errors throw instead of returning ok()", async () => {
    // status tool should throw when no store is available
    vi.doUnmock("autoctx");
    vi.doMock("autoctx", () => ({
      loadSettings: () => ({}),
      resolveProviderConfig: () => ({ providerType: "anthropic" }),
      createProvider: () => ({ defaultModel: () => "test" }),
      SQLiteStore: class { constructor() { throw new Error("no db"); } },
    }));
    const mod = await import("../src/index.js");
    const api = createMockPiAPI();
    mod.default(api as unknown);
    const status = api.tools.find((t) => t.name === "autocontext_status")!;
    await expect(status.execute("c1", {}, undefined, undefined, undefined)).rejects.toThrow();
  });
});

// ---------------------------------------------------------------------------
// Tool parameter schemas
// ---------------------------------------------------------------------------

describe("Tool parameter schemas", () => {
  it("autocontext_judge has task_prompt, agent_output, rubric params", async () => {
    const api = await loadExtension();
    const judge = api.tools.find((t) => t.name === "autocontext_judge")!;
    const schema = judge.parameters as Record<string, unknown>;
    const props = (schema as { properties?: Record<string, unknown> }).properties;
    expect(props).toBeDefined();
    expect(props!.task_prompt).toBeDefined();
    expect(props!.agent_output).toBeDefined();
    expect(props!.rubric).toBeDefined();
  });

  it("autocontext_improve has task_prompt, initial_output, rubric params", async () => {
    const api = await loadExtension();
    const improve = api.tools.find((t) => t.name === "autocontext_improve")!;
    const schema = improve.parameters as Record<string, unknown>;
    const props = (schema as { properties?: Record<string, unknown> }).properties;
    expect(props).toBeDefined();
    expect(props!.task_prompt).toBeDefined();
    expect(props!.initial_output).toBeDefined();
    expect(props!.rubric).toBeDefined();
  });

  it("autocontext_queue has spec_name param", async () => {
    const api = await loadExtension();
    const queue = api.tools.find((t) => t.name === "autocontext_queue")!;
    const schema = queue.parameters as Record<string, unknown>;
    const props = (schema as { properties?: Record<string, unknown> }).properties;
    expect(props).toBeDefined();
    expect(props!.spec_name).toBeDefined();
  });

  it("autocontext_runtime_snapshot has run and session selectors", async () => {
    const api = await loadExtension();
    const runtime = api.tools.find((t) => t.name === "autocontext_runtime_snapshot")!;
    const schema = runtime.parameters as Record<string, unknown>;
    const props = (schema as { properties?: Record<string, unknown> }).properties;
    expect(props).toBeDefined();
    expect(props!.run_id).toBeDefined();
    expect(props!.session_id).toBeDefined();
    expect(props!.include_outputs).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tool execution paths
// ---------------------------------------------------------------------------

describe("Tool execution", () => {
  it("autocontext_improve uses autoctx provider resolution and runnable task APIs", async () => {
    const api = await loadExtension();
    const improve = api.tools.find((t) => t.name === "autocontext_improve");
    expect(improve).toBeDefined();

    const result = await improve!.execute("call-1", {
      task_prompt: "Write a concise summary",
      initial_output: "Draft summary",
      rubric: "Reward clarity and correctness",
      max_rounds: 4,
      quality_threshold: 0.95,
    });

    expect(result).toEqual(expect.objectContaining({
      content: expect.arrayContaining([
        expect.objectContaining({
          text: expect.stringContaining("Improvement complete."),
        }),
      ]),
    }));
    expect(mockState.providerOpts).toEqual(expect.objectContaining({
      providerType: "openai",
      apiKey: "test-key",
      model: "gpt-4o-mini",
      baseUrl: "https://example.test/v1",
    }));
    expect(mockState.simpleTaskArgs).toEqual([
      "Write a concise summary",
      "Reward clarity and correctness",
      expect.objectContaining({
        defaultModel: expect.any(Function),
      }),
      "gpt-4o-mini",
    ]);
    expect(mockState.loopOpts).toEqual({
      task: expect.any(Object),
      maxRounds: 4,
      qualityThreshold: 0.95,
    });
    expect(mockState.loopInput).toEqual({
      initialOutput: "Draft summary",
      state: {},
    });
  });

  it("autocontext_status uses the configured autoctx db path", async () => {
    mockState.settings.dbPath = "/workspace/runs/autocontext.sqlite3";
    const api = await loadExtension();
    const status = api.tools.find((t) => t.name === "autocontext_status");
    expect(status).toBeDefined();

    const result = await status!.execute("call-2", {});

    expect(mockState.storeDbPath).toBe("/workspace/runs/autocontext.sqlite3");
    expect(result).toEqual(expect.objectContaining({
      content: expect.arrayContaining([
        expect.objectContaining({
          text: expect.stringContaining("1 run(s) found."),
        }),
      ]),
    }));
  });

  it("autocontext_queue forwards task overrides to autoctx enqueueTask", async () => {
    const api = await loadExtension();
    const queue = api.tools.find((t) => t.name === "autocontext_queue");
    expect(queue).toBeDefined();

    await queue!.execute("call-3", {
      spec_name: "writing_task",
      task_prompt: "Draft a release note",
      rubric: "Score factual accuracy",
      priority: 5,
    });

    expect(mockState.enqueueArgs).toEqual({
      specName: "writing_task",
      opts: {
        taskPrompt: "Draft a release note",
        rubric: "Score factual accuracy",
        priority: 5,
      },
    });
  });

  it("autocontext_runtime_snapshot returns run artifacts, package records, session lineage, and recent events", async () => {
    const eventDir = mkdtempSync(join(tmpdir(), "autoctx-pi-events-"));
    mockState.settings.dbPath = "/workspace/runs/autocontext.sqlite3";
    mockState.settings.eventStreamPath = join(eventDir, "events.ndjson");
    writeFileSync(
      mockState.settings.eventStreamPath,
      [
        JSON.stringify({ event: "run_started", payload: { run_id: "run-1" } }),
        JSON.stringify({ event: "generation_completed", payload: { run_id: "run-1", generation_index: 1 } }),
        JSON.stringify({ event: "run_started", payload: { run_id: "other" } }),
      ].join("\n") + "\n",
      "utf-8",
    );
    const api = await loadExtension();
    const runtime = api.tools.find((t) => t.name === "autocontext_runtime_snapshot");
    expect(runtime).toBeDefined();

    const result = await runtime!.execute("call-4", {
      run_id: "run-1",
      session_id: "sess-1",
      include_outputs: true,
      generation_index: 1,
      limit: 5,
    }) as {
      content: Array<{ type: "text"; text: string }>;
      details: Record<string, unknown>;
    };

    expect(mockState.storeDbPath).toBe("/workspace/runs/autocontext.sqlite3");
    expect(mockState.sessionStoreDbPath).toBe("/workspace/runs/autocontext.sqlite3");
    expect(result.content[0].text).toContain("run-1");
    expect(result.content[0].text).toContain("sess-1");
    expect(result.details.run).toEqual(expect.objectContaining({ run_id: "run-1" }));
    expect(result.details.generations).toHaveLength(2);
    expect(result.details.agentOutputs).toEqual([
      expect.objectContaining({ role: "competitor", preview: "candidate strategy body" }),
    ]);
    expect((result.details.agentOutputs as Array<Record<string, unknown>>)[0].content).toBeUndefined();
    expect(result.details.packages).toEqual([
      expect.objectContaining({ package_id: "pkg-1", source_run_id: "run-1" }),
    ]);
    expect(result.details.session).toEqual(expect.objectContaining({
      sessionId: "sess-1",
      activeBranchId: "alt",
      branches: [
        expect.objectContaining({ branchId: "main", pathTurnIds: ["t1"] }),
        expect.objectContaining({ branchId: "alt", pathTurnIds: ["t1", "t2"] }),
      ],
    }));
    expect(result.details.events).toEqual([
      expect.objectContaining({ event: "run_started" }),
      expect.objectContaining({ event: "generation_completed" }),
    ]);
  });
});
