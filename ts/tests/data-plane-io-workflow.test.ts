import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import {
  buildCompletedDataPlaneResult,
  buildDataPlaneStatus,
  buildFailedDataPlaneResult,
  loadTraceEntries,
  summarizeDataPlaneSources,
  toShareGptTraceRow,
  writeCuratedDatasetArtifacts,
} from "../src/traces/data-plane-io-workflow.js";
import type { CuratedDataset, TraceEntry } from "../src/traces/data-plane-types.js";
import { SCHEMA_VERSION } from "../src/traces/public-schema.js";

let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac-data-plane-io-"));
});

afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

function makeTraceEntry(id: string, score = 0.9, sourceHarness = "autocontext"): TraceEntry {
  return {
    trace: {
      schemaVersion: SCHEMA_VERSION,
      traceId: id,
      sourceHarness,
      collectedAt: "2026-03-27T10:00:00Z",
      messages: [
        { role: "user", content: `Task ${id}`, timestamp: "2026-03-27T10:00:01Z" },
        { role: "assistant", content: `Solution ${id}`, timestamp: "2026-03-27T10:00:02Z" },
      ],
      outcome: { score, reasoning: "ok", dimensions: {} },
    },
    manifest: {
      schemaVersion: SCHEMA_VERSION,
      sourceHarness,
      collectionMethod: "automated",
      license: "CC-BY-4.0",
      traceCount: 1,
      createdAt: "2026-03-27T10:00:00Z",
    },
    attestation: {
      schemaVersion: SCHEMA_VERSION,
      submitterId: "user",
      consentGiven: true,
      dataOrigin: "own_work",
      allowRedistribution: true,
      allowTraining: true,
      attestedAt: "2026-03-27T10:00:00Z",
    },
  };
}

describe("data-plane io workflow", () => {
  it("loads valid trace entries, skips malformed files, and converts ShareGPT rows", () => {
    const traceDir = join(tmpDir, "traces");
    mkdirSync(traceDir, { recursive: true });
    writeFileSync(join(traceDir, "trace_1.json"), JSON.stringify(makeTraceEntry("trace_1")), "utf-8");
    writeFileSync(join(traceDir, "broken.json"), "{not valid json", "utf-8");

    const loaded = loadTraceEntries(traceDir);
    expect(loaded).toHaveLength(1);
    expect(loaded[0]?.trace.traceId).toBe("trace_1");

    expect(toShareGptTraceRow(makeTraceEntry("trace_row").trace)).toMatchObject({
      conversations: [
        { from: "human", value: "Task trace_row" },
        { from: "gpt", value: "Solution trace_row" },
      ],
      metadata: {
        traceId: "trace_row",
        sourceHarness: "autocontext",
        score: 0.9,
      },
    });
  });

  it("writes curated dataset artifacts and derives build/status results", () => {
    const dataset: CuratedDataset = {
      included: [makeTraceEntry("t1", 0.8, "autocontext"), makeTraceEntry("t2", 0.9, "hermes")],
      excluded: [makeTraceEntry("t3", 0.2, "autocontext")],
      train: [makeTraceEntry("t1", 0.8, "autocontext")],
      heldOut: [makeTraceEntry("t2", 0.9, "hermes")],
    };

    const outputDir = join(tmpDir, "dataset");
    const { manifest } = writeCuratedDatasetArtifacts({
      outputDir,
      dataset,
      curationPolicy: { minScore: 0.5, heldOutRatio: 0.5 },
    });

    expect(existsSync(join(outputDir, "train.jsonl"))).toBe(true);
    expect(existsSync(join(outputDir, "held_out.jsonl"))).toBe(true);
    expect(existsSync(join(outputDir, "manifest.json"))).toBe(true);
    expect(JSON.parse(readFileSync(join(outputDir, "manifest.json"), "utf-8"))).toMatchObject({
      totalTraces: 3,
      includedTraces: 2,
      excludedTraces: 1,
      trainSize: 1,
      heldOutSize: 1,
      sources: { autocontext: 1, hermes: 1 },
      curationPolicy: { minScore: 0.5, heldOutRatio: 0.5 },
    });

    expect(summarizeDataPlaneSources(dataset.included)).toEqual({ autocontext: 1, hermes: 1 });
    expect(buildCompletedDataPlaneResult(outputDir, manifest)).toMatchObject({
      status: "completed",
      totalTraces: 3,
      trainSize: 1,
      heldOutSize: 1,
      outputDir,
    });
    expect(buildFailedDataPlaneResult(outputDir, new Error("boom"))).toMatchObject({
      status: "failed",
      error: "boom",
      outputDir,
    });
    expect(buildDataPlaneStatus(outputDir, buildCompletedDataPlaneResult(outputDir, manifest))).toEqual({
      totalTraces: 3,
      includedTraces: 2,
      trainSize: 1,
      heldOutSize: 1,
      outputDir,
      built: true,
    });
  });
});
