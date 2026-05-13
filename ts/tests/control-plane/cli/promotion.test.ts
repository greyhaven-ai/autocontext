import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runControlPlaneCommand } from "../../../src/control-plane/cli/index.js";
import { EXIT } from "../../../src/control-plane/cli/_shared/exit-codes.js";

let tmp: string;
let payload: string;

const baseMetrics = {
  quality: { score: 0.9, sampleSize: 1000 },
  cost: { tokensIn: 100, tokensOut: 50 },
  latency: { p50Ms: 10, p95Ms: 20, p99Ms: 30 },
  safety: { regressions: [] },
  evalRunnerIdentity: {
    name: "test",
    version: "1.0.0",
    configHash: "sha256:" + "9".repeat(64),
  },
};

const dp = {
  datasetId: "ds-1",
  sliceHash: "sha256:" + "a".repeat(64),
  sampleCount: 1000,
};

const passedAblation = {
  status: "passed",
  targets: ["strategy", "harness"],
  verifiedAt: "2026-05-13T12:00:00.000Z",
  evidenceRefs: ["runs/ablation/run_1.json"],
};

async function registerPayload(content: string): Promise<string> {
  const d = join(tmp, "payload-" + Math.random().toString(36).slice(2));
  mkdirSync(d, { recursive: true });
  writeFileSync(join(d, "prompt.txt"), content);
  const r = await runControlPlaneCommand(
    ["candidate", "register", "--scenario", "grid_ctf", "--actuator", "prompt-patch", "--payload", d, "--output", "json"],
    { cwd: tmp },
  );
  if (r.exitCode !== 0) throw new Error(`register failed: ${r.stderr}`);
  return JSON.parse(r.stdout).id;
}

async function attachMetrics(
  artifactId: string,
  runId: string,
  metrics: object,
  ablationVerification?: object,
): Promise<void> {
  const mPath = join(tmp, `metrics-${runId}.json`);
  const dpPath = join(tmp, `dp-${runId}.json`);
  const ablationPath = join(tmp, `ablation-${runId}.json`);
  writeFileSync(mPath, JSON.stringify(metrics));
  writeFileSync(dpPath, JSON.stringify(dp));
  if (ablationVerification !== undefined) {
    writeFileSync(ablationPath, JSON.stringify(ablationVerification));
  }
  const ablationArgs = ablationVerification === undefined
    ? []
    : ["--ablation-verification", ablationPath];
  const r = await runControlPlaneCommand(
    [
      "eval",
      "attach",
      artifactId,
      "--suite",
      "prod-eval",
      "--metrics",
      mPath,
      "--dataset-provenance",
      dpPath,
      "--run-id",
      runId,
      ...ablationArgs,
    ],
    { cwd: tmp },
  );
  if (r.exitCode !== 0) throw new Error(`attach failed: ${r.stderr}`);
}

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "autocontext-cli-prom-"));
  payload = join(tmp, "payload");
  mkdirSync(payload, { recursive: true });
  writeFileSync(join(payload, "prompt.txt"), "v1");
});

afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
});

describe("promotion --help", () => {
  test("prints help", async () => {
    const r = await runControlPlaneCommand(["promotion", "--help"], { cwd: tmp });
    expect(r.exitCode).toBe(0);
    expect(r.stdout).toContain("decide");
    expect(r.stdout).toContain("apply");
    expect(r.stdout).toContain("history");
  });
});

