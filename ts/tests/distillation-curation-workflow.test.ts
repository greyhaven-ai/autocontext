import { describe, expect, it } from "vitest";

import {
  applyDistillationPolicy,
  computeTopQuartileThreshold,
  normalizeDistillationPolicy,
  splitHeldOutEntries,
  summarizeSources,
} from "../src/traces/distillation-curation-workflow.js";
import type { TraceEntry } from "../src/traces/distillation-types.js";
import { SCHEMA_VERSION } from "../src/traces/public-schema.js";

function makeTraceEntry(opts: {
  id: string;
  score: number;
  family?: string;
  gate?: string;
  source?: string;
  allowTraining?: boolean;
}): TraceEntry {
  return {
    trace: {
      schemaVersion: SCHEMA_VERSION,
      traceId: opts.id,
      sourceHarness: opts.source ?? "autocontext",
      collectedAt: "2026-03-27T10:00:00Z",
      messages: [
        { role: "user", content: `Task ${opts.id}`, timestamp: "2026-03-27T10:00:00Z" },
        { role: "assistant", content: `Solution ${opts.id}`, timestamp: "2026-03-27T10:00:01Z" },
      ],
      outcome: { score: opts.score, reasoning: "ok", dimensions: {} },
      metadata: { family: opts.family ?? "agent_task", gateDecision: opts.gate ?? "advance" },
    },
    manifest: {
      schemaVersion: SCHEMA_VERSION,
      sourceHarness: opts.source ?? "autocontext",
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
      allowTraining: opts.allowTraining ?? true,
      attestedAt: "2026-03-27T10:00:00Z",
    },
  };
}

describe("distillation curation workflow", () => {
  it("normalizes policy defaults and computes top-quartile thresholds", () => {
    expect(normalizeDistillationPolicy()).toMatchObject({
      minScore: 0,
      topQuartile: false,
      failurePolicy: "exclude",
      requireTrainingConsent: true,
    });

    const threshold = computeTopQuartileThreshold([
      makeTraceEntry({ id: "t1", score: 0.3 }),
      makeTraceEntry({ id: "t2", score: 0.5 }),
      makeTraceEntry({ id: "t3", score: 0.7 }),
      makeTraceEntry({ id: "t4", score: 0.9 }),
      makeTraceEntry({ id: "t5", score: 0.4 }),
      makeTraceEntry({ id: "t6", score: 0.6 }),
      makeTraceEntry({ id: "t7", score: 0.8 }),
      makeTraceEntry({ id: "t8", score: 0.95 }),
    ]);

    expect(threshold).toBe(0.9);
  });

  it("applies consent, gate, family, and failure policies", () => {
    const policy = normalizeDistillationPolicy({
      minScore: 0.5,
      advanceOnly: true,
      familyFilter: ["simulation"],
      failurePolicy: "contrastive",
    });
    const result = applyDistillationPolicy([
      makeTraceEntry({ id: "t1", score: 0.9, family: "simulation", gate: "advance" }),
      makeTraceEntry({ id: "t2", score: 0.2, family: "simulation", gate: "advance" }),
      makeTraceEntry({ id: "t3", score: 0.9, family: "agent_task", gate: "advance" }),
      makeTraceEntry({ id: "t4", score: 0.9, family: "simulation", gate: "retry" }),
      makeTraceEntry({ id: "t5", score: 0.9, family: "simulation", gate: "advance", allowTraining: false }),
    ], policy);

    expect(result.included.map((entry) => entry.trace.traceId)).toEqual(["t1"]);
    expect(result.contrastive.map((entry) => entry.trace.traceId)).toEqual(["t2"]);
    expect(result.excluded.map((entry) => entry.trace.traceId).sort()).toEqual(["t3", "t4", "t5"]);
  });

  it("splits held-out traces and summarizes included sources", () => {
    const entries = [
      makeTraceEntry({ id: "t1", score: 0.9, source: "autocontext" }),
      makeTraceEntry({ id: "t2", score: 0.8, source: "hermes" }),
      makeTraceEntry({ id: "t3", score: 0.7, source: "autocontext" }),
      makeTraceEntry({ id: "t4", score: 0.6, source: "autocontext" }),
    ];

    const split = splitHeldOutEntries(entries, 0.25);
    expect(split.train.map((entry) => entry.trace.traceId)).toEqual(["t1", "t2", "t3"]);
    expect(split.heldOut.map((entry) => entry.trace.traceId)).toEqual(["t4"]);
    expect(summarizeSources(entries)).toEqual({ autocontext: 3, hermes: 1 });
  });
});
