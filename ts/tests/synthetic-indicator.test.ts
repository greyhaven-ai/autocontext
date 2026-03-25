/**
 * Tests for AC-404: Deterministic provider should indicate results are synthetic.
 *
 * - JSON output includes "provider" and "synthetic" fields
 * - Non-JSON mode prints a synthetic banner
 * - Real providers don't get the synthetic flag
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
    timeout: 30000,
    cwd: opts.cwd,
    env: buildEnv(opts.env),
  });
  return { stdout: r.stdout ?? "", stderr: r.stderr ?? "", exitCode: r.status ?? 1 };
}

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-synth-"));
}

// ---------------------------------------------------------------------------
// run --json with deterministic provider
// ---------------------------------------------------------------------------

describe("run --json with deterministic provider", () => {
  let dir: string;
  beforeEach(() => {
    dir = makeTempDir();
    mkdirSync(join(dir, "runs"), { recursive: true });
    mkdirSync(join(dir, "knowledge"), { recursive: true });
    writeFileSync(join(dir, ".autoctx.json"), JSON.stringify({
      default_scenario: "grid_ctf",
      provider: "deterministic",
      gens: 1,
      runs_dir: "./runs",
      knowledge_dir: "./knowledge",
    }, null, 2), "utf-8");
  });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("includes provider field in JSON output", () => {
    const { stdout, exitCode } = runCli(["run", "--json"], { cwd: dir });
    expect(exitCode).toBe(0);
    const parsed = JSON.parse(stdout);
    expect(parsed.provider).toBe("deterministic");
  });

  it("includes synthetic: true in JSON output", () => {
    const { stdout, exitCode } = runCli(["run", "--json"], { cwd: dir });
    expect(exitCode).toBe(0);
    const parsed = JSON.parse(stdout);
    expect(parsed.synthetic).toBe(true);
  });

  it("uses the fully resolved provider when generic env overrides project config", () => {
    writeFileSync(join(dir, ".autoctx.json"), JSON.stringify({
      default_scenario: "grid_ctf",
      provider: "anthropic",
      gens: 1,
      runs_dir: "./runs",
      knowledge_dir: "./knowledge",
    }, null, 2), "utf-8");

    const { stdout, exitCode } = runCli(["run", "--json"], {
      cwd: dir,
      env: { AUTOCONTEXT_PROVIDER: "Deterministic" },
    });
    expect(exitCode).toBe(0);
    const parsed = JSON.parse(stdout);
    expect(parsed.provider).toBe("deterministic");
    expect(parsed.synthetic).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// run without --json with deterministic provider
// ---------------------------------------------------------------------------

describe("run without --json with deterministic provider", () => {
  let dir: string;
  beforeEach(() => {
    dir = makeTempDir();
    mkdirSync(join(dir, "runs"), { recursive: true });
    mkdirSync(join(dir, "knowledge"), { recursive: true });
    writeFileSync(join(dir, ".autoctx.json"), JSON.stringify({
      default_scenario: "grid_ctf",
      provider: "deterministic",
      gens: 1,
      runs_dir: "./runs",
      knowledge_dir: "./knowledge",
    }, null, 2), "utf-8");
  });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("prints a synthetic banner to stderr", () => {
    const { stderr, exitCode } = runCli(["run"], { cwd: dir });
    expect(exitCode).toBe(0);
    expect(stderr).toContain("deterministic");
    expect(stderr).toContain("synthetic");
  });
});

// ---------------------------------------------------------------------------
// benchmark --json with deterministic provider
// ---------------------------------------------------------------------------

describe("benchmark --json with deterministic provider", () => {
  let dir: string;
  beforeEach(() => {
    dir = makeTempDir();
    mkdirSync(join(dir, "runs"), { recursive: true });
    mkdirSync(join(dir, "knowledge"), { recursive: true });
    writeFileSync(join(dir, ".autoctx.json"), JSON.stringify({
      default_scenario: "grid_ctf",
      provider: "deterministic",
      gens: 1,
      runs_dir: "./runs",
      knowledge_dir: "./knowledge",
    }, null, 2), "utf-8");
  });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("includes synthetic flag in JSON benchmark output", () => {
    const { stdout, exitCode } = runCli(["benchmark", "--runs", "1", "--gens", "1", "--json"], { cwd: dir });
    expect(exitCode).toBe(0);
    const parsed = JSON.parse(stdout);
    expect(parsed.synthetic).toBe(true);
    expect(parsed.provider).toBe("deterministic");
  });

  it("normalizes mixed-case provider overrides before labeling results", () => {
    const { stdout, exitCode } = runCli(["benchmark", "--provider", "Deterministic", "--runs", "1", "--gens", "1", "--json"], { cwd: dir });
    expect(exitCode).toBe(0);
    const parsed = JSON.parse(stdout);
    expect(parsed.provider).toBe("deterministic");
    expect(parsed.synthetic).toBe(true);
  });
});
