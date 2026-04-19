import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runProductionTracesCommand } from "../../../../src/production-traces/cli/index.js";
import { acquireLock } from "../../../../src/production-traces/ingest/lock.js";

let cwd: string;

beforeEach(() => {
  cwd = mkdtempSync(join(tmpdir(), "autocontext-pt-cli-init-"));
});
afterEach(() => {
  rmSync(cwd, { recursive: true, force: true });
});

describe("autoctx production-traces init", () => {
  test("scaffolds directory tree + policies + salt on a fresh cwd", async () => {
    const r = await runProductionTracesCommand(["init", "--output", "json"], { cwd });
    expect(r.exitCode).toBe(0);
    const report = JSON.parse(r.stdout);
    expect(report.cwd).toBe(cwd);
    expect(Array.isArray(report.created)).toBe(true);
    expect(Array.isArray(report.alreadyPresent)).toBe(true);
    // On first run, all created, nothing already present.
    expect(report.alreadyPresent).toHaveLength(0);

    expect(existsSync(join(cwd, ".autocontext", "production-traces", "incoming"))).toBe(true);
    expect(existsSync(join(cwd, ".autocontext", "production-traces", "ingested"))).toBe(true);
    expect(existsSync(join(cwd, ".autocontext", "production-traces", "failed"))).toBe(true);
    expect(existsSync(join(cwd, ".autocontext", "production-traces", "gc"))).toBe(true);
    expect(existsSync(join(cwd, ".autocontext", "production-traces", "redaction-policy.json"))).toBe(true);
    expect(existsSync(join(cwd, ".autocontext", "production-traces", "retention-policy.json"))).toBe(true);
    expect(existsSync(join(cwd, ".autocontext", "install-salt"))).toBe(true);
  });

  test("default redaction-policy.json has mode on-export", async () => {
    await runProductionTracesCommand(["init", "--output", "json"], { cwd });
    const policy = JSON.parse(
      readFileSync(join(cwd, ".autocontext", "production-traces", "redaction-policy.json"), "utf-8"),
    );
    expect(policy.mode).toBe("on-export");
  });

  test("default retention-policy.json has 90-day retention + preserves failure", async () => {
    await runProductionTracesCommand(["init", "--output", "json"], { cwd });
    const policy = JSON.parse(
      readFileSync(join(cwd, ".autocontext", "production-traces", "retention-policy.json"), "utf-8"),
    );
    expect(policy.retentionDays).toBe(90);
    expect(policy.preserveCategories).toContain("failure");
  });

  test("idempotent: second run reports everything as already-present", async () => {
    await runProductionTracesCommand(["init", "--output", "json"], { cwd });
    const r2 = await runProductionTracesCommand(["init", "--output", "json"], { cwd });
    expect(r2.exitCode).toBe(0);
    const report = JSON.parse(r2.stdout);
    expect(report.created).toHaveLength(0);
    // alreadyPresent should cover all scaffolded paths.
    expect(report.alreadyPresent.length).toBeGreaterThan(5);
  });

  test("idempotent: second run does NOT rotate the install-salt", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    const saltPath = join(cwd, ".autocontext", "install-salt");
    const saltBefore = readFileSync(saltPath, "utf-8");
    await runProductionTracesCommand(["init"], { cwd });
    const saltAfter = readFileSync(saltPath, "utf-8");
    expect(saltBefore).toBe(saltAfter);
  });

  test("lock contention yields exit 10 with lock-timeout diagnostic", async () => {
    const holder = acquireLock(cwd);
    try {
      const r = await runProductionTracesCommand(["init", "--output", "json"], { cwd });
      expect(r.exitCode).toBe(10);
      expect(r.stderr.toLowerCase()).toMatch(/lock/);
    } finally {
      holder.release();
    }
  });

  test("--help exits 0", async () => {
    const r = await runProductionTracesCommand(["init", "--help"], { cwd });
    expect(r.exitCode).toBe(0);
    expect(r.stdout.toLowerCase()).toContain("init");
  });
});
