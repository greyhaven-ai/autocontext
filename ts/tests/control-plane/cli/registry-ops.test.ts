import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync, unlinkSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runControlPlaneCommand } from "../../../src/control-plane/cli/index.js";
import { EXIT } from "../../../src/control-plane/cli/_shared/exit-codes.js";

let tmp: string;

async function registerPayload(content: string): Promise<string> {
  const d = join(tmp, "payload-" + Math.random().toString(36).slice(2));
  mkdirSync(d, { recursive: true });
  writeFileSync(join(d, "f.txt"), content);
  const r = await runControlPlaneCommand(
    ["candidate", "register", "--scenario", "grid_ctf", "--actuator", "prompt-patch", "--payload", d, "--output", "json"],
    { cwd: tmp },
  );
  if (r.exitCode !== 0) throw new Error(`register failed: ${r.stderr}`);
  return JSON.parse(r.stdout).id;
}

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "autocontext-cli-reg-"));
});

afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
});

describe("registry --help", () => {
  test("prints help", async () => {
    const r = await runControlPlaneCommand(["registry", "--help"], { cwd: tmp });
    expect(r.exitCode).toBe(0);
    expect(r.stdout).toContain("repair");
    expect(r.stdout).toContain("validate");
    expect(r.stdout).toContain("migrate");
  });
});

describe("registry repair", () => {
  test("rebuilds state pointer after state/ directory deletion", async () => {
    const id = await registerPayload("v1");
    // Promote to active so a pointer exists.
    const rApply = await runControlPlaneCommand(
      ["promotion", "apply", id, "--to", "active", "--reason", "initial"],
      { cwd: tmp },
    );
    expect(rApply.exitCode).toBe(0);

    // Delete the state pointer directory.
    const pointerPath = join(tmp, ".autocontext", "state", "active", "grid_ctf", "prompt-patch", "production.json");
    expect(existsSync(pointerPath)).toBe(true);
    unlinkSync(pointerPath);
    expect(existsSync(pointerPath)).toBe(false);

    const rRepair = await runControlPlaneCommand(["registry", "repair"], { cwd: tmp });
    expect(rRepair.exitCode).toBe(0);
    expect(existsSync(pointerPath)).toBe(true);
  });
});

describe("registry validate", () => {
  test("reports ok for a clean registry", async () => {
    await registerPayload("v1");
    const r = await runControlPlaneCommand(
      ["registry", "validate", "--output", "json"],
      { cwd: tmp },
    );
    const report = JSON.parse(r.stdout);
    expect(report.ok).toBe(true);
    expect(r.exitCode).toBe(EXIT.PASS_STRONG_OR_MODERATE);
  });

  test("reports issues + non-zero exit for tampered payload", async () => {
    const id = await registerPayload("v1");
    // Tamper: overwrite payload file so hash mismatches.
    const payloadFile = join(tmp, ".autocontext", "candidates", id, "payload", "f.txt");
    writeFileSync(payloadFile, "tampered!");
    const r = await runControlPlaneCommand(
      ["registry", "validate", "--output", "json"],
      { cwd: tmp },
    );
    const report = JSON.parse(r.stdout);
    expect(report.ok).toBe(false);
    expect(r.exitCode).toBe(EXIT.VALIDATION_FAILED);
    expect(report.issues.some((i: { kind: string }) => i.kind === "payload-hash-mismatch")).toBe(true);
  });
});

describe("registry migrate (stub)", () => {
  test("exits non-zero with not-implemented message", async () => {
    const r = await runControlPlaneCommand(["registry", "migrate"], { cwd: tmp });
    expect(r.exitCode).toBe(EXIT.NOT_IMPLEMENTED);
    expect(r.stderr.toLowerCase()).toContain("not implemented");
  });
});
