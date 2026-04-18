import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync, existsSync, readFileSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runPatchOnlyMode } from "../../../../src/control-plane/emit/modes/patch-only.js";
import { defaultWorkspaceLayout } from "../../../../src/control-plane/emit/workspace-layout.js";
import type { Patch, PromotionDecision } from "../../../../src/control-plane/contract/types.js";
import type { ArtifactId } from "../../../../src/control-plane/contract/branded-ids.js";

let tmp: string;

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "autocontext-patch-only-"));
});

afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
});

const candidateId = "01HZCANDIDATE00000000AAAAA" as ArtifactId;
const TIMESTAMP = "2026-04-17T12:00:00.000Z";

const patch: Patch = {
  filePath: "agents/grid_ctf/prompts/01HZCANDIDATE00000000AAAAA-prompt-patch.txt",
  operation: "create",
  unifiedDiff: "--- a\n+++ b\n@@ @@\n+new\n",
  afterContent: "new\n",
};

const decision: PromotionDecision = {
  schemaVersion: "1.0",
  pass: true,
  recommendedTargetState: "canary",
  deltas: {
    quality: { baseline: 0.75, candidate: 0.85, delta: 0.1, passed: true },
    cost: {
      baseline: { tokensIn: 100, tokensOut: 200 },
      candidate: { tokensIn: 100, tokensOut: 200 },
      delta: { tokensIn: 0, tokensOut: 0 },
      passed: true,
    },
    latency: {
      baseline: { p50Ms: 400, p95Ms: 900, p99Ms: 1200 },
      candidate: { p50Ms: 400, p95Ms: 900, p99Ms: 1200 },
      delta: { p50Ms: 0, p95Ms: 0, p99Ms: 0 },
      passed: true,
    },
    safety: { regressions: [], passed: true },
  },
  confidence: 0.7,
  thresholds: {
    qualityMinDelta: 0.02,
    costMaxRelativeIncrease: 0.1,
    latencyMaxRelativeIncrease: 0.1,
    strongConfidenceMin: 0.9,
    moderateConfidenceMin: 0.7,
    strongQualityMultiplier: 2.0,
  },
  reasoning: "ok",
  evaluatedAt: TIMESTAMP,
};

describe("runPatchOnlyMode", () => {
  test("writes the expected dry-run directory layout per spec §9.5", async () => {
    const prBody = "## Autocontext candidate promotion\n...body...\n";
    const location = await runPatchOnlyMode({
      cwd: tmp,
      candidateId,
      timestamp: TIMESTAMP,
      patches: [patch],
      prBody,
      decision,
      layout: defaultWorkspaceLayout(),
      resolvedMode: "patch-only",
      preflightIssues: [],
      branchName: "autocontext/grid_ctf/prompt-patch/01HZCAND",
    });

    // <cwd>/.autocontext/dry-run-patches/<candidateId>/<timestamp>/
    const expectedRoot = join(
      tmp,
      ".autocontext",
      "dry-run-patches",
      candidateId,
      TIMESTAMP.replace(/[:.]/g, "-"),
    );
    expect(location).toBe(expectedRoot);
    expect(existsSync(expectedRoot)).toBe(true);

    // patches/<n>.<flattened-targetPath>.patch
    const patchesDir = join(expectedRoot, "patches");
    expect(existsSync(patchesDir)).toBe(true);
    const patchFiles = readdirSync(patchesDir);
    expect(patchFiles).toHaveLength(1);
    expect(patchFiles[0]!.startsWith("0.")).toBe(true);
    expect(patchFiles[0]!.endsWith(".patch")).toBe(true);
    expect(patchFiles[0]).toContain("prompt-patch.txt");

    expect(readFileSync(join(patchesDir, patchFiles[0]!), "utf-8")).toBe(patch.unifiedDiff);

    // pr-body.md
    expect(readFileSync(join(expectedRoot, "pr-body.md"), "utf-8")).toBe(prBody);

    // decision.json — canonical JSON of the PromotionDecision.
    const decisionRaw = readFileSync(join(expectedRoot, "decision.json"), "utf-8");
    const decisionParsed = JSON.parse(decisionRaw) as PromotionDecision;
    expect(decisionParsed.pass).toBe(true);
    expect(decisionParsed.recommendedTargetState).toBe("canary");

    // resolved-layout.json — the workspace layout fields captured for audit.
    const layoutRaw = readFileSync(join(expectedRoot, "resolved-layout.json"), "utf-8");
    const layoutParsed = JSON.parse(layoutRaw) as Record<string, string>;
    expect(layoutParsed.promptSubdir).toBe("prompts");

    // plan.json — operations + chosen mode + preflight.
    const planRaw = readFileSync(join(expectedRoot, "plan.json"), "utf-8");
    const plan = JSON.parse(planRaw) as {
      mode: string;
      branchName: string;
      patches: Array<{ filePath: string; operation: string }>;
      preflightIssues: Array<{ code: number; message: string }>;
    };
    expect(plan.mode).toBe("patch-only");
    expect(plan.branchName).toBe("autocontext/grid_ctf/prompt-patch/01HZCAND");
    expect(plan.patches).toHaveLength(1);
    expect(plan.patches[0]!.filePath).toBe(patch.filePath);
    expect(plan.patches[0]!.operation).toBe("create");
    expect(plan.preflightIssues).toEqual([]);
  });

  test("is idempotent for the same inputs (byte-identical dry-run bundle)", async () => {
    const prBody = "body\n";
    const layout = defaultWorkspaceLayout();
    const p = async () =>
      runPatchOnlyMode({
        cwd: tmp,
        candidateId,
        timestamp: TIMESTAMP,
        patches: [patch],
        prBody,
        decision,
        layout,
        resolvedMode: "patch-only",
        preflightIssues: [],
        branchName: "autocontext/grid_ctf/prompt-patch/01HZCAND",
      });

    const loc1 = await p();
    const bytes1 = readFileSync(join(loc1, "pr-body.md"), "utf-8")
      + "|" + readFileSync(join(loc1, "decision.json"), "utf-8")
      + "|" + readFileSync(join(loc1, "plan.json"), "utf-8");
    const loc2 = await p();
    const bytes2 = readFileSync(join(loc2, "pr-body.md"), "utf-8")
      + "|" + readFileSync(join(loc2, "decision.json"), "utf-8")
      + "|" + readFileSync(join(loc2, "plan.json"), "utf-8");
    expect(loc1).toBe(loc2);
    expect(bytes1).toBe(bytes2);
  });
});
