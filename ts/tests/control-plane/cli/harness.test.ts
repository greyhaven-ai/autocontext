import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runControlPlaneCommand } from "../../../src/control-plane/cli/index.js";
import { EXIT } from "../../../src/control-plane/cli/_shared/exit-codes.js";

let tmp: string;

const baseMetrics = {
  quality: { score: 0.7, sampleSize: 1000 },
  cost: { tokensIn: 100, tokensOut: 50 },
  latency: { p50Ms: 10, p95Ms: 20, p99Ms: 30 },
  safety: { regressions: [] },
  evalRunnerIdentity: {
    name: "heldout",
    version: "1.0.0",
    configHash: `sha256:${"9".repeat(64)}`,
  },
};

const dp = {
  datasetId: "prod-traces",
  sliceHash: `sha256:${"a".repeat(64)}`,
  sampleCount: 1000,
};

async function registerPayload(content: string): Promise<string> {
  const d = join(tmp, `payload-${Math.random().toString(36).slice(2)}`);
  mkdirSync(d, { recursive: true });
  writeFileSync(join(d, "prompt.txt"), content);
  const r = await runControlPlaneCommand(
    ["candidate", "register", "--scenario", "grid_ctf", "--actuator", "prompt-patch", "--payload", d, "--output", "json"],
    { cwd: tmp, now: () => "2026-05-13T12:00:00.000Z" },
  );
  if (r.exitCode !== 0) throw new Error(`register failed: ${r.stderr}`);
  return JSON.parse(r.stdout).id;
}

async function attachMetrics(
  artifactId: string,
  runId: string,
  score: number,
  suite = "heldout-suite",
): Promise<void> {
  const mPath = join(tmp, `metrics-${runId}.json`);
  const dpPath = join(tmp, `dp-${runId}.json`);
  writeFileSync(mPath, JSON.stringify({
    ...baseMetrics,
    quality: { score, sampleSize: 1000 },
  }));
  writeFileSync(dpPath, JSON.stringify(dp));
  const r = await runControlPlaneCommand(
    [
      "eval",
      "attach",
      artifactId,
      "--suite",
      suite,
      "--metrics",
      mPath,
      "--dataset-provenance",
      dpPath,
      "--run-id",
      runId,
    ],
    { cwd: tmp, now: () => "2026-05-13T12:05:00.000Z" },
  );
  if (r.exitCode !== 0) throw new Error(`attach failed: ${r.stderr}`);
}

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "autocontext-cli-harness-"));
});

afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
});

