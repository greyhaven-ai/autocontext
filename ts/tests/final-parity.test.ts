/**
 * Tests for AC-370: remaining TS package parity — solve flows, sandbox,
 * agent task CRUD, package management, capabilities, and exclusion docs.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-final-parity-"));
}

type ToolResult = { content: Array<{ text: string }> };

type RegisteredToolServer = {
  _registeredTools: Record<
    string,
    {
      handler: (
        args: Record<string, unknown>,
        extra: unknown,
      ) => Promise<ToolResult>;
    }
  >;
};

async function createToolServer(dir: string): Promise<{
  store: import("../src/storage/index.js").SQLiteStore;
  server: RegisteredToolServer;
}> {
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
  }) as unknown as RegisteredToolServer;

  return { store, server };
}

async function waitForSolveTerminalState(
  server: RegisteredToolServer,
  jobId: string,
): Promise<Record<string, unknown>> {
  for (let attempt = 0; attempt < 100; attempt++) {
    const result = await server._registeredTools.solve_status.handler({ jobId }, {});
    const payload = JSON.parse(result.content[0].text) as Record<string, unknown>;
    const status = String(payload.status ?? "");
    if (status === "completed" || status === "failed" || status === "not_found") {
      return payload;
    }
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error(`Timed out waiting for solve job ${jobId}`);
}

describe("MCP final tool count", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("registers >= 35 tools for package-surface parity", async () => {
    const { store, server } = await createToolServer(dir);
    const names = Object.keys(server._registeredTools);

    expect(names.length).toBeGreaterThanOrEqual(35);

    expect(names).toContain("solve_scenario");
    expect(names).toContain("solve_status");
    expect(names).toContain("solve_result");

    expect(names).toContain("sandbox_create");
    expect(names).toContain("sandbox_run");
    expect(names).toContain("sandbox_status");
    expect(names).toContain("sandbox_playbook");
    expect(names).toContain("sandbox_list");
    expect(names).toContain("sandbox_destroy");

    expect(names).toContain("create_agent_task");
    expect(names).toContain("list_agent_tasks");
    expect(names).toContain("get_agent_task");

    expect(names).toContain("export_package");
    expect(names).toContain("import_package");
    expect(names).toContain("capabilities");
    expect(names).toContain("generate_output");

    store.close();
  });
});

describe("Solve flow", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("solve tools share state and return the completed package export", async () => {
    const { store, server } = await createToolServer(dir);

    const submitted = await server._registeredTools.solve_scenario.handler({
      description: "grid ctf",
      generations: 1,
    }, {});
    const submittedPayload = JSON.parse(submitted.content[0].text) as Record<string, unknown>;
    const jobId = String(submittedPayload.jobId);

    const status = await waitForSolveTerminalState(server, jobId);
    expect(status.status).toBe("completed");
    expect(status.scenarioName).toBe("grid_ctf");

    const result = await server._registeredTools.solve_result.handler({ jobId }, {});
    const payload = JSON.parse(result.content[0].text) as Record<string, unknown>;
    expect(payload.scenario_name).toBe("grid_ctf");
    expect(payload.skill_markdown).toBeTypeOf("string");

    store.close();
  });

  it("routes generated scenarios through family-aware execution (AC-436)", async () => {
    const { store, server } = await createToolServer(dir);

    const submitted = await server._registeredTools.solve_scenario.handler({
      description: "Investigate the root cause of a production outage using evidence logs",
      generations: 1,
    }, {});
    const submittedPayload = JSON.parse(submitted.content[0].text) as Record<string, unknown>;
    const jobId = String(submittedPayload.jobId);

    const status = await waitForSolveTerminalState(server, jobId);
    // With the codegen pipeline, non-game scenarios should complete and
    // still return the exported skill-package contract from solve_result.
    expect(status.scenarioName).not.toBe("grid_ctf");
    expect(status.status).toBe("completed");

    const result = await server._registeredTools.solve_result.handler({ jobId }, {});
    const payload = JSON.parse(result.content[0].text) as Record<string, unknown>;
    expect(payload.scenario_name).toBe(status.scenarioName);
    expect(payload.skill_markdown).toBeTypeOf("string");
    expect(payload.best_score).toBeTypeOf("number");
    expect((payload.metadata as Record<string, unknown>).family).toBe(status.family);

    store.close();
  });
});

describe("Sandbox lifecycle", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("sandbox tools share lifecycle state across MCP calls", async () => {
    const { store, server } = await createToolServer(dir);

    const created = await server._registeredTools.sandbox_create.handler({
      scenario: "grid_ctf",
      userId: "test-user",
    }, {});
    const createdPayload = JSON.parse(created.content[0].text) as Record<string, unknown>;
    const sandboxId = String(createdPayload.sandboxId);
    expect(createdPayload.scenarioName).toBe("grid_ctf");

    const status = await server._registeredTools.sandbox_status.handler({ sandboxId }, {});
    const statusPayload = JSON.parse(status.content[0].text) as Record<string, unknown>;
    expect(statusPayload.userId).toBe("test-user");
    expect(statusPayload.status).toBe("active");

    const listed = await server._registeredTools.sandbox_list.handler({}, {});
    const listedPayload = JSON.parse(listed.content[0].text) as Array<Record<string, unknown>>;
    expect(listedPayload).toHaveLength(1);
    expect(listedPayload[0]?.sandboxId).toBe(sandboxId);

    const run = await server._registeredTools.sandbox_run.handler({
      sandboxId,
      generations: 1,
    }, {});
    const runPayload = JSON.parse(run.content[0].text) as Record<string, unknown>;
    expect(runPayload.runId).toBeTypeOf("string");
    expect(runPayload.bestScore).toBeTypeOf("number");

    const playbook = await server._registeredTools.sandbox_playbook.handler({ sandboxId }, {});
    expect(playbook.content[0].text).toContain("Strategy Updates");

    const destroyed = await server._registeredTools.sandbox_destroy.handler({ sandboxId }, {});
    const destroyedPayload = JSON.parse(destroyed.content[0].text) as Record<string, unknown>;
    expect(destroyedPayload.destroyed).toBe(true);

    const listedAfterDestroy = await server._registeredTools.sandbox_list.handler({}, {});
    expect(JSON.parse(listedAfterDestroy.content[0].text)).toEqual([]);

    store.close();
  });
});

describe("Agent task CRUD", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("AgentTaskStore creates, lists, gets, and deletes tasks", async () => {
    const { AgentTaskStore } = await import("../src/scenarios/agent-task-store.js");

    const taskStore = new AgentTaskStore(join(dir, "tasks"));

    taskStore.create({
      name: "test-task",
      taskPrompt: "Summarize this document.",
      rubric: "Evaluate completeness.",
    });

    const list = taskStore.list();
    expect(list.length).toBe(1);
    expect(list[0].name).toBe("test-task");

    const task = taskStore.get("test-task");
    expect(task).not.toBeNull();
    expect(task!.taskPrompt).toBe("Summarize this document.");

    taskStore.delete("test-task");
    expect(taskStore.list().length).toBe(0);
  });
});

describe("Capabilities discovery", () => {
  it("returns capability metadata", async () => {
    const { getCapabilities } = await import("../src/mcp/capabilities.js");
    const caps = getCapabilities();
    const pkg = JSON.parse(readFileSync(join(import.meta.dirname, "..", "package.json"), "utf-8"));
    expect(caps.scenarios).toBeDefined();
    expect(caps.providers).toBeDefined();
    expect(caps.concept_model).toBeDefined();
    expect(caps.concept_model.user_facing.some((entry) => entry.name === "Scenario")).toBe(true);
    expect(caps.version).toBe(pkg.version);
    expect(Array.isArray(caps.scenarios)).toBe(true);
    expect(caps.scenarios.length).toBeGreaterThan(0);
  });
});
