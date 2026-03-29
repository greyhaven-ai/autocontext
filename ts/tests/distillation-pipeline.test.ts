/**
 * AC-458: Curated distillation dataset pipeline.
 *
 * Tests richer curation policies, provenance, failure-example handling,
 * and external corpus mixing beyond the basic DatasetCurator.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync, existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  DistillationPipeline,
  type DistillationPolicy,
  type DistillationManifest,
  type DistillationResult,
} from "../src/traces/distillation-pipeline.js";
import { SCHEMA_VERSION } from "../src/traces/public-schema.js";

let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac-458-test-"));
});
afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

function seedTraces(dir: string, traces: Array<{
  id: string; score: number; family?: string; gate?: string;
  source?: string; createdAt?: string;
}>) {
  mkdirSync(dir, { recursive: true });
  for (const t of traces) {
    writeFileSync(join(dir, `${t.id}.json`), JSON.stringify({
      trace: {
        schemaVersion: SCHEMA_VERSION,
        traceId: t.id,
        sourceHarness: t.source ?? "autocontext",
        collectedAt: t.createdAt ?? "2026-03-27T10:00:00Z",
        messages: [
          { role: "user", content: `Task ${t.id}`, timestamp: "2026-03-27T10:00:00Z" },
          { role: "assistant", content: `Solution ${t.id}`, timestamp: "2026-03-27T10:00:01Z" },
        ],
        outcome: { score: t.score, reasoning: "ok", dimensions: {} },
        metadata: { family: t.family ?? "agent_task", gateDecision: t.gate ?? "advance" },
      },
      manifest: {
        schemaVersion: SCHEMA_VERSION, sourceHarness: t.source ?? "autocontext",
        collectionMethod: "automated", license: "CC-BY-4.0", traceCount: 1,
        createdAt: t.createdAt ?? "2026-03-27T10:00:00Z",
      },
      attestation: {
        submitterId: "user", consentGiven: true, dataOrigin: "own_work",
        allowRedistribution: true, allowTraining: true,
        attestedAt: "2026-03-27T10:00:00Z",
      },
    }), "utf-8");
  }
}

// ---------------------------------------------------------------------------
// Gate-based filtering
// ---------------------------------------------------------------------------

describe("gate-based filtering", () => {
  it("includes only advance-gated traces when advanceOnly is set", () => {
    const traceDir = join(tmpDir, "traces");
    seedTraces(traceDir, [
      { id: "t1", score: 0.9, gate: "advance" },
      { id: "t2", score: 0.8, gate: "retry" },
      { id: "t3", score: 0.7, gate: "advance" },
      { id: "t4", score: 0.6, gate: "rollback" },
    ]);

    const pipeline = new DistillationPipeline({
      traceDir,
      outputDir: join(tmpDir, "out"),
      policy: { advanceOnly: true },
    });
    const result = pipeline.build();

    expect(result.includedTraces).toBe(2); // t1, t3
  });
});

// ---------------------------------------------------------------------------
// Top-quartile selection
// ---------------------------------------------------------------------------

describe("top-quartile selection", () => {
  it("selects only top quartile when topQuartile is set", () => {
    const traceDir = join(tmpDir, "traces");
    seedTraces(traceDir, [
      { id: "t1", score: 0.3 },
      { id: "t2", score: 0.5 },
      { id: "t3", score: 0.7 },
      { id: "t4", score: 0.9 },
      { id: "t5", score: 0.4 },
      { id: "t6", score: 0.6 },
      { id: "t7", score: 0.8 },
      { id: "t8", score: 0.95 },
    ]);

    const pipeline = new DistillationPipeline({
      traceDir,
      outputDir: join(tmpDir, "out"),
      policy: { topQuartile: true },
    });
    const result = pipeline.build();

    // Top quartile of 8 = top 2
    expect(result.includedTraces).toBe(2);
  });
});

// ---------------------------------------------------------------------------
// Scenario-family filtering
// ---------------------------------------------------------------------------

describe("scenario-family filtering", () => {
  it("filters by family when familyFilter is set", () => {
    const traceDir = join(tmpDir, "traces");
    seedTraces(traceDir, [
      { id: "t1", score: 0.9, family: "simulation" },
      { id: "t2", score: 0.8, family: "agent_task" },
      { id: "t3", score: 0.7, family: "simulation" },
      { id: "t4", score: 0.6, family: "investigation" },
    ]);

    const pipeline = new DistillationPipeline({
      traceDir,
      outputDir: join(tmpDir, "out"),
      policy: { familyFilter: ["simulation"] },
    });
    const result = pipeline.build();

    expect(result.includedTraces).toBe(2); // t1, t3
  });
});

// ---------------------------------------------------------------------------
// Failure-example policy
// ---------------------------------------------------------------------------

describe("failure-example policy", () => {
  it("excludes failures by default", () => {
    const traceDir = join(tmpDir, "traces");
    seedTraces(traceDir, [
      { id: "t1", score: 0.9 },
      { id: "t2", score: 0.2 },
    ]);

    const pipeline = new DistillationPipeline({
      traceDir,
      outputDir: join(tmpDir, "out"),
      policy: { minScore: 0.5, failurePolicy: "exclude" },
    });
    const result = pipeline.build();

    expect(result.includedTraces).toBe(1);
  });

  it("routes failures to eval-only split when failurePolicy is eval_only", () => {
    const traceDir = join(tmpDir, "traces");
    seedTraces(traceDir, [
      { id: "t1", score: 0.9 },
      { id: "t2", score: 0.2 },
    ]);

    const pipeline = new DistillationPipeline({
      traceDir,
      outputDir: join(tmpDir, "out"),
      policy: { minScore: 0.5, failurePolicy: "eval_only" },
    });
    const result = pipeline.build();

    expect(result.includedTraces).toBe(1);
    expect(result.evalOnlyTraces).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// Distillation manifest
// ---------------------------------------------------------------------------

describe("distillation manifest", () => {
  it("records curation policy in manifest", () => {
    const traceDir = join(tmpDir, "traces");
    seedTraces(traceDir, [{ id: "t1", score: 0.9 }]);

    const pipeline = new DistillationPipeline({
      traceDir,
      outputDir: join(tmpDir, "out"),
      policy: { minScore: 0.7, advanceOnly: true, heldOutRatio: 0.1 },
    });
    pipeline.build();

    const manifest = JSON.parse(readFileSync(join(tmpDir, "out", "manifest.json"), "utf-8")) as DistillationManifest;
    expect(manifest.curationPolicy.minScore).toBe(0.7);
    expect(manifest.curationPolicy.advanceOnly).toBe(true);
    expect(manifest.curationPolicy.heldOutRatio).toBe(0.1);
  });

  it("records source provenance per trace", () => {
    const traceDir = join(tmpDir, "traces");
    seedTraces(traceDir, [
      { id: "t1", score: 0.9, source: "autocontext" },
      { id: "t2", score: 0.8, source: "hermes" },
    ]);

    const pipeline = new DistillationPipeline({
      traceDir,
      outputDir: join(tmpDir, "out"),
    });
    pipeline.build();

    const manifest = JSON.parse(readFileSync(join(tmpDir, "out", "manifest.json"), "utf-8")) as DistillationManifest;
    expect(manifest.sources["autocontext"]).toBe(1);
    expect(manifest.sources["hermes"]).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// Result shape
// ---------------------------------------------------------------------------

describe("DistillationResult shape", () => {
  it("has all required fields", () => {
    const traceDir = join(tmpDir, "traces");
    seedTraces(traceDir, [{ id: "t1", score: 0.9 }]);

    const pipeline = new DistillationPipeline({
      traceDir,
      outputDir: join(tmpDir, "out"),
    });
    const result: DistillationResult = pipeline.build();

    expect(result).toHaveProperty("status");
    expect(result).toHaveProperty("totalTraces");
    expect(result).toHaveProperty("includedTraces");
    expect(result).toHaveProperty("trainSize");
    expect(result).toHaveProperty("heldOutSize");
    expect(result).toHaveProperty("evalOnlyTraces");
    expect(result).toHaveProperty("outputDir");
  });
});
