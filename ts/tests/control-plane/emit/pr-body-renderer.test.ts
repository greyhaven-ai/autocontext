import { describe, test, expect } from "vitest";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { renderPrBody } from "../../../src/control-plane/emit/pr-body-renderer.js";
import { createArtifact, createEvalRun } from "../../../src/control-plane/contract/factories.js";
import type { ArtifactId, ContentHash, Scenario, SuiteId } from "../../../src/control-plane/contract/branded-ids.js";
import type {
  Artifact,
  EvalRun,
  MetricBundle,
  PromotionDecision,
  PromotionThresholds,
  Provenance,
} from "../../../src/control-plane/contract/types.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const GOLDEN_DIR = join(__dirname, "golden", "pr-bodies");
const UPDATE = process.env.UPDATE_GOLDEN === "1";

function readGolden(name: string): string {
  const p = join(GOLDEN_DIR, `${name}.md`);
  if (!existsSync(p)) {
    // Create on first run so the author can review + commit the generated
    // golden. This still fails the test run — the author must acknowledge the
    // new file by committing it (subsequent runs will compare against the
    // recorded bytes).
    throw new Error(
      `Golden file missing: ${p}. Run tests with UPDATE_GOLDEN=1 to create it, `
      + `then review the output and commit.`,
    );
  }
  return readFileSync(p, "utf-8");
}

function writeGolden(name: string, content: string): void {
  writeFileSync(join(GOLDEN_DIR, `${name}.md`), content, "utf-8");
}

function assertMatchesGolden(name: string, actual: string): void {
  if (UPDATE) {
    writeGolden(name, actual);
    return;
  }
  const expected = readGolden(name);
  // Produce a helpful diff preview on mismatch rather than just the bare
  // expect() output — golden bodies run ~50+ lines.
  if (expected !== actual) {
    const msg = `Golden mismatch for ${name}.\n\n--- expected\n+++ actual\n`
      + diffPreview(expected, actual);
    throw new Error(msg);
  }
  expect(actual).toBe(expected);
}

function diffPreview(a: string, b: string): string {
  const al = a.split("\n");
  const bl = b.split("\n");
  const max = Math.max(al.length, bl.length);
  const out: string[] = [];
  for (let i = 0; i < max; i++) {
    if (al[i] !== bl[i]) {
      out.push(`L${i + 1}`);
      out.push(`- ${al[i] ?? "<eof>"}`);
      out.push(`+ ${bl[i] ?? "<eof>"}`);
    }
  }
  return out.slice(0, 80).join("\n");
}

// ---- Fixtures ----

const CAND_ID = "01HZCANDIDATE00000000AAAAA" as ArtifactId;
const BASE_ID = "01HZBASELINE000000000AAAAA" as ArtifactId;
const SUITE_ID = "suite-prompt-quality-v1" as SuiteId;

const provHuman: Provenance = {
  authorType: "human",
  authorId: "jay@greyhaven.ai",
  parentArtifactIds: [BASE_ID],
  createdAt: "2026-04-17T00:00:00.000Z",
};

const provRoot: Provenance = {
  authorType: "autocontext-run",
  authorId: "run_01HZXYZ",
  parentArtifactIds: [],
  createdAt: "2026-04-17T00:00:00.000Z",
};

const CONFIG_HASH = "sha256:cfg0000000000000000000000000000000000000000000000000000000000" as ContentHash;
const SLICE_HASH = "sha256:sli0000000000000000000000000000000000000000000000000000000000" as ContentHash;
const PAYLOAD_HASH = "sha256:pl000000000000000000000000000000000000000000000000000000000000" as ContentHash;

function metricBundle(overrides: Partial<MetricBundle> = {}): MetricBundle {
  const base: MetricBundle = {
    quality: { score: 0.85, sampleSize: 500 },
    cost: { tokensIn: 100, tokensOut: 200 },
    latency: { p50Ms: 400, p95Ms: 900, p99Ms: 1200 },
    safety: { regressions: [] },
    evalRunnerIdentity: {
      name: "autocontext-eval",
      version: "1.0.0",
      configHash: CONFIG_HASH,
    },
  };
  return { ...base, ...overrides };
}

function mkEvalRun(artifactId: ArtifactId, metrics: MetricBundle): EvalRun {
  return createEvalRun({
    runId: `run-of-${artifactId.slice(-8)}`,
    artifactId,
    suiteId: SUITE_ID,
    metrics,
    datasetProvenance: {
      datasetId: "prod-traffic-2026-04-10",
      sliceHash: SLICE_HASH,
      sampleCount: metrics.quality.sampleSize,
    },
    ingestedAt: "2026-04-17T00:00:00.000Z",
  });
}

function mkArtifact(
  id: ArtifactId,
  prov: Provenance,
  overrides: Partial<Artifact> = {},
): Artifact {
  const base = createArtifact({
    id,
    actuatorType: "prompt-patch",
    scenario: "grid_ctf" as Scenario,
    payloadHash: PAYLOAD_HASH,
    provenance: prov,
  });
  return { ...base, ...overrides };
}

