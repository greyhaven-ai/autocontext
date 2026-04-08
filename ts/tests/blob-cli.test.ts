/**
 * AC-518 Phase 4: Blob CLI tests.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";

const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");

function runCli(
  args: string[],
  opts: { cwd?: string; env?: Record<string, string> } = {},
): { stdout: string; stderr: string; exitCode: number } {
  const result = spawnSync("npx", ["tsx", CLI, ...args], {
    cwd: opts.cwd,
    env: { ...process.env, NODE_NO_WARNINGS: "1", ...opts.env },
    encoding: "utf8",
    timeout: 10000,
  });
  return {
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    exitCode: result.status ?? 1,
  };
}

let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac518-blob-cli-"));
});
afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

describe("autoctx blob --help", () => {
  it("shows blob subcommands", () => {
    const { stdout, exitCode } = runCli(["blob", "--help"]);
    expect(exitCode).toBe(0);
    expect(stdout).toContain("sync");
    expect(stdout).toContain("status");
    expect(stdout).toContain("hydrate");
  });
});

describe("autoctx blob status", () => {
  it("reports empty store when no blobs exist", () => {
    const { stdout, exitCode } = runCli(["blob", "status", "--json"], {
      env: {
        AUTOCONTEXT_BLOB_STORE_ENABLED: "true",
        AUTOCONTEXT_BLOB_STORE_BACKEND: "local",
        AUTOCONTEXT_BLOB_STORE_ROOT: join(tmpDir, "blobs"),
        AUTOCONTEXT_RUNS_ROOT: join(tmpDir, "runs"),
      },
    });
    expect(exitCode).toBe(0);
    const parsed = JSON.parse(stdout);
    expect(parsed.totalBlobs).toBe(0);
    expect(parsed.runCount).toBe(0);
  });
});

describe("autoctx blob sync", () => {
  it("syncs a run directory to blob store", () => {
    const runsRoot = join(tmpDir, "runs");
    const runDir = join(runsRoot, "run_001");
    mkdirSync(runDir, { recursive: true });
    writeFileSync(join(runDir, "events.ndjson"), '{"e":"start"}\n');

    const { stdout, exitCode } = runCli(
      ["blob", "sync", "--run-id", "run_001", "--json"],
      {
        env: {
          AUTOCONTEXT_BLOB_STORE_ENABLED: "true",
          AUTOCONTEXT_BLOB_STORE_BACKEND: "local",
          AUTOCONTEXT_BLOB_STORE_ROOT: join(tmpDir, "blobs"),
          AUTOCONTEXT_RUNS_ROOT: runsRoot,
        },
      },
    );
    expect(exitCode).toBe(0);
    const parsed = JSON.parse(stdout);
    expect(parsed.syncedCount).toBeGreaterThanOrEqual(1);
  });

  it("exits 1 when blob store is not enabled", () => {
    const { exitCode, stderr } = runCli(["blob", "sync", "--run-id", "r1"], {
      env: { AUTOCONTEXT_BLOB_STORE_ENABLED: "false" },
    });
    expect(exitCode).toBe(1);
    expect(stderr.toLowerCase()).toContain("not enabled");
  });
});
