/**
 * Tests for AC-415: CodeMission MVP with hard external verifiers.
 *
 * - CommandVerifier: runs shell command, parses exit code
 * - CompositeVerifier: all verifiers must pass
 * - CodeMissionSpec: extends MissionSpec with code-specific fields
 * - createCodeMission: factory wiring verifiers to mission
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");
const MIGRATIONS_DIR = join(import.meta.dirname, "..", "migrations");

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-codemission-"));
}

const SANITIZED_KEYS = [
  "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AUTOCONTEXT_API_KEY",
  "AUTOCONTEXT_AGENT_API_KEY", "AUTOCONTEXT_PROVIDER", "AUTOCONTEXT_AGENT_PROVIDER",
  "AUTOCONTEXT_DB_PATH", "AUTOCONTEXT_RUNS_ROOT", "AUTOCONTEXT_KNOWLEDGE_ROOT",
  "AUTOCONTEXT_CONFIG_DIR", "AUTOCONTEXT_AGENT_DEFAULT_MODEL", "AUTOCONTEXT_MODEL",
];

function buildEnv(overrides: Record<string, string> = {}): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env, NODE_NO_WARNINGS: "1" };
  for (const key of SANITIZED_KEYS) delete env[key];
  return { ...env, ...overrides };
}

function runCli(
  args: string[],
  opts: { cwd?: string; env?: Record<string, string> } = {},
): { stdout: string; stderr: string; exitCode: number } {
  const result = spawnSync("npx", ["tsx", CLI, ...args], {
    encoding: "utf8",
    timeout: 15000,
    cwd: opts.cwd,
    env: buildEnv(opts.env),
  });
  return {
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    exitCode: result.status ?? 1,
  };
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
// CommandVerifier
// ---------------------------------------------------------------------------

describe("CommandVerifier", () => {
  let dir: string;
  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("passes when command exits 0", async () => {
    const { CommandVerifier } = await import("../src/mission/verifiers.js");
    const verifier = new CommandVerifier("true", dir);
    const result = await verifier.verify("m-1");
    expect(result.passed).toBe(true);
  });

  it("fails when command exits non-zero", async () => {
    const { CommandVerifier } = await import("../src/mission/verifiers.js");
    const verifier = new CommandVerifier("false", dir);
    const result = await verifier.verify("m-1");
    expect(result.passed).toBe(false);
    expect(result.reason).toContain("exit");
  });

  it("captures stdout in metadata", async () => {
    const { CommandVerifier } = await import("../src/mission/verifiers.js");
    const verifier = new CommandVerifier("echo hello-world", dir);
    const result = await verifier.verify("m-1");
    expect(result.passed).toBe(true);
    expect(result.metadata?.stdout).toContain("hello-world");
  });

  it("runs in the specified working directory", async () => {
    const { CommandVerifier } = await import("../src/mission/verifiers.js");
    const verifier = new CommandVerifier("pwd", dir);
    const result = await verifier.verify("m-1");
    expect(result.metadata?.stdout).toContain(dir);
  });

  it("has a descriptive label", async () => {
    const { CommandVerifier } = await import("../src/mission/verifiers.js");
    const verifier = new CommandVerifier("npm test", dir);
    expect(verifier.label).toBe("npm test");
  });
});

// ---------------------------------------------------------------------------
// CompositeVerifier
// ---------------------------------------------------------------------------

describe("CompositeVerifier", () => {
  let dir: string;
  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("passes when all verifiers pass", async () => {
    const { CommandVerifier, CompositeVerifier } = await import("../src/mission/verifiers.js");
    const composite = new CompositeVerifier([
      new CommandVerifier("true", dir),
      new CommandVerifier("echo ok", dir),
    ]);
    const result = await composite.verify("m-1");
    expect(result.passed).toBe(true);
  });

  it("fails when any verifier fails", async () => {
    const { CommandVerifier, CompositeVerifier } = await import("../src/mission/verifiers.js");
    const composite = new CompositeVerifier([
      new CommandVerifier("true", dir),
      new CommandVerifier("false", dir),
    ]);
    const result = await composite.verify("m-1");
    expect(result.passed).toBe(false);
    expect(result.reason).toContain("false");
  });

  it("reports which verifier failed", async () => {
    const { CommandVerifier, CompositeVerifier } = await import("../src/mission/verifiers.js");
    const composite = new CompositeVerifier([
      new CommandVerifier("true", dir),
      new CommandVerifier("false", dir),
    ]);
    const result = await composite.verify("m-1");
    expect(result.metadata?.failedVerifier).toBe("false");
  });

  it("stops at first failure (short-circuit)", async () => {
    const { CommandVerifier, CompositeVerifier } = await import("../src/mission/verifiers.js");
    let secondCalled = false;
    const composite = new CompositeVerifier([
      new CommandVerifier("false", dir),
      {
        label: "should-not-run",
        verify: async () => { secondCalled = true; return { passed: true, reason: "ok" }; },
      },
    ]);
    await composite.verify("m-1");
    expect(secondCalled).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// CodeMissionSpec
// ---------------------------------------------------------------------------

describe("CodeMissionSpec", () => {
  it("CodeMissionSpecSchema validates code mission config", async () => {
    const { CodeMissionSpecSchema } = await import("../src/mission/verifiers.js");
    const spec = CodeMissionSpecSchema.parse({
      name: "Fix login bug",
      goal: "Tests pass and lint clean",
      repoPath: "/path/to/repo",
      testCommand: "npm test",
      lintCommand: "npm run lint",
    });
    expect(spec.repoPath).toBe("/path/to/repo");
    expect(spec.testCommand).toBe("npm test");
  });

  it("CodeMissionSpecSchema works with minimal fields", async () => {
    const { CodeMissionSpecSchema } = await import("../src/mission/verifiers.js");
    const spec = CodeMissionSpecSchema.parse({
      name: "Quick fix",
      goal: "Fix the bug",
      repoPath: ".",
      testCommand: "npm test",
    });
    expect(spec.lintCommand).toBeUndefined();
    expect(spec.buildCommand).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// createCodeMission — factory
// ---------------------------------------------------------------------------

describe("createCodeMission", () => {
  let dir: string;
  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("creates a mission with verifiers wired up", async () => {
    const { createCodeMission } = await import("../src/mission/verifiers.js");
    const { MissionManager } = await import("../src/mission/manager.js");
    const manager = new MissionManager(join(dir, "test.db"));

    const id = createCodeMission(manager, {
      name: "Fix bug",
      goal: "Tests pass",
      repoPath: dir,
      testCommand: "true",
    });

    expect(manager.get(id)!.status).toBe("active");
    expect(manager.hasVerifier(id)).toBe(true);

    // Verify passes since "true" exits 0
    const result = await manager.verify(id);
    expect(result.passed).toBe(true);
    manager.close();
  });

  it("wires composite verifier when multiple commands provided", async () => {
    const { createCodeMission } = await import("../src/mission/verifiers.js");
    const { MissionManager } = await import("../src/mission/manager.js");
    const manager = new MissionManager(join(dir, "test.db"));

    const id = createCodeMission(manager, {
      name: "Fix bug",
      goal: "Tests + lint pass",
      repoPath: dir,
      testCommand: "true",
      lintCommand: "true",
    });

    const result = await manager.verify(id);
    expect(result.passed).toBe(true);
    manager.close();
  });

  it("composite fails when test command fails", async () => {
    const { createCodeMission } = await import("../src/mission/verifiers.js");
    const { MissionManager } = await import("../src/mission/manager.js");
    const manager = new MissionManager(join(dir, "test.db"));

    const id = createCodeMission(manager, {
      name: "Fix bug",
      goal: "Tests pass",
      repoPath: dir,
      testCommand: "false",
      lintCommand: "true",
    });

    const result = await manager.verify(id);
    expect(result.passed).toBe(false);
    manager.close();
  });

  it("sets budget from spec", async () => {
    const { createCodeMission } = await import("../src/mission/verifiers.js");
    const { MissionManager } = await import("../src/mission/manager.js");
    const manager = new MissionManager(join(dir, "test.db"));

    const id = createCodeMission(manager, {
      name: "Fix bug",
      goal: "Tests pass",
      repoPath: dir,
      testCommand: "true",
      budget: { maxSteps: 20 },
    });

    const usage = manager.budgetUsage(id);
    expect(usage.maxSteps).toBe(20);
    manager.close();
  });

  it("CLI can create and run a code mission with honest failed status and checkpoint artifacts", () => {
    const projectDir = setupProjectDir();
    try {
      const created = runCli([
        "mission", "create",
        "--type", "code",
        "--name", "Fix bug",
        "--goal", "Tests pass",
        "--repo-path", projectDir,
        "--test-command", "false",
      ], { cwd: projectDir });
      expect(created.exitCode).toBe(0);

      const createdPayload = JSON.parse(created.stdout);
      expect(createdPayload.metadata.missionType).toBe("code");
      expect(createdPayload.metadata.repoPath).toBe(projectDir);
      expect(createdPayload.metadata.testCommand).toBe("false");

      const missionId = createdPayload.id as string;
      const run = runCli(["mission", "run", "--id", missionId], { cwd: projectDir });
      expect(run.exitCode).toBe(0);

      const runPayload = JSON.parse(run.stdout);
      expect(runPayload.finalStatus).toBe("failed");
      expect(runPayload.verifierPassed).toBe(false);
      expect(runPayload.latestVerification.reason).toContain("failed (exit 1)");

      const status = JSON.parse(runCli(["mission", "status", "--id", missionId], { cwd: projectDir }).stdout);
      expect(status.status).toBe("failed");

      const artifacts = JSON.parse(runCli(["mission", "artifacts", "--id", missionId], { cwd: projectDir }).stdout);
      expect(artifacts.latestCheckpoint.mission.metadata.missionType).toBe("code");
      expect(artifacts.latestCheckpoint.mission.status).toBe("failed");
      expect(artifacts.latestCheckpoint.verifications[0].metadata.exitCode).toBe(1);
    } finally {
      rmSync(projectDir, { recursive: true, force: true });
    }
  }, 15000);

  it("MCP create_mission accepts code mission parameters and persists verifier config", async () => {
    const dir = setupProjectDir();
    try {
      const { store, server } = await createMissionToolServer(dir);
      const created = JSON.parse((await server._registeredTools.create_mission.handler({
        type: "code",
        name: "Fix login",
        goal: "Tests pass",
        repo_path: dir,
        test_command: "true",
        lint_command: "true",
      }, {})).content[0].text);

      expect(created.metadata.missionType).toBe("code");
      expect(created.metadata.repoPath).toBe(dir);
      expect(created.metadata.testCommand).toBe("true");
      expect(created.metadata.lintCommand).toBe("true");
      store.close();
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