const defaultThresholds: PromotionThresholds = {
  qualityMinDelta: 0.02,
  costMaxRelativeIncrease: 0.1,
  latencyMaxRelativeIncrease: 0.1,
  strongConfidenceMin: 0.9,
  moderateConfidenceMin: 0.7,
  strongQualityMultiplier: 2.0,
};

function mkDecision(overrides: Partial<PromotionDecision>): PromotionDecision {
  const base: PromotionDecision = {
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
    thresholds: defaultThresholds,
    reasoning: "Pass: quality Δ=0.100 OK, cost OK, latency OK, confidence=0.70.",
    evaluatedAt: "2026-04-17T12:00:00.000Z",
  };
  return { ...base, ...overrides };
}

const FIXED_TIMESTAMP = "2026-04-17T12:00:00.000Z";
const FIXED_VERSION = "0.4.3";

// ---- Scenarios ----

describe("renderPrBody — golden files", () => {
  test("strong: active recommendation, high confidence", () => {
    const candidate = mkArtifact(CAND_ID, provHuman);
    const baseline = mkArtifact(BASE_ID, provRoot, { activationState: "active" });
    const metrics = metricBundle({ quality: { score: 0.92, sampleSize: 2000 } });
    const evalRun = mkEvalRun(CAND_ID, metrics);
    const decision = mkDecision({
      pass: true,
      recommendedTargetState: "active",
      confidence: 0.95,
      deltas: {
        quality: { baseline: 0.75, candidate: 0.92, delta: 0.17, passed: true },
        cost: {
          baseline: { tokensIn: 100, tokensOut: 200 },
          candidate: { tokensIn: 95, tokensOut: 195 },
          delta: { tokensIn: -5, tokensOut: -5 },
          passed: true,
        },
        latency: {
          baseline: { p50Ms: 400, p95Ms: 900, p99Ms: 1200 },
          candidate: { p50Ms: 380, p95Ms: 870, p99Ms: 1150 },
          delta: { p50Ms: -20, p95Ms: -30, p99Ms: -50 },
          passed: true,
        },
        safety: { regressions: [], passed: true },
      },
      reasoning: "Pass: quality Δ=0.170 OK, cost OK, latency OK, confidence=0.95.",
    });

    const body = renderPrBody({
      candidate,
      baseline,
      decision,
      evalRun,
      autocontextVersion: FIXED_VERSION,
      timestamp: FIXED_TIMESTAMP,
    });
    assertMatchesGolden("strong", body);
  });

  test("moderate: canary recommendation", () => {
    const candidate = mkArtifact(CAND_ID, provHuman);
    const baseline = mkArtifact(BASE_ID, provRoot, { activationState: "active" });
    const evalRun = mkEvalRun(CAND_ID, metricBundle());
    const decision = mkDecision({
      pass: true,
      recommendedTargetState: "canary",
      confidence: 0.75,
    });

    const body = renderPrBody({
      candidate,
      baseline,
      decision,
      evalRun,
      autocontextVersion: FIXED_VERSION,
      timestamp: FIXED_TIMESTAMP,
    });
    assertMatchesGolden("moderate", body);
  });

  test("marginal: shadow recommendation, low confidence", () => {
    const candidate = mkArtifact(CAND_ID, provHuman);
    const baseline = mkArtifact(BASE_ID, provRoot, { activationState: "active" });
    const metrics = metricBundle({ quality: { score: 0.77, sampleSize: 30 } });
    const evalRun = mkEvalRun(CAND_ID, metrics);
    const decision = mkDecision({
      pass: true,
      recommendedTargetState: "shadow",
      confidence: 0.4,
      deltas: {
        quality: { baseline: 0.75, candidate: 0.77, delta: 0.02, passed: true },
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
      reasoning: "Pass: quality Δ=0.020 OK, cost OK, latency OK, confidence=0.40.",
    });

    const body = renderPrBody({
      candidate,
      baseline,
      decision,
      evalRun,
      autocontextVersion: FIXED_VERSION,
      timestamp: FIXED_TIMESTAMP,
    });
    assertMatchesGolden("marginal", body);
  });

  test("hard-fail: safety regressions present", () => {
    const candidate = mkArtifact(CAND_ID, provHuman);
    const baseline = mkArtifact(BASE_ID, provRoot, { activationState: "active" });
    const metrics = metricBundle({
      safety: {
        regressions: [
          {
            id: "SAFE-001",
            severity: "major",
            description: "PII leak detected in 3 test samples",
            exampleRef: "eval/sample-42",
          },
        ],
      },
    });
    const evalRun = mkEvalRun(CAND_ID, metrics);
    const decision = mkDecision({
      pass: false,
      recommendedTargetState: "disabled",
      confidence: 0.7,
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
        safety: {
          regressions: [
            {
              id: "SAFE-001",
              severity: "major",
              description: "PII leak detected in 3 test samples",
              exampleRef: "eval/sample-42",
            },
          ],
          passed: false,
        },
      },
      reasoning: "Safety regressions present — rejected regardless of other dimensions.",
    });

    const body = renderPrBody({
      candidate,
      baseline,
      decision,
      evalRun,
      autocontextVersion: FIXED_VERSION,
      timestamp: FIXED_TIMESTAMP,
    });
    assertMatchesGolden("hard-fail", body);
  });

  test("no-incumbent: baseline is null → shadow", () => {
    const candidate = mkArtifact(CAND_ID, provRoot);
    const evalRun = mkEvalRun(CAND_ID, metricBundle());
    const decision = mkDecision({
      pass: true,
      recommendedTargetState: "shadow",
      confidence: 0.67,
      deltas: {
        quality: { baseline: 0, candidate: 0.85, delta: 0.85, passed: true },
        cost: {
          baseline: { tokensIn: 0, tokensOut: 0 },
          candidate: { tokensIn: 100, tokensOut: 200 },
          delta: { tokensIn: 100, tokensOut: 200 },
          passed: true,
        },
        latency: {
          baseline: { p50Ms: 0, p95Ms: 0, p99Ms: 0 },
          candidate: { p50Ms: 400, p95Ms: 900, p99Ms: 1200 },
          delta: { p50Ms: 400, p95Ms: 900, p99Ms: 1200 },
          passed: true,
        },
        safety: { regressions: [], passed: true },
      },
      reasoning: "No incumbent baseline; candidate gets shadow to enable future comparison.",
    });

    const body = renderPrBody({
      candidate,
      baseline: null,
      decision,
      evalRun,
      autocontextVersion: FIXED_VERSION,
      timestamp: FIXED_TIMESTAMP,
    });
    assertMatchesGolden("no-incumbent", body);
  });

  test("rollback: prior-active demoted, rollback PR to revert", () => {
    // A rollback PR conceptually swaps roles — the "candidate" is the
    // artifact we're rolling BACK TO, the "baseline" is the one being
    // demoted. The renderer doesn't need to care about the swap: the PR body
    // reflects the inputs it's given. We express rollback via the decision's
    // reasoning + recommendedTargetState.
    const priorActive = mkArtifact(CAND_ID, provHuman, { activationState: "candidate" });
    const beingDemoted = mkArtifact(BASE_ID, provRoot, { activationState: "active" });
    const evalRun = mkEvalRun(CAND_ID, metricBundle({ quality: { score: 0.9, sampleSize: 1500 } }));
    const decision = mkDecision({
      pass: true,
      recommendedTargetState: "active",
      confidence: 0.88,
      deltas: {
        quality: { baseline: 0.65, candidate: 0.9, delta: 0.25, passed: true },
        cost: {
          baseline: { tokensIn: 120, tokensOut: 240 },
          candidate: { tokensIn: 100, tokensOut: 200 },
          delta: { tokensIn: -20, tokensOut: -40 },
          passed: true,
        },
        latency: {
          baseline: { p50Ms: 450, p95Ms: 1000, p99Ms: 1400 },
          candidate: { p50Ms: 400, p95Ms: 900, p99Ms: 1200 },
          delta: { p50Ms: -50, p95Ms: -100, p99Ms: -200 },
          passed: true,
        },
        safety: { regressions: [], passed: true },
      },
      reasoning:
        "Rollback restoring prior-known-good artifact after regression in current active.",
    });

    const body = renderPrBody({
      candidate: priorActive,
      baseline: beingDemoted,
      decision,
      evalRun,
      autocontextVersion: FIXED_VERSION,
      timestamp: FIXED_TIMESTAMP,
    });
    assertMatchesGolden("rollback", body);
  });
});

