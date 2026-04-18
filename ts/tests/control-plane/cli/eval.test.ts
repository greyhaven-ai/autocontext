import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runControlPlaneCommand } from "../../../src/control-plane/cli/index.js";

let tmp: string;
let payload: string;
let metricsPath: string;
let dpPath: string;

const goodMetrics = {
  quality: { score: 0.9, sampleSize: 100 },
  cost: { tokensIn: 100, tokensOut: 50 },
  latency: { p50Ms: 10, p95Ms: 20, p99Ms: 30 },
  safety: { regressions: [] },
  evalRunnerIdentity: {
    name: "test",
    version: "1.0.0",
    configHash: "sha256:" + "9".repeat(64),
  },
};

const goodDp = {
  datasetId: "ds-1",
  sliceHash: "sha256:" + "a".repeat(64),
  sampleCount: 100,
};

async function registerArtifact(): Promise<string> {
  const r = await runControlPlaneCommand(
    [
      "candidate",
      "register",
      "--scenario",
      "grid_ctf",
      "--actuator",
      "prompt-patch",
      "--payload",
      payload,
      "--output",
      "json",
    ],
    { cwd: tmp },
  );
  if (r.exitCode !== 0) throw new Error(`register failed: ${r.stderr}`);
  return JSON.parse(r.stdout).id;
}

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "autocontext-cli-eval-"));
  payload = join(tmp, "payload");
  mkdirSync(payload, { recursive: true });
  writeFileSync(join(payload, "prompt.txt"), "v1");
  metricsPath = join(tmp, "metrics.json");
  dpPath = join(tmp, "dp.json");
  writeFileSync(metricsPath, JSON.stringify(goodMetrics));
  writeFileSync(dpPath, JSON.stringify(goodDp));
});

afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
});

describe("eval --help", () => {
  test("prints help", async () => {
    const r = await runControlPlaneCommand(["eval", "--help"], { cwd: tmp });
    expect(r.exitCode).toBe(0);
    expect(r.stdout).toContain("attach");
    expect(r.stdout).toContain("list");
  });
});

describe("eval attach", () => {
  test("attaches an EvalRun from metrics + dataset provenance files", async () => {
    const id = await registerArtifact();
    const r = await runControlPlaneCommand(
      [
        "eval",
        "attach",
        id,
        "--suite",
        "prod-eval",
        "--metrics",
        metricsPath,
        "--dataset-provenance",
        dpPath,
        "--run-id",
        "run_1",
        "--output",
        "json",
      ],
      { cwd: tmp },
    );
    expect(r.exitCode).toBe(0);
    const parsed = JSON.parse(r.stdout);
    expect(parsed.artifactId).toBe(id);
    expect(parsed.runId).toBe("run_1");
    expect(parsed.evalRunCount).toBe(1);
  });

  test("rejects invalid artifact id", async () => {
    const r = await runControlPlaneCommand(
      ["eval", "attach", "bogus-id", "--suite", "prod", "--metrics", metricsPath, "--dataset-provenance", dpPath],
      { cwd: tmp },
    );
    expect(r.exitCode).not.toBe(0);
  });

  test("rejects missing metrics path", async () => {
    const id = await registerArtifact();
    const r = await runControlPlaneCommand(
      ["eval", "attach", id, "--suite", "prod", "--metrics", join(tmp, "nope.json"), "--dataset-provenance", dpPath],
      { cwd: tmp },
    );
    expect(r.exitCode).not.toBe(0);
  });

  test("rejects duplicate (artifact, runId) attach", async () => {
    const id = await registerArtifact();
    await runControlPlaneCommand(
      ["eval", "attach", id, "--suite", "prod-eval", "--metrics", metricsPath, "--dataset-provenance", dpPath, "--run-id", "dup"],
      { cwd: tmp },
    );
    const r2 = await runControlPlaneCommand(
      ["eval", "attach", id, "--suite", "prod-eval", "--metrics", metricsPath, "--dataset-provenance", dpPath, "--run-id", "dup"],
      { cwd: tmp },
    );
    expect(r2.exitCode).not.toBe(0);
    expect(r2.stderr.toLowerCase()).toContain("already");
  });
});

describe("eval list", () => {
  test("lists attached runs after attach", async () => {
    const id = await registerArtifact();
    await runControlPlaneCommand(
      ["eval", "attach", id, "--suite", "prod-eval", "--metrics", metricsPath, "--dataset-provenance", dpPath, "--run-id", "run_1"],
      { cwd: tmp },
    );
    const rList = await runControlPlaneCommand(
      ["eval", "list", id, "--output", "json"],
      { cwd: tmp },
    );
    expect(rList.exitCode).toBe(0);
    const parsed = JSON.parse(rList.stdout);
    expect(Array.isArray(parsed)).toBe(true);
    expect(parsed).toHaveLength(1);
    expect(parsed[0].evalRunId).toBe("run_1");
  });
});
