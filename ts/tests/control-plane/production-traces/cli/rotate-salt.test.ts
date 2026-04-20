import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runProductionTracesCommand } from "../../../../src/production-traces/cli/index.js";

let cwd: string;

beforeEach(() => {
  cwd = mkdtempSync(join(tmpdir(), "autocontext-pt-cli-rotate-"));
});
afterEach(() => {
  rmSync(cwd, { recursive: true, force: true });
});

describe("autoctx production-traces rotate-salt", () => {
  test("refuses to run without --force (exit 1)", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    const r = await runProductionTracesCommand(["rotate-salt"], { cwd });
    expect(r.exitCode).toBe(1);
    expect(r.stderr).toContain("--force");
  });

  test("rotates with --force and emits a break-glass advisory", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    const saltPath = join(cwd, ".autocontext/install-salt");
    const before = readFileSync(saltPath, "utf-8");

    const r = await runProductionTracesCommand(
      ["rotate-salt", "--force", "--output", "json"],
      { cwd },
    );
    expect(r.exitCode).toBe(0);
    const after = readFileSync(saltPath, "utf-8");
    expect(after).not.toBe(before);
    expect(after.trim()).toHaveLength(64); // 256-bit hex = 64 chars
    expect(r.stderr.toLowerCase()).toContain("break-glass");
  });

  test("--help exits 0", async () => {
    const r = await runProductionTracesCommand(["rotate-salt", "--help"], { cwd });
    expect(r.exitCode).toBe(0);
  });
});