describe("renderPrBody — determinism", () => {
  test("same inputs → byte-identical output", () => {
    const candidate = mkArtifact(CAND_ID, provHuman);
    const baseline = mkArtifact(BASE_ID, provRoot, { activationState: "active" });
    const evalRun = mkEvalRun(CAND_ID, metricBundle());
    const decision = mkDecision({});

    const body1 = renderPrBody({
      candidate,
      baseline,
      decision,
      evalRun,
      autocontextVersion: FIXED_VERSION,
      timestamp: FIXED_TIMESTAMP,
    });
    const body2 = renderPrBody({
      candidate,
      baseline,
      decision,
      evalRun,
      autocontextVersion: FIXED_VERSION,
      timestamp: FIXED_TIMESTAMP,
    });
    expect(body1).toBe(body2);
  });
});

describe("renderPrBody — required section headers (machine-parseable)", () => {
  test("emits all section headers per spec §9.4", () => {
    const candidate = mkArtifact(CAND_ID, provHuman);
    const baseline = mkArtifact(BASE_ID, provRoot, { activationState: "active" });
    const evalRun = mkEvalRun(CAND_ID, metricBundle());
    const decision = mkDecision({});

    const body = renderPrBody({
      candidate,
      baseline,
      decision,
      evalRun,
      autocontextVersion: FIXED_VERSION,
      timestamp: FIXED_TIMESTAMP,
    });
    expect(body).toContain("### Metric deltas");
    expect(body).toContain("### Dataset provenance");
    expect(body).toContain("### Rollback");
    expect(body).toContain("### Lineage");
    expect(body).toContain("### Audit");
  });
});