describe("promotion decide — exit codes per spec §6.5", () => {
  test("strong pass → exit 0 (active recommended)", async () => {
    const id = await registerPayload("v1");
    await attachMetrics(id, "run_1", {
      ...baseMetrics,
      quality: { score: 0.99, sampleSize: 2000 },
    });
    // Baseline with worse quality + enough samples for strong confidence.
    const baselineId = await registerPayload("base");
    await attachMetrics(baselineId, "run_base", {
      ...baseMetrics,
      quality: { score: 0.5, sampleSize: 2000 },
    });
    const rApply = await runControlPlaneCommand(
      ["promotion", "apply", baselineId, "--to", "active", "--reason", "initial"],
      { cwd: tmp },
    );
    expect(rApply.exitCode).toBe(0);

    const r = await runControlPlaneCommand(
      ["promotion", "decide", id, "--baseline", "auto", "--output", "json"],
      { cwd: tmp },
    );
    const decision = JSON.parse(r.stdout);
    expect(decision.pass).toBe(true);
    expect(decision.recommendedTargetState).toBe("active");
    expect(r.exitCode).toBe(EXIT.PASS_STRONG_OR_MODERATE);
  });

  test("marginal (shadow-only) → exit 2", async () => {
    // Deltas that pass quality/cost/latency but tiny sample size → low confidence → shadow.
    // Use values that avoid float-epsilon issues (0.8 - 0.7 = 0.09999... so we use 0.8 - 0.6 = 0.2).
    const id = await registerPayload("v1");
    await attachMetrics(id, "run_1", {
      ...baseMetrics,
      quality: { score: 0.8, sampleSize: 3 },
    });
    const baselineId = await registerPayload("base");
    await attachMetrics(baselineId, "run_base", {
      ...baseMetrics,
      quality: { score: 0.6, sampleSize: 3 },
    });
    await runControlPlaneCommand(
      ["promotion", "apply", baselineId, "--to", "active", "--reason", "initial"],
      { cwd: tmp },
    );
    const r = await runControlPlaneCommand(
      ["promotion", "decide", id, "--baseline", "auto", "--output", "json"],
      { cwd: tmp },
    );
    const decision = JSON.parse(r.stdout);
    expect(decision.pass).toBe(true);
    expect(decision.recommendedTargetState).toBe("shadow");
    expect(r.exitCode).toBe(EXIT.MARGINAL);
  });

  test("hard fail (safety regression) → exit 1", async () => {
    const id = await registerPayload("v1");
    await attachMetrics(id, "run_1", {
      ...baseMetrics,
      safety: {
        regressions: [
          { id: "r1", severity: "critical", description: "broken output" },
        ],
      },
    });
    const r = await runControlPlaneCommand(
      ["promotion", "decide", id, "--baseline", "none", "--output", "json"],
      { cwd: tmp },
    );
    const decision = JSON.parse(r.stdout);
    expect(decision.pass).toBe(false);
    expect(r.exitCode).toBe(EXIT.HARD_FAIL);
  });

  test("--require-ablation fails when the latest candidate EvalRun lacks evidence", async () => {
    const id = await registerPayload("v1");
    await attachMetrics(id, "run_1", baseMetrics);
    const r = await runControlPlaneCommand(
      ["promotion", "decide", id, "--baseline", "none", "--require-ablation", "--output", "json"],
      { cwd: tmp },
    );

    const decision = JSON.parse(r.stdout);
    expect(decision.pass).toBe(false);
    expect(decision.ablationVerification.status).toBe("missing");
    expect(decision.reasoning).toContain("missing required ablation verification");
    expect(r.exitCode).toBe(EXIT.HARD_FAIL);
  });

  test("--require-ablation passes through when ablation evidence covers strategy and harness", async () => {
    const id = await registerPayload("v1");
    await attachMetrics(id, "run_ablation", baseMetrics, passedAblation);
    const r = await runControlPlaneCommand(
      ["promotion", "decide", id, "--baseline", "none", "--require-ablation", "--output", "json"],
      { cwd: tmp },
    );

    const decision = JSON.parse(r.stdout);
    expect(decision.pass).toBe(true);
    expect(decision.ablationVerification.status).toBe("passed");
    expect(decision.ablationVerification.requiredTargets).toEqual(["strategy", "harness"]);
    expect(r.exitCode).toBe(EXIT.MARGINAL);
  });

  test("--ablation-targets narrows the opt-in requirement", async () => {
    const id = await registerPayload("v1");
    await attachMetrics(id, "run_ablation", baseMetrics, {
      ...passedAblation,
      targets: ["strategy"],
    });
    const r = await runControlPlaneCommand(
      [
        "promotion",
        "decide",
        id,
        "--baseline",
        "none",
        "--require-ablation",
        "--ablation-targets",
        "strategy",
        "--output",
        "json",
      ],
      { cwd: tmp },
    );

    const decision = JSON.parse(r.stdout);
    expect(decision.pass).toBe(true);
    expect(decision.ablationVerification.requiredTargets).toEqual(["strategy"]);
  });

  test("missing eval runs → exit MISSING_BASELINE", async () => {
    const id = await registerPayload("v1");
    const r = await runControlPlaneCommand(
      ["promotion", "decide", id, "--baseline", "none", "--output", "json"],
      { cwd: tmp },
    );
    expect(r.exitCode).toBe(EXIT.MISSING_BASELINE);
  });
});

describe("promotion apply", () => {
  test("transitions candidate → shadow", async () => {
    const id = await registerPayload("v1");
    const r = await runControlPlaneCommand(
      ["promotion", "apply", id, "--to", "shadow", "--reason", "initial-eval"],
      { cwd: tmp },
    );
    expect(r.exitCode).toBe(0);

    const rShow = await runControlPlaneCommand(
      ["candidate", "show", id, "--output", "json"],
      { cwd: tmp },
    );
    expect(JSON.parse(rShow.stdout).activationState).toBe("shadow");
  });

  test("--dry-run makes no state changes", async () => {
    const id = await registerPayload("v1");
    const r = await runControlPlaneCommand(
      ["promotion", "apply", id, "--to", "active", "--reason", "trial", "--dry-run"],
      { cwd: tmp },
    );
    expect(r.exitCode).toBe(0);
    expect(r.stdout).toContain("dry-run");

    // Still candidate on disk.
    const rShow = await runControlPlaneCommand(
      ["candidate", "show", id, "--output", "json"],
      { cwd: tmp },
    );
    expect(JSON.parse(rShow.stdout).activationState).toBe("candidate");
  });

  test("rejects disallowed transition", async () => {
    const id = await registerPayload("v1");
    // candidate → deprecated is not in allow-list.
    const r = await runControlPlaneCommand(
      ["promotion", "apply", id, "--to", "deprecated", "--reason", "x"],
      { cwd: tmp },
    );
    expect(r.exitCode).not.toBe(0);
  });
});

describe("promotion history", () => {
  test("dumps promotion-history.jsonl after an apply", async () => {
    const id = await registerPayload("v1");
    await runControlPlaneCommand(
      ["promotion", "apply", id, "--to", "shadow", "--reason", "eval-1"],
      { cwd: tmp },
    );
    const r = await runControlPlaneCommand(
      ["promotion", "history", id, "--output", "json"],
      { cwd: tmp },
    );
    expect(r.exitCode).toBe(0);
    const history = JSON.parse(r.stdout);
    expect(Array.isArray(history)).toBe(true);
    expect(history).toHaveLength(1);
    expect(history[0].to).toBe("shadow");
  });
});
