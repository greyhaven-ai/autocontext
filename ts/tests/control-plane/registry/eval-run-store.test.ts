import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  saveEvalRun,
  loadEvalRun,
  listEvalRunIds,
} from "../../../src/control-plane/registry/eval-run-store.js";
import { createEvalRun } from "../../../src/control-plane/contract/factories.js";
import type { ArtifactId } from "../../../src/control-plane/contract/branded-ids.js";
import type { EvalRun, MetricBundle } from "../../../src/control-plane/contract/types.js";

const aMetrics: MetricBundle = {
  quality: { score: 0.85, sampleSize: 250 },
  cost: { tokensIn: 1000, tokensOut: 500 },
  latency: { p50Ms: 100, p95Ms: 200, p99Ms: 300 },
  safety: { regressions: [] },
  evalRunnerIdentity: {
    name: "my-eval",
    version: "1.0.0",
    configHash: "sha256:" + "f".repeat(64),
  },
};

const ARTIFACT_ID = "01KPEYB3BRQWK2WSHK9E93N6NP" as ArtifactId;

function makeRun(runId: string): EvalRun {
  return createEvalRun({
    runId,
    artifactId: ARTIFACT_ID,
    suiteId: "prod-eval-v3" as any,
    metrics: aMetrics,
    datasetProvenance: {
      datasetId: "ds-1",
      sliceHash: "sha256:" + "a".repeat(64),
      sampleCount: 250,
    },
    ingestedAt: "2026-04-17T12:05:00.000Z",
  });
}

describe("eval-run-store", () => {
  let artifactDir: string;

  beforeEach(() => {
    artifactDir = mkdtempSync(join(tmpdir(), "autocontext-eval-runs-"));
  });

  afterEach(() => {
    rmSync(artifactDir, { recursive: true, force: true });
  });

  test("saveEvalRun writes <artifactDir>/eval-runs/<runId>.json", () => {
    const run = makeRun("eval_1");
    saveEvalRun(artifactDir, run);
    expect(existsSync(join(artifactDir, "eval-runs", "eval_1.json"))).toBe(true);
  });

  test("round-trip: saveEvalRun then loadEvalRun returns the same object", () => {
    const run = makeRun("eval_2");
    saveEvalRun(artifactDir, run);
    const back = loadEvalRun(artifactDir, "eval_2");
    expect(back).toEqual(run);
  });

  test("loadEvalRun throws when the runId is unknown", () => {
    expect(() => loadEvalRun(artifactDir, "missing")).toThrow(/not found/i);
  });

  test("listEvalRunIds enumerates all written runs", () => {
    saveEvalRun(artifactDir, makeRun("a"));
    saveEvalRun(artifactDir, makeRun("b"));
    saveEvalRun(artifactDir, makeRun("c"));
    expect(listEvalRunIds(artifactDir).sort()).toEqual(["a", "b", "c"]);
  });

  test("listEvalRunIds returns [] when the eval-runs dir does not exist", () => {
    expect(listEvalRunIds(artifactDir)).toEqual([]);
  });

  test("loadEvalRun rejects malformed stored JSON", () => {
    const dst = join(artifactDir, "eval-runs");
    mkdirSync(dst, { recursive: true });
    writeFileSync(join(dst, "bad.json"), "not json");
    expect(() => loadEvalRun(artifactDir, "bad")).toThrow();
  });

  test("saveEvalRun rejects an EvalRun that fails schema validation", () => {
    const bogus = { ...makeRun("z"), metrics: undefined } as unknown as EvalRun;
    expect(() => saveEvalRun(artifactDir, bogus)).toThrow();
  });
});
