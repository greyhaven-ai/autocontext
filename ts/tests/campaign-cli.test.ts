/**
 * Tests for AC-533: Campaign CLI subcommands.
 *
 * CLI: autoctx campaign create/status/list/add-mission/progress/pause/resume/cancel
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");

const SANITIZED_KEYS = [
  "ANTHROPIC_API_KEY",
  "OPENAI_API_KEY",
  "AUTOCONTEXT_API_KEY",
  "AUTOCONTEXT_AGENT_API_KEY",
  "AUTOCONTEXT_PROVIDER",
  "AUTOCONTEXT_AGENT_PROVIDER",
  "AUTOCONTEXT_DB_PATH",
  "AUTOCONTEXT_RUNS_ROOT",
  "AUTOCONTEXT_KNOWLEDGE_ROOT",
  "AUTOCONTEXT_CONFIG_DIR",
  "AUTOCONTEXT_AGENT_DEFAULT_MODEL",
  "AUTOCONTEXT_MODEL",
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
  return {
    stdout: r.stdout ?? "",
    stderr: r.stderr ?? "",
    exitCode: r.status ?? 1,
  };
}

function setupProjectDir(): string {
  const dir = mkdtempSync(join(tmpdir(), "ac-campaign-cli-"));
  mkdirSync(join(dir, "runs"), { recursive: true });
  mkdirSync(join(dir, "knowledge"), { recursive: true });
  writeFileSync(
    join(dir, ".autoctx.json"),
    JSON.stringify(
      {
        default_scenario: "grid_ctf",
        provider: "deterministic",
        gens: 1,
        runs_dir: "./runs",
        knowledge_dir: "./knowledge",
      },
      null,
      2,
    ),
    "utf-8",
  );
  return dir;
}

// ---------------------------------------------------------------------------
// CLI: autoctx campaign --help
// ---------------------------------------------------------------------------

describe("autoctx campaign --help", () => {
  it("shows campaign subcommands", () => {
    const { stdout, exitCode } = runCli(["campaign", "--help"]);
    expect(exitCode).toBe(0);
    expect(stdout).toContain("create");
    expect(stdout).toContain("status");
    expect(stdout).toContain("list");
    expect(stdout).toContain("add-mission");
    expect(stdout).toContain("progress");
    expect(stdout).toContain("pause");
    expect(stdout).toContain("resume");
    expect(stdout).toContain("cancel");
  });
});

// ---------------------------------------------------------------------------
// CLI: campaign create + status
// ---------------------------------------------------------------------------

describe("autoctx campaign create", () => {
  let dir: string;
  beforeEach(() => {
    dir = setupProjectDir();
  });
  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("creates a campaign and returns its ID", () => {
    const { stdout, exitCode } = runCli(
      [
        "campaign",
        "create",
        "--name",
        "Q2 Goals",
        "--goal",
        "Ship OAuth and billing",
      ],
      { cwd: dir },
    );
    expect(exitCode).toBe(0);
    const parsed = JSON.parse(stdout);
    expect(parsed.id).toBeTruthy();
    expect(parsed.name).toBe("Q2 Goals");
    expect(parsed.status).toBe("active");
  });

  it("creates a campaign with budget constraints", () => {
    const { stdout, exitCode } = runCli(
      [
        "campaign",
        "create",
        "--name",
        "Budgeted",
        "--goal",
        "Test budget",
        "--max-missions",
        "5",
        "--max-steps",
        "50",
      ],
      { cwd: dir },
    );
    expect(exitCode).toBe(0);
    const parsed = JSON.parse(stdout);
    expect(parsed.id).toBeTruthy();
    expect(parsed.status).toBe("active");
  });

  it("requires name and goal", () => {
    const { exitCode, stderr } = runCli(["campaign", "create"], { cwd: dir });
    expect(exitCode).toBe(1);
    expect(stderr).toContain("--name");
    expect(stderr).toContain("--goal");
  });
});

// ---------------------------------------------------------------------------
// CLI: campaign status
// ---------------------------------------------------------------------------

describe("autoctx campaign status", () => {
  let dir: string;
  beforeEach(() => {
    dir = setupProjectDir();
  });
  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("returns campaign details with progress", () => {
    const { stdout: created } = runCli(
      ["campaign", "create", "--name", "Test", "--goal", "Do thing"],
      { cwd: dir },
    );
    const { id } = JSON.parse(created);

    const { stdout, exitCode } = runCli(["campaign", "status", "--id", id], {
      cwd: dir,
    });
    expect(exitCode).toBe(0);
    const parsed = JSON.parse(stdout);
    expect(parsed.name).toBe("Test");
    expect(parsed.status).toBe("active");
    expect(parsed.progress).toBeDefined();
    expect(parsed.progress.totalMissions).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// CLI: campaign list
// ---------------------------------------------------------------------------

describe("autoctx campaign list", () => {
  let dir: string;
  beforeEach(() => {
    dir = setupProjectDir();
  });
  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("lists all campaigns as JSON", () => {
    runCli(["campaign", "create", "--name", "A", "--goal", "g1"], { cwd: dir });
    runCli(["campaign", "create", "--name", "B", "--goal", "g2"], { cwd: dir });

    const { stdout, exitCode } = runCli(["campaign", "list"], { cwd: dir });
    expect(exitCode).toBe(0);
    const parsed = JSON.parse(stdout);
    expect(parsed.length).toBe(2);
  });

  it("filters by status", () => {
    const { stdout: r1 } = runCli(
      ["campaign", "create", "--name", "A", "--goal", "g1"],
      { cwd: dir },
    );
    runCli(["campaign", "create", "--name", "B", "--goal", "g2"], { cwd: dir });
    const { id } = JSON.parse(r1);
    runCli(["campaign", "pause", "--id", id], { cwd: dir });

    const { stdout } = runCli(["campaign", "list", "--status", "active"], {
      cwd: dir,
    });
    const parsed = JSON.parse(stdout);
    expect(parsed.length).toBe(1);
    expect(parsed[0].name).toBe("B");
  });
});

// ---------------------------------------------------------------------------
// CLI: campaign add-mission + progress
// ---------------------------------------------------------------------------

describe("autoctx campaign add-mission and progress", () => {
  let dir: string;
  beforeEach(() => {
    dir = setupProjectDir();
  });
  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("adds a mission to a campaign", () => {
    const { stdout: cOut } = runCli(
      ["campaign", "create", "--name", "C", "--goal", "g"],
      { cwd: dir },
    );
    const campaignId = JSON.parse(cOut).id;

    const { stdout: mOut } = runCli(
      ["mission", "create", "--name", "M1", "--goal", "mg"],
      { cwd: dir },
    );
    const missionId = JSON.parse(mOut).id;

    const { exitCode } = runCli(
      [
        "campaign",
        "add-mission",
        "--id",
        campaignId,
        "--mission-id",
        missionId,
      ],
      { cwd: dir },
    );
    expect(exitCode).toBe(0);

    const { stdout: progressOut } = runCli(
      ["campaign", "progress", "--id", campaignId],
      { cwd: dir },
    );
    const progress = JSON.parse(progressOut);
    expect(progress.totalMissions).toBe(1);
  });

  it("adds a mission with priority and dependencies", () => {
    const { stdout: cOut } = runCli(
      ["campaign", "create", "--name", "C", "--goal", "g"],
      { cwd: dir },
    );
    const campaignId = JSON.parse(cOut).id;

    const { stdout: m1Out } = runCli(
      ["mission", "create", "--name", "M1", "--goal", "mg1"],
      { cwd: dir },
    );
    const m1Id = JSON.parse(m1Out).id;

    const { stdout: m2Out } = runCli(
      ["mission", "create", "--name", "M2", "--goal", "mg2"],
      { cwd: dir },
    );
    const m2Id = JSON.parse(m2Out).id;

    runCli(
      ["campaign", "add-mission", "--id", campaignId, "--mission-id", m1Id],
      { cwd: dir },
    );
    const { exitCode } = runCli(
      [
        "campaign",
        "add-mission",
        "--id",
        campaignId,
        "--mission-id",
        m2Id,
        "--priority",
        "10",
        "--depends-on",
        m1Id,
      ],
      { cwd: dir },
    );
    expect(exitCode).toBe(0);

    const { stdout: statusOut } = runCli(
      ["campaign", "status", "--id", campaignId],
      { cwd: dir },
    );
    const status = JSON.parse(statusOut);
    expect(status.missions.length).toBe(2);
  });
});

// ---------------------------------------------------------------------------
// CLI: campaign pause/resume/cancel
// ---------------------------------------------------------------------------

describe("autoctx campaign lifecycle", () => {
  let dir: string;
  beforeEach(() => {
    dir = setupProjectDir();
  });
  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("pause sets status to paused", () => {
    const { stdout: created } = runCli(
      ["campaign", "create", "--name", "T", "--goal", "g"],
      { cwd: dir },
    );
    const { id } = JSON.parse(created);

    const { exitCode } = runCli(["campaign", "pause", "--id", id], {
      cwd: dir,
    });
    expect(exitCode).toBe(0);

    const { stdout } = runCli(["campaign", "status", "--id", id], { cwd: dir });
    expect(JSON.parse(stdout).status).toBe("paused");
  });

  it("resume sets status back to active", () => {
    const { stdout: created } = runCli(
      ["campaign", "create", "--name", "T", "--goal", "g"],
      { cwd: dir },
    );
    const { id } = JSON.parse(created);

    runCli(["campaign", "pause", "--id", id], { cwd: dir });
    runCli(["campaign", "resume", "--id", id], { cwd: dir });

    const { stdout } = runCli(["campaign", "status", "--id", id], { cwd: dir });
    expect(JSON.parse(stdout).status).toBe("active");
  });

  it("cancel sets status to canceled", () => {
    const { stdout: created } = runCli(
      ["campaign", "create", "--name", "T", "--goal", "g"],
      { cwd: dir },
    );
    const { id } = JSON.parse(created);

    runCli(["campaign", "cancel", "--id", id], { cwd: dir });

    const { stdout } = runCli(["campaign", "status", "--id", id], { cwd: dir });
    expect(JSON.parse(stdout).status).toBe("canceled");
  });

  it("returns an error for nonexistent campaign IDs", () => {
    const { stderr, exitCode } = runCli(
      ["campaign", "status", "--id", "nonexistent-id"],
      { cwd: dir },
    );
    expect(exitCode).toBe(1);
    expect(stderr).toContain("Campaign not found");
  });
});
