import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runProductionTracesCommand } from "../../../../src/production-traces/cli/index.js";

let cwd: string;

function writeFakeDataset(id: string, overrides: Partial<{
  name: string;
  traceCount: number;
  trainRows: number;
}> = {}): void {
  const dir = join(cwd, ".autocontext", "datasets", id);
  mkdirSync(dir, { recursive: true });
  const manifest = {
    schemaVersion: "1.0",
    datasetId: id,
    name: overrides.name ?? "fake",
    description: "",
    createdAt: "2026-04-17T12:00:00.000Z",
    autoctxVersion: "test",
    source: {
      traceCount: overrides.traceCount ?? 1,
      timeRange: { from: "2026-04-17T12:00:00.000Z", to: "2026-04-17T12:00:01.000Z" },
      clusterStrategy: "taskType",
      filterRules: [],
      redactionPolicy: { mode: "on-export", snapshotHash: "h" },
    },
    splits: {
      train: { rowCount: overrides.trainRows ?? 1, fileHash: "h" },
      eval: { rowCount: 0, fileHash: "h" },
      holdout: { rowCount: 0, fileHash: "h" },
    },
    clusters: [],
    provenance: { configHash: "h", inputTracesHash: "h" },
  };
  writeFileSync(join(dir, "manifest.json"), JSON.stringify(manifest));
}

beforeEach(() => {
  cwd = mkdtempSync(join(tmpdir(), "autocontext-pt-cli-datasets-"));
});
afterEach(() => {
  rmSync(cwd, { recursive: true, force: true });
});

describe("autoctx production-traces datasets list", () => {
  test("empty cwd returns []", async () => {
    const r = await runProductionTracesCommand(
      ["datasets", "list", "--output", "json"],
      { cwd },
    );
    expect(r.exitCode).toBe(0);
    expect(JSON.parse(r.stdout)).toEqual([]);
  });

  test("lists fake manifests", async () => {
    writeFakeDataset("ds_01KFDQ9XZ3M7RT2V8K1PHY4BNC", { name: "one", traceCount: 5 });
    writeFakeDataset("ds_01KFDQ9XZ3M7RT2V8K1PHY4BND", { name: "two", traceCount: 7, trainRows: 5 });
    const r = await runProductionTracesCommand(
      ["datasets", "list", "--output", "json"],
      { cwd },
    );
    expect(r.exitCode).toBe(0);
    const rows = JSON.parse(r.stdout);
    expect(rows).toHaveLength(2);
    const names = rows.map((r: any) => r.name).sort();
    expect(names).toEqual(["one", "two"]);
  });
});

describe("autoctx production-traces datasets show", () => {
  test("round-trips a manifest", async () => {
    writeFakeDataset("ds_01KFDQ9XZ3M7RT2V8K1PHY4BNC", { name: "one" });
    const r = await runProductionTracesCommand(
      ["datasets", "show", "ds_01KFDQ9XZ3M7RT2V8K1PHY4BNC", "--output", "json"],
      { cwd },
    );
    expect(r.exitCode).toBe(0);
    const manifest = JSON.parse(r.stdout);
    expect(manifest.datasetId).toBe("ds_01KFDQ9XZ3M7RT2V8K1PHY4BNC");
    expect(manifest.name).toBe("one");
  });

  test("unknown dataset id yields exit 12", async () => {
    const r = await runProductionTracesCommand(
      ["datasets", "show", "ds_01KFDQ9XZ3M7RT2V8K1PHY4BNC"],
      { cwd },
    );
    expect(r.exitCode).toBe(12);
  });

  test("--help exits 0", async () => {
    const r = await runProductionTracesCommand(["datasets", "--help"], { cwd });
    expect(r.exitCode).toBe(0);
  });
});
