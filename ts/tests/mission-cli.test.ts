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

// ---------------------------------------------------------------------------
// CLI: autoctx mission --help
// ---------------------------------------------------------------------------

describe("autoctx mission --help", () => {
  it("shows mission subcommands", () => {
    const { stdout, exitCode } = runCli(["mission", "--help"]);
    expect(exitCode).toBe(0);
    expect(stdout).toContain("create");
    expect(stdout).toContain("status");
    expect(stdout).toContain("list");
    expect(stdout).toContain("pause");
    expect(stdout).toContain("resume");
    expect(stdout).toContain("cancel");
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
  });

  it("filters by status", () => {
    const { stdout: r1 } = runCli(["mission", "create", "--name", "A", "--goal", "g1"], { cwd: dir });
    runCli(["mission", "create", "--name", "B", "--goal", "g2"], { cwd: dir });
    const { id } = JSON.parse(r1);
    runCli(["mission", "pause", "--id", id], { cwd: dir });

    const { stdout } = runCli(["mission", "list", "--status", "active"], { cwd: dir });
    const parsed = JSON.parse(stdout);
    expect(parsed.length).toBe(1);
    expect(parsed[0].name).toBe("B");
  });
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
});

// ---------------------------------------------------------------------------
// MCP: mission tools registered
// ---------------------------------------------------------------------------

describe("MCP mission tools", () => {
  it("server registers mission tools", async () => {
    const { MISSION_TOOLS } = await import("../src/mcp/mission-tools.js");
    expect(MISSION_TOOLS.length).toBeGreaterThanOrEqual(5);
    const names = MISSION_TOOLS.map((t) => t.name);
    expect(names).toContain("create_mission");
    expect(names).toContain("mission_status");
    expect(names).toContain("mission_list");
    expect(names).toContain("pause_mission");
    expect(names).toContain("cancel_mission");
  });

  it("create_mission tool has required parameters", async () => {
    const { MISSION_TOOLS } = await import("../src/mcp/mission-tools.js");
    const create = MISSION_TOOLS.find((t) => t.name === "create_mission")!;
    expect(create.schema.properties.name).toBeDefined();
    expect(create.schema.properties.goal).toBeDefined();
  });
});
