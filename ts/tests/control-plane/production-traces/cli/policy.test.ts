import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runProductionTracesCommand } from "../../../../src/production-traces/cli/index.js";

let cwd: string;

beforeEach(() => {
  cwd = mkdtempSync(join(tmpdir(), "autocontext-pt-cli-policy-"));
});
afterEach(() => {
  rmSync(cwd, { recursive: true, force: true });
});

describe("autoctx production-traces policy show", () => {
  test("shows defaults on a cwd with no policy file", async () => {
    const r = await runProductionTracesCommand(
      ["policy", "show", "--output", "json"],
      { cwd },
    );
    expect(r.exitCode).toBe(0);
    const policy = JSON.parse(r.stdout);
    expect(policy.mode).toBe("on-export");
  });

  test("shows persisted policy after init", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    const r = await runProductionTracesCommand(
      ["policy", "show", "--output", "json"],
      { cwd },
    );
    expect(r.exitCode).toBe(0);
    expect(JSON.parse(r.stdout).mode).toBe("on-export");
  });
});

describe("autoctx production-traces policy set", () => {
  test("on-export → on-ingest allowed without --force but warns", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    const r = await runProductionTracesCommand(
      ["policy", "set", "--mode", "on-ingest", "--output", "json"],
      { cwd },
    );
    expect(r.exitCode).toBe(0);
    expect(r.stderr.toLowerCase()).toContain("warning");
    const policyPath = join(
      cwd,
      ".autocontext/production-traces/redaction-policy.json",
    );
    const parsed = JSON.parse(readFileSync(policyPath, "utf-8"));
    expect(parsed.mode).toBe("on-ingest");
  });

  test("on-ingest → on-export without --force is refused with exit 1", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    await runProductionTracesCommand(
      ["policy", "set", "--mode", "on-ingest"],
      { cwd },
    );
    const r = await runProductionTracesCommand(
      ["policy", "set", "--mode", "on-export"],
      { cwd },
    );
    expect(r.exitCode).toBe(1);
    expect(r.stderr.toLowerCase()).toContain("--force");
  });

  test("on-ingest → on-export with --force succeeds (with advisory)", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    await runProductionTracesCommand(
      ["policy", "set", "--mode", "on-ingest"],
      { cwd },
    );
    const r = await runProductionTracesCommand(
      ["policy", "set", "--mode", "on-export", "--force", "--output", "json"],
      { cwd },
    );
    expect(r.exitCode).toBe(0);
    expect(r.stderr.toLowerCase()).toContain("warning");
  });

  test("setting to the same mode is a no-op with a harmless diagnostic", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    const r = await runProductionTracesCommand(
      ["policy", "set", "--mode", "on-export", "--output", "json"],
      { cwd },
    );
    expect(r.exitCode).toBe(0);
    expect(r.stderr.toLowerCase()).toMatch(/already/);
  });

  test("invalid --mode value yields exit 1", async () => {
    const r = await runProductionTracesCommand(
      ["policy", "set", "--mode", "garbage"],
      { cwd },
    );
    expect(r.exitCode).toBe(1);
  });

  test("missing --mode is a required-flag error", async () => {
    const r = await runProductionTracesCommand(["policy", "set"], { cwd });
    expect(r.exitCode).toBe(1);
    expect(r.stderr).toContain("--mode");
  });

  test("--help on show + set + namespace all return exit 0", async () => {
    const a = await runProductionTracesCommand(["policy", "--help"], { cwd });
    expect(a.exitCode).toBe(0);
  });
});
