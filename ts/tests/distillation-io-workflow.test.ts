import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import {
  buildDistillationManifest,
  ensureDistillationOutputDir,
  loadDistillationEntries,
  toShareGPT,
  writeDistillationJsonl,
  writeDistillationManifest,
} from "../src/traces/distillation-io-workflow.js";
import type { TraceEntry } from "../src/traces/distillation-types.js";
import { SCHEMA_VERSION } from "../src/traces/public-schema.js";

let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac-distillation-io-"));
});

afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

function makeTraceEntry(id: string, score = 0.9): TraceEntry {
  return {
    trace: {
      schemaVersion: SCHEMA_VERSION,
      traceId: id,
      sourceHarness: "autocontext",
      collectedAt: "2026-03-27T10:00:00Z",
      messages: [
        { role: "user", content: `Task ${id}`, timestamp: "2026-03-27T10:00:00Z" },
        { role: "assistant", content: `Solution ${id}`, timestamp: "2026-03-27T10:00:01Z" },
      ],
      outcome: { score, reasoning: "ok", dimensions: {} },
      metadata: { family: "agent_task", gateDecision: "advance" },
    },
    manifest: {
      schemaVersion: SCHEMA_VERSION,
      sourceHarness: "autocontext",
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

describe("distillation io workflow", () => {
  it("loads valid entries and reports malformed files as warnings", () => {
    const traceDir = join(tmpDir, "traces");
    mkdirSync(traceDir, { recursive: true });
    writeFileSync(join(traceDir, "valid.json"), JSON.stringify(makeTraceEntry("t1")), "utf-8");
    writeFileSync(join(traceDir, "broken.json"), "{not valid json", "utf-8");
    writeFileSync(join(traceDir, "missing.json"), JSON.stringify({ trace: { traceId: "oops" } }), "utf-8");

    const result = loadDistillationEntries(traceDir);
    expect(result.entries).toHaveLength(1);
    expect(result.entries[0].trace.traceId).toBe("t1");
    expect(result.warnings).toHaveLength(2);
    expect(result.warnings.some((warning) => warning.includes("broken.json"))).toBe(true);
    expect(result.warnings.some((warning) => warning.includes("missing.json"))).toBe(true);
  });

  it("writes ShareGPT JSONL rows and manifest files", () => {
    const outputDir = join(tmpDir, "out");
    ensureDistillationOutputDir(outputDir);
    expect(existsSync(outputDir)).toBe(true);

    const shareGpt = toShareGPT(makeTraceEntry("t1").trace, { examplePolicy: "contrastive" });
    expect(shareGpt).toMatchObject({
      conversations: [
        { from: "human", value: "Task t1" },
        { from: "gpt", value: "Solution t1" },
      ],
      metadata: {
        traceId: "t1",
        sourceHarness: "autocontext",
        score: 0.9,
        examplePolicy: "contrastive",
      },
    });

    writeDistillationJsonl(join(outputDir, "train.jsonl"), [makeTraceEntry("t1")]);
    const lines = readFileSync(join(outputDir, "train.jsonl"), "utf-8").trim().split("\n");
    expect(lines).toHaveLength(1);

    const manifest = buildDistillationManifest({
      totalTraces: 2,
      includedTraces: 1,
      excludedTraces: 1,
      trainSize: 1,
      heldOutSize: 0,
      evalOnlySize: 0,
      contrastiveSize: 1,
      curationPolicy: { minScore: 0.5 },
      sources: { autocontext: 1 },
    });
    writeDistillationManifest(outputDir, manifest);
    const persisted = JSON.parse(readFileSync(join(outputDir, "manifest.json"), "utf-8")) as {
      contrastiveSize: number;
      sources: Record<string, number>;
      curationPolicy: { minScore?: number };
      createdAt: string;
    };
    expect(persisted.contrastiveSize).toBe(1);
    expect(persisted.sources.autocontext).toBe(1);
    expect(persisted.curationPolicy.minScore).toBe(0.5);
    expect(persisted.createdAt).toBeTruthy();
  });
});