describe("harness proposal CLI", () => {
  test("creates, lists, and gates a harness proposal against heldout evidence", async () => {
    const patchPath = join(tmp, "patches.json");
    writeFileSync(patchPath, JSON.stringify([
      {
        filePath: "agents/grid_ctf/prompts/competitor.txt",
        operation: "modify",
        unifiedDiff: "--- a/competitor.txt\n+++ b/competitor.txt\n@@ -1 +1 @@\n-old\n+new\n",
        afterContent: "new\n",
      },
    ]));
    const impactPath = join(tmp, "impact.json");
    writeFileSync(impactPath, JSON.stringify({
      qualityDelta: 0.08,
      riskReduction: "Reduces verifier gaming.",
    }));

    const create = await runControlPlaneCommand(
      [
        "harness",
        "proposal",
        "create",
        "--finding",
        "finding-1",
        "--surface",
        "prompt",
        "--summary",
        "Tighten verifier-facing prompt.",
        "--patches",
        patchPath,
        "--expected-impact",
        impactPath,
        "--rollback",
        "Revert prompt patch if heldout quality drops.",
        "--output",
        "json",
      ],
      { cwd: tmp, now: () => "2026-05-13T12:00:00.000Z" },
    );
    expect(create.exitCode).toBe(EXIT.PASS_STRONG_OR_MODERATE);
    const created = JSON.parse(create.stdout);
    expect(created.status).toBe("proposed");
    expect(created.findingIds).toEqual(["finding-1"]);

    const list = await runControlPlaneCommand(
      ["harness", "proposal", "list", "--output", "json"],
      { cwd: tmp },
    );
    expect(JSON.parse(list.stdout)).toEqual([
      expect.objectContaining({
        id: created.id,
        targetSurface: "prompt",
        status: "proposed",
      }),
    ]);

    const candidateId = await registerPayload("candidate");
    const baselineId = await registerPayload("baseline");
    await attachMetrics(candidateId, "candidate-heldout", 0.88);
    await attachMetrics(baselineId, "baseline-heldout", 0.70);

    const decide = await runControlPlaneCommand(
      [
        "harness",
        "proposal",
        "decide",
        created.id,
        "--candidate",
        candidateId,
        "--baseline",
        baselineId,
        "--validation",
        "heldout",
        "--suite",
        "heldout-suite",
        "--evidence-ref",
        "runs/heldout/candidate-heldout.json",
        "--output",
        "json",
      ],
      { cwd: tmp, now: () => "2026-05-13T12:10:00.000Z" },
    );

    expect(decide.exitCode).toBe(EXIT.PASS_STRONG_OR_MODERATE);
    const decided = JSON.parse(decide.stdout);
    expect(decided.status).toBe("accepted");
    expect(decided.decision.promotionDecision.pass).toBe(true);

    const show = await runControlPlaneCommand(
      ["harness", "proposal", "show", created.id, "--output", "json"],
      { cwd: tmp },
    );
    expect(JSON.parse(show.stdout).decision.status).toBe("accepted");
  });

  test("dev-only evidence is inconclusive and exits marginal", async () => {
    const patchPath = join(tmp, "patches.json");
    writeFileSync(patchPath, JSON.stringify([
      {
        filePath: "agents/grid_ctf/prompts/competitor.txt",
        operation: "modify",
        unifiedDiff: "--- a/competitor.txt\n+++ b/competitor.txt\n@@ -1 +1 @@\n-old\n+new\n",
      },
    ]));
    const create = await runControlPlaneCommand(
      [
        "harness",
        "proposal",
        "create",
        "--finding",
        "finding-1",
        "--surface",
        "prompt",
        "--summary",
        "Tighten prompt.",
        "--patches",
        patchPath,
        "--rollback",
        "Revert if dev-only signal fails on heldout.",
        "--output",
        "json",
      ],
      { cwd: tmp, now: () => "2026-05-13T12:00:00.000Z" },
    );
    const created = JSON.parse(create.stdout);
    const candidateId = await registerPayload("candidate");
    const baselineId = await registerPayload("baseline");
    await attachMetrics(candidateId, "candidate-dev", 0.88, "dev-suite");
    await attachMetrics(baselineId, "baseline-dev", 0.70, "dev-suite");

    const decide = await runControlPlaneCommand(
      [
        "harness",
        "proposal",
        "decide",
        created.id,
        "--candidate",
        candidateId,
        "--baseline",
        baselineId,
        "--validation",
        "dev",
        "--suite",
        "dev-suite",
        "--output",
        "json",
      ],
      { cwd: tmp, now: () => "2026-05-13T12:10:00.000Z" },
    );

    expect(decide.exitCode).toBe(EXIT.MARGINAL);
    expect(JSON.parse(decide.stdout).decision.status).toBe("inconclusive");
  });

  test("requires EvalRun evidence for the requested validation suite", async () => {
    const patchPath = join(tmp, "patches.json");
    writeFileSync(patchPath, JSON.stringify([
      {
        filePath: "agents/grid_ctf/prompts/competitor.txt",
        operation: "modify",
        unifiedDiff: "--- a/competitor.txt\n+++ b/competitor.txt\n@@ -1 +1 @@\n-old\n+new\n",
      },
    ]));
    const create = await runControlPlaneCommand(
      [
        "harness",
        "proposal",
        "create",
        "--finding",
        "finding-1",
        "--surface",
        "prompt",
        "--summary",
        "Tighten prompt.",
        "--patches",
        patchPath,
        "--rollback",
        "Revert if heldout evidence is missing.",
        "--output",
        "json",
      ],
      { cwd: tmp, now: () => "2026-05-13T12:00:00.000Z" },
    );
    const created = JSON.parse(create.stdout);
    const candidateId = await registerPayload("candidate");
    const baselineId = await registerPayload("baseline");
    await attachMetrics(candidateId, "candidate-dev", 0.88, "dev-suite");
    await attachMetrics(baselineId, "baseline-heldout", 0.70, "heldout-suite");

    const decide = await runControlPlaneCommand(
      [
        "harness",
        "proposal",
        "decide",
        created.id,
        "--candidate",
        candidateId,
        "--baseline",
        baselineId,
        "--validation",
        "heldout",
        "--suite",
        "heldout-suite",
        "--output",
        "json",
      ],
      { cwd: tmp, now: () => "2026-05-13T12:10:00.000Z" },
    );

    expect(decide.exitCode).toBe(EXIT.MISSING_BASELINE);
    expect(decide.stderr).toContain("has no EvalRuns for suite heldout-suite");
  });

  test("rejects malformed expected impact as validation failure", async () => {
    const patchPath = join(tmp, "patches.json");
    writeFileSync(patchPath, JSON.stringify([
      {
        filePath: "agents/grid_ctf/prompts/competitor.txt",
        operation: "modify",
        unifiedDiff: "--- a/competitor.txt\n+++ b/competitor.txt\n@@ -1 +1 @@\n-old\n+new\n",
      },
    ]));
    const impactPath = join(tmp, "impact.json");
    writeFileSync(impactPath, JSON.stringify({ unsupported: true }));

    const create = await runControlPlaneCommand(
      [
        "harness",
        "proposal",
        "create",
        "--finding",
        "finding-1",
        "--surface",
        "prompt",
        "--summary",
        "Tighten prompt.",
        "--patches",
        patchPath,
        "--expected-impact",
        impactPath,
        "--rollback",
        "Revert if validation rejects the shape.",
        "--output",
        "json",
      ],
      { cwd: tmp, now: () => "2026-05-13T12:00:00.000Z" },
    );

    expect(create.exitCode).toBe(EXIT.VALIDATION_FAILED);
    expect(create.stderr).toContain("invalid HarnessChangeProposal");
  });
});
