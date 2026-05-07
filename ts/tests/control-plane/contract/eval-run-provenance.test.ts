import { describe, expect, test } from "vitest";
import { createEvalRun } from "../../../src/control-plane/contract/factories.js";
import { validateEvalRun } from "../../../src/control-plane/contract/validators.js";
import type {
  ArtifactId,
  ContentHash,
  SuiteId,
} from "../../../src/control-plane/contract/branded-ids.js";
import type { MetricBundle } from "../../../src/control-plane/contract/types.js";

const metrics: MetricBundle = {
  quality: { score: 0.5, sampleSize: 20 },
  cost: { tokensIn: 100, tokensOut: 50 },
  latency: { p50Ms: 100, p95Ms: 200, p99Ms: 300 },
  safety: { regressions: [] },
  evalRunnerIdentity: {
    name: "external-benchmark",
    version: "2.0.0",
    configHash: `sha256:${"a".repeat(64)}` as ContentHash,
  },
};

describe("EvalRun provenance and integrity metadata", () => {
  test("accepts adapter provenance, web policy, trials, reconciliation, and memory pack refs", () => {
    const run = createEvalRun({
      runId: "external_eval_1",
      artifactId: "01KPEYB3BRQWK2WSHK9E93N6NP" as ArtifactId,
      suiteId: "external-eval" as SuiteId,
      metrics,
      datasetProvenance: {
        datasetId: "heldout-slice",
        sliceHash: `sha256:${"b".repeat(64)}` as ContentHash,
        sampleCount: 20,
      },
      ingestedAt: "2026-05-06T19:00:00.000Z",
      adapterProvenance: {
        provider: "codex",
        model: "gpt-5.5",
        reasoningEffort: "high",
        promptTemplateHash: `sha256:${"c".repeat(64)}` as ContentHash,
        webPolicy: "disabled",
        integrityMode: "external-eval",
      },
      integrity: {
        status: "clean",
        notes: ["web search disabled"],
      },
      trials: [
        {
          taskId: "task-a",
          trialId: "task-a-1",
          attempt: 1,
          status: "passed",
          reward: 1,
        },
      ],
      reconciliation: {
        view: "first-completed-per-task",
        score: 1,
        selectedTrialIdsByTask: { "task-a": "task-a-1" },
        ignoredTrialIds: [],
        unresolvedTaskIds: [],
        counts: {
          taskCount: 1,
          selectedTaskCount: 1,
          passed: 1,
          failed: 0,
          infrastructureErrors: 0,
          cancelled: 0,
          discarded: 0,
          duplicatesIgnored: 0,
        },
      },
      memoryPacks: [
        {
          packId: "terminal-ops-v1",
          version: "1.0.0",
          contentHash: `sha256:${"d".repeat(64)}` as ContentHash,
        },
      ],
    });

    expect(validateEvalRun(run)).toEqual({ valid: true });
  });

  test("rejects unknown web policy values", () => {
    const run = createEvalRun({
      runId: "external_eval_2",
      artifactId: "01KPEYB3BRQWK2WSHK9E93N6NP" as ArtifactId,
      suiteId: "external-eval" as SuiteId,
      metrics,
      datasetProvenance: {
        datasetId: "heldout-slice",
        sliceHash: `sha256:${"b".repeat(64)}` as ContentHash,
        sampleCount: 20,
      },
      ingestedAt: "2026-05-06T19:00:00.000Z",
      adapterProvenance: {
        provider: "codex",
        model: "gpt-5.5",
        webPolicy: "answer-seeking" as never,
        integrityMode: "external-eval",
      },
    });

    expect(validateEvalRun(run)).toMatchObject({ valid: false });
  });
});
