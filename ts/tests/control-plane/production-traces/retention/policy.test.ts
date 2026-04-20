import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  loadRetentionPolicy,
  saveRetentionPolicy,
  defaultRetentionPolicy,
  retentionPolicyPath,
  type RetentionPolicy,
} from "../../../../src/production-traces/retention/index.js";
import { productionTracesRoot } from "../../../../src/production-traces/ingest/paths.js";

let cwd: string;

beforeEach(() => {
  cwd = mkdtempSync(join(tmpdir(), "autocontext-pt-retention-policy-"));
});
afterEach(() => {
  rmSync(cwd, { recursive: true, force: true });
});

describe("retention/policy", () => {
  test("defaultRetentionPolicy returns the spec §6.6 defaults", () => {
    const p = defaultRetentionPolicy();
    expect(p.schemaVersion).toBe("1.0");
    expect(p.retentionDays).toBe(90);
    expect(p.preserveAll).toBe(false);
    expect(p.preserveCategories).toEqual(["failure"]);
    expect(p.gcBatchSize).toBe(1000);
  });

  test("loadRetentionPolicy falls back to defaults when file is missing", async () => {
    const p = await loadRetentionPolicy(cwd);
    expect(p).toEqual(defaultRetentionPolicy());
  });

  test("save then load round-trips the policy byte-identically", async () => {
    const policy: RetentionPolicy = {
      schemaVersion: "1.0",
      retentionDays: 30,
      preserveAll: false,
      preserveCategories: ["failure", "partial"],
      gcBatchSize: 500,
    };
    await saveRetentionPolicy(cwd, policy);
    expect(existsSync(retentionPolicyPath(cwd))).toBe(true);

    const loaded = await loadRetentionPolicy(cwd);
    expect(loaded).toEqual(policy);
  });

  test("retentionPolicyPath is under productionTracesRoot", () => {
    const p = retentionPolicyPath(cwd);
    expect(p).toBe(join(productionTracesRoot(cwd), "retention-policy.json"));
  });

  test("loadRetentionPolicy rejects malformed JSON with a clear error", async () => {
    mkdirSync(productionTracesRoot(cwd), { recursive: true });
    writeFileSync(retentionPolicyPath(cwd), "{not-json", "utf-8");
    await expect(loadRetentionPolicy(cwd)).rejects.toThrow(/malformed JSON/);
  });

  test("loadRetentionPolicy rejects policy with retentionDays < 0", async () => {
    mkdirSync(productionTracesRoot(cwd), { recursive: true });
    writeFileSync(
      retentionPolicyPath(cwd),
      JSON.stringify({
        schemaVersion: "1.0",
        retentionDays: -1,
        preserveAll: false,
        preserveCategories: [],
        gcBatchSize: 100,
      }),
      "utf-8",
    );
    await expect(loadRetentionPolicy(cwd)).rejects.toThrow();
  });

  test("loadRetentionPolicy rejects policy with gcBatchSize <= 0", async () => {
    mkdirSync(productionTracesRoot(cwd), { recursive: true });
    writeFileSync(
      retentionPolicyPath(cwd),
      JSON.stringify({
        schemaVersion: "1.0",
        retentionDays: 30,
        preserveAll: false,
        preserveCategories: [],
        gcBatchSize: 0,
      }),
      "utf-8",
    );
    await expect(loadRetentionPolicy(cwd)).rejects.toThrow();
  });

  test("loadRetentionPolicy rejects policy with non-string preserveCategories entries", async () => {
    mkdirSync(productionTracesRoot(cwd), { recursive: true });
    writeFileSync(
      retentionPolicyPath(cwd),
      JSON.stringify({
        schemaVersion: "1.0",
        retentionDays: 30,
        preserveAll: false,
        preserveCategories: ["failure", 123],
        gcBatchSize: 100,
      }),
      "utf-8",
    );
    await expect(loadRetentionPolicy(cwd)).rejects.toThrow();
  });

  test("loadRetentionPolicy rejects policy with wrong schemaVersion", async () => {
    mkdirSync(productionTracesRoot(cwd), { recursive: true });
    writeFileSync(
      retentionPolicyPath(cwd),
      JSON.stringify({
        schemaVersion: "2.0",
        retentionDays: 30,
        preserveAll: false,
        preserveCategories: [],
        gcBatchSize: 100,
      }),
      "utf-8",
    );
    await expect(loadRetentionPolicy(cwd)).rejects.toThrow();
  });

  test("saveRetentionPolicy writes canonical JSON (deterministic key order)", async () => {
    // Write with keys in an unusual insertion order.
    const policy: RetentionPolicy = {
      schemaVersion: "1.0",
      retentionDays: 45,
      preserveAll: false,
      preserveCategories: ["failure"],
      gcBatchSize: 250,
    };
    await saveRetentionPolicy(cwd, policy);
    const { readFileSync } = await import("node:fs");
    const raw = readFileSync(retentionPolicyPath(cwd), "utf-8");
    // Canonical JSON sorts keys lexicographically.
    const keyOrder = raw.match(/"(\w+)"\s*:/g)?.map((m) => m.match(/"(\w+)"/)![1]) ?? [];
    expect(keyOrder).toEqual([
      "gcBatchSize",
      "preserveAll",
      "preserveCategories",
      "retentionDays",
      "schemaVersion",
    ]);
  });
});
