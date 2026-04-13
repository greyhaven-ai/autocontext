/**
 * Tests for AC-413: Mission CLI and MCP control plane.
 *
 * - CLI: autoctx mission create/status/list/pause/resume/cancel
 * - MCP: mission tools exposed via server
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");
const MIGRATIONS_DIR = join(import.meta.dirname, "..", "migrations");

const SANITIZED_KEYS = [
  "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AUTOCONTEXT_API_KEY",
  "AUTOCONTEXT_AGENT_API_KEY", "AUTOCONTEXT_PROVIDER", "AUTOCONTEXT_AGENT_PROVIDER",
  "AUTOCONTEXT_DB_PATH", "AUTOCONTEXT_RUNS_ROOT", "AUTOCONTEXT_KNOWLEDGE_ROOT",
  "AUTOCONTEXT_CONFIG_DIR", "AUTOCONTEXT_AGENT_DEFAULT_MODEL", "AUTOCONTEXT_MODEL",
];

function buildEnv(overrides: Record<string, string> = {}): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env, NODE_NO_WARNINGS: "1" };
  for (const k of SANITIZED_KEYS) delete env[k];
  return { ...env, ...overrides };
}

function runCli(
  args: string[],
  opts: { cwd?: string; env?: Record<string, string> } = {},
): { stdout: string; stderr: string; exitCode: number } {
  const r = spawnSync("npx", ["tsx", CLI, ...args], {
    encoding: "utf8",
    timeout: 15000,
    cwd: opts.cwd,
    env: buildEnv(opts.env),
  });
  return { stdout: r.stdout ?? "", stderr: r.stderr ?? "", exitCode: r.status ?? 1 };
}

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-mission-cli-"));
}

function setupProjectDir(): string {
  const dir = makeTempDir();
  mkdirSync(join(dir, "runs"), { recursive: true });
  mkdirSync(join(dir, "knowledge"), { recursive: true });
  writeFileSync(join(dir, ".autoctx.json"), JSON.stringify({
    default_scenario: "grid_ctf",
    provider: "deterministic",
    gens: 1,
    runs_dir: "./runs",
    knowledge_dir: "./knowledge",
  }, null, 2), "utf-8");
  return dir;
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

async function createMissionToolServer(dir: string): Promise<{
  store: import("../src/storage/index.js").SQLiteStore;
  server: RegisteredToolServer;
}> {
  const { SQLiteStore } = await import("../src/storage/index.js");
  const { DeterministicProvider } = await import("../src/providers/deterministic.js");
  const { createMcpServer } = await import("../src/mcp/server.js");

  const dbPath = join(dir, "test.db");
  const store = new SQLiteStore(dbPath);
  store.migrate(MIGRATIONS_DIR);
  const server = createMcpServer({
    store,
    provider: new DeterministicProvider(),
    dbPath,
    runsRoot: join(dir, "runs"),
    knowledgeRoot: join(dir, "knowledge"),
  }) as unknown as RegisteredToolServer;

  return { store, server };
}

// ---------------------------------------------------------------------------
// CLI: autoctx mission --help
// ---------------------------------------------------------------------------

describe("autoctx mission --help", () => {
  it("shows mission subcommands", () => {
    const { stdout, exitCode } = runCli(["mission", "--help"]);
    expect(exitCode).toBe(0);
    expect(stdout).toContain("create");
    expect(stdout).toContain("run");
    expect(stdout).toContain("status");
    expect(stdout).toContain("list");
    expect(stdout).toContain("pause");
    expect(stdout).toContain("resume");
    expect(stdout).toContain("cancel");
    expect(stdout).toContain("artifacts");
  });
});

// ---------------------------------------------------------------------------
// CLI: mission create + status
// ---------------------------------------------------------------------------

describe("autoctx mission create", () => {
  let dir: string;
  beforeEach(() => { dir = setupProjectDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("creates a mission and returns its ID", () => {
    const { stdout, exitCode } = runCli(
      ["mission", "create", "--name", "Ship login", "--goal", "Implement OAuth"],
      { cwd: dir },
    );
    expect(exitCode).toBe(0);
    const parsed = JSON.parse(stdout);
    expect(parsed.id).toMatch(/^mission-/);
    expect(parsed.status).toBe("active");
  });

  it("mission status returns mission details", () => {
    const createResult = runCli(
      ["mission", "create", "--name", "Test", "--goal", "Do thing"],
      { cwd: dir },
    );
    const { id } = JSON.parse(createResult.stdout);

    const { stdout, exitCode } = runCli(["mission", "status", "--id", id], { cwd: dir });
    expect(exitCode).toBe(0);
    const parsed = JSON.parse(stdout);
    expect(parsed.name).toBe("Test");
    expect(parsed.status).toBe("active");
  });
});

// ---------------------------------------------------------------------------
// CLI: mission list
// ---------------------------------------------------------------------------

describe("autoctx mission list", () => {
  let dir: string;
  beforeEach(() => { dir = setupProjectDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("lists all missions as JSON", () => {
    runCli(["mission", "create", "--name", "A", "--goal", "g1"], { cwd: dir });
    runCli(["mission", "create", "--name", "B", "--goal", "g2"], { cwd: dir });

    const { stdout, exitCode } = runCli(["mission", "list"], { cwd: dir });
    expect(exitCode).toBe(0);
    const parsed = JSON.parse(stdout);
    expect(parsed.length).toBe(2);
  }, 15000);

  it("filters by status", () => {
    const { stdout: r1 } = runCli(["mission", "create", "--name", "A", "--goal", "g1"], { cwd: dir });
    runCli(["mission", "create", "--name", "B", "--goal", "g2"], { cwd: dir });
    const { id } = JSON.parse(r1);
    runCli(["mission", "pause", "--id", id], { cwd: dir });

    const { stdout } = runCli(["mission", "list", "--status", "active"], { cwd: dir });
    const parsed = JSON.parse(stdout);
    expect(parsed.length).toBe(1);
    expect(parsed[0].name).toBe("B");
  }, 15000);
});

// ---------------------------------------------------------------------------
// CLI: mission pause/resume/cancel
// ---------------------------------------------------------------------------

describe("autoctx mission lifecycle", () => {
  let dir: string;
  beforeEach(() => { dir = setupProjectDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("pause sets status to paused", () => {
    const { stdout: created } = runCli(["mission", "create", "--name", "T", "--goal", "g"], { cwd: dir });
    const { id } = JSON.parse(created);

    const { exitCode } = runCli(["mission", "pause", "--id", id], { cwd: dir });
    expect(exitCode).toBe(0);

    const { stdout } = runCli(["mission", "status", "--id", id], { cwd: dir });
    expect(JSON.parse(stdout).status).toBe("paused");
  });

  it("resume sets status back to active", () => {
    const { stdout: created } = runCli(["mission", "create", "--name", "T", "--goal", "g"], { cwd: dir });
    const { id } = JSON.parse(created);

    runCli(["mission", "pause", "--id", id], { cwd: dir });
    runCli(["mission", "resume", "--id", id], { cwd: dir });

    const { stdout } = runCli(["mission", "status", "--id", id], { cwd: dir });
    expect(JSON.parse(stdout).status).toBe("active");
  });

  it("cancel sets status to canceled", () => {
    const { stdout: created } = runCli(["mission", "create", "--name", "T", "--goal", "g"], { cwd: dir });
    const { id } = JSON.parse(created);

    runCli(["mission", "cancel", "--id", id], { cwd: dir });

    const { stdout } = runCli(["mission", "status", "--id", id], { cwd: dir });
    expect(JSON.parse(stdout).status).toBe("canceled");
  });

  it("returns an error for nonexistent mission IDs", () => {
    const { stderr, exitCode } = runCli(["mission", "pause", "--id", "mission-does-not-exist"], { cwd: dir });
    expect(exitCode).toBe(1);
    expect(stderr).toContain("Mission not found: mission-does-not-exist");
  });
});

describe("autoctx mission run and artifacts", () => {
  let dir: string;
  beforeEach(() => { dir = setupProjectDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("run uses adaptive planning for generic missions and artifacts exposes persisted checkpoints", () => {
    const { stdout: created } = runCli(
      ["mission", "create", "--name", "T", "--goal", "Ship OAuth"],
      { cwd: dir },
    );
    const { id } = JSON.parse(created);

    const { stdout: runOut, exitCode: runExit } = runCli(
      ["mission", "run", "--id", id, "--max-iterations", "1"],
      { cwd: dir },
    );
    expect(runExit).toBe(0);
    const runPayload = JSON.parse(runOut);
    expect(runPayload.id).toBe(id);
    expect(runPayload.stepsExecuted).toBe(1);
    expect(runPayload.planGenerated).toBe(true);
    expect(runPayload.finalStatus).toBe("completed");
    expect(runPayload.checkpointPath).toContain(`/missions/${id}/checkpoints/`);

    const { stdout: statusOut } = runCli(["mission", "status", "--id", id], { cwd: dir });
    const statusPayload = JSON.parse(statusOut);
    expect(statusPayload.stepsCount).toBe(1);
    expect(statusPayload.subgoalCount).toBeGreaterThanOrEqual(1);
    expect(statusPayload.latestVerification.reason).toContain("All subgoals completed");

    const { stdout: artifactsOut, exitCode: artifactsExit } = runCli(
      ["mission", "artifacts", "--id", id],
      { cwd: dir },
    );
    expect(artifactsExit).toBe(0);
    const artifactsPayload = JSON.parse(artifactsOut);
    expect(artifactsPayload.checkpoints.length).toBeGreaterThanOrEqual(2);
    expect(artifactsPayload.latestCheckpoint.mission.id).toBe(id);
  });
});

// ---------------------------------------------------------------------------
// MCP: mission tools registered and runnable
// ---------------------------------------------------------------------------

describe("MCP mission tools", () => {
  let dir: string;
  beforeEach(() => { dir = setupProjectDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("registers mission tools on the real MCP server", async () => {
    const { store, server } = await createMissionToolServer(dir);
    const names = Object.keys(server._registeredTools);
    expect(names).toContain("create_mission");
    expect(names).toContain("mission_status");
    expect(names).toContain("mission_result");
    expect(names).toContain("mission_artifacts");
    expect(names).toContain("pause_mission");
    expect(names).toContain("resume_mission");
    expect(names).toContain("cancel_mission");
    store.close();
  });

  it("mission tool handlers operate against the shared mission store", async () => {
    const { store, server } = await createMissionToolServer(dir);

    const created = JSON.parse((await server._registeredTools.create_mission.handler({
      name: "Ship login",
      goal: "Implement OAuth",
      max_steps: 3,
    }, {})).content[0].text);
    expect(created.id).toMatch(/^mission-/);

    const missionId = created.id as string;
    const status = JSON.parse((await server._registeredTools.mission_status.handler({
      mission_id: missionId,
    }, {})).content[0].text);
    expect(status.status).toBe("active");

    const artifacts = JSON.parse((await server._registeredTools.mission_artifacts.handler({
      mission_id: missionId,
    }, {})).content[0].text);
    expect(artifacts.checkpoints.length).toBe(1);
    expect(artifacts.latestCheckpoint.mission.id).toBe(missionId);

    const paused = JSON.parse((await server._registeredTools.pause_mission.handler({
      mission_id: missionId,
    }, {})).content[0].text);
    expect(paused.status).toBe("paused");

    const resumed = JSON.parse((await server._registeredTools.resume_mission.handler({
      mission_id: missionId,
    }, {})).content[0].text);
    expect(resumed.status).toBe("active");

    const result = JSON.parse((await server._registeredTools.mission_result.handler({
      mission_id: missionId,
    }, {})).content[0].text);
    expect(result.mission.id).toBe(missionId);
    expect(Array.isArray(result.steps)).toBe(true);

    const canceled = JSON.parse((await server._registeredTools.cancel_mission.handler({
      mission_id: missionId,
    }, {})).content[0].text);
    expect(canceled.status).toBe("canceled");

    store.close();
  });
});
