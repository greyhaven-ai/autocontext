import { describe, test, expect, beforeEach, afterEach } from "vitest";
import {
  mkdtempSync,
  rmSync,
  mkdirSync,
  writeFileSync,
  unlinkSync,
  existsSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { ulid } from "ulid";
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

describe("registry migrate", () => {
  function legacyRecord(artifactId: string): Record<string, unknown> {
    return {
      artifactId,
      scenario: "grid_ctf",
      family: "llama-3",
      backend: "mlx",
      checkpointDir: "/mnt/models/grid_ctf-v1",
      checkpointHash: "sha256:" + "a".repeat(64),
      activationState: "candidate",
      promotionHistory: [],
      registeredAt: "2026-04-17T12:00:00.000Z",
    };
  }

  test("prints a pretty summary (imported/skipped/errors) on success and exits 0", async () => {
    const fromPath = join(tmp, "legacy.json");
    writeFileSync(fromPath, JSON.stringify([legacyRecord(ulid()), legacyRecord(ulid())]), "utf-8");

    const r = await runControlPlaneCommand(
      ["registry", "migrate", "--from", fromPath],
      { cwd: tmp },
    );
    expect(r.exitCode).toBe(0);
    expect(r.stdout.toLowerCase()).toContain("imported");
    expect(r.stdout).toContain("2");
  });

  test("emits structured JSON with --output json and exits 0 on clean run", async () => {
    const fromPath = join(tmp, "legacy.json");
    const id = ulid();
    writeFileSync(fromPath, JSON.stringify([legacyRecord(id)]), "utf-8");

    const r = await runControlPlaneCommand(
      ["registry", "migrate", "--from", fromPath, "--output", "json"],
      { cwd: tmp },
    );
    expect(r.exitCode).toBe(0);
    const parsed = JSON.parse(r.stdout);
    expect(parsed.imported).toBe(1);
    expect(parsed.skipped).toBe(0);
    expect(parsed.errors).toEqual([]);
  });

  test("exits 1 when one or more records error, but still imports the good ones", async () => {
    const fromPath = join(tmp, "legacy.json");
    const goodId = ulid();
    const good = legacyRecord(goodId);
    const bad = { ...legacyRecord(ulid()), scenario: "INVALID SLUG!" };
    writeFileSync(fromPath, JSON.stringify([good, bad]), "utf-8");

    const r = await runControlPlaneCommand(
      ["registry", "migrate", "--from", fromPath, "--output", "json"],
      { cwd: tmp },
    );
    expect(r.exitCode).toBe(EXIT.HARD_FAIL);
    const parsed = JSON.parse(r.stdout);
    expect(parsed.imported).toBe(1);
    expect(parsed.errors).toHaveLength(1);
  });

  test("help: migrate --help documents --from and --output and the default discovery path", async () => {
    const r = await runControlPlaneCommand(["registry", "migrate", "--help"], { cwd: tmp });
    expect(r.exitCode).toBe(0);
    expect(r.stdout).toContain("--from");
    expect(r.stdout).toContain("legacy-model-records.json");
  });

  test("discovers <cwd>/.autocontext/legacy-model-records.json when --from omitted", async () => {
    mkdirSync(join(tmp, ".autocontext"), { recursive: true });
    writeFileSync(
      join(tmp, ".autocontext", "legacy-model-records.json"),
      JSON.stringify([legacyRecord(ulid())]),
      "utf-8",
    );

    const r = await runControlPlaneCommand(
      ["registry", "migrate", "--output", "json"],
      { cwd: tmp },
    );
    expect(r.exitCode).toBe(0);
    const parsed = JSON.parse(r.stdout);
    expect(parsed.imported).toBe(1);
  });
});
