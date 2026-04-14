import { describe, expect, it } from "vitest";

import {
  curateTraceEntries,
  normalizeCurationPolicy,
  shouldIncludeTraceEntry,
  splitHeldOutTraceEntries,
} from "../src/traces/data-plane-curation-workflow.js";
import type { TraceEntry } from "../src/traces/data-plane-types.js";
import { SCHEMA_VERSION } from "../src/traces/public-schema.js";

function makeTraceEntry(opts: {
  id: string;
  score: number;
  allowTraining?: boolean;
}): TraceEntry {
  return {
    trace: {
      schemaVersion: SCHEMA_VERSION,
      traceId: opts.id,
      sourceHarness: "autocontext",
      collectedAt: "2026-03-27T10:00:00Z",
      messages: [{ role: "user", content: `Task ${opts.id}`, timestamp: "2026-03-27T10:00:00Z" }],
      outcome: { score: opts.score, reasoning: "ok", dimensions: {} },
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
      allowTraining: opts.allowTraining ?? true,
      attestedAt: "2026-03-27T10:00:00Z",
    },
  };
}

describe("data-plane curation workflow", () => {
  it("normalizes policy defaults and applies inclusion checks", () => {
    const policy = normalizeCurationPolicy();
    expect(policy).toEqual({ minScore: 0, heldOutRatio: 0, requireTrainingConsent: true });

    expect(shouldIncludeTraceEntry(makeTraceEntry({ id: "t1", score: 0.9 }), policy)).toBe(true);
    expect(shouldIncludeTraceEntry(makeTraceEntry({ id: "t2", score: 0.2, allowTraining: false }), policy)).toBe(false);
    expect(shouldIncludeTraceEntry(
      makeTraceEntry({ id: "t3", score: 0.2 }),
      normalizeCurationPolicy({ minScore: 0.5 }),
    )).toBe(false);
  });

  it("splits held-out entries deterministically", () => {
    const entries = [
      makeTraceEntry({ id: "t1", score: 0.4 }),
      makeTraceEntry({ id: "t2", score: 0.6 }),
      makeTraceEntry({ id: "t3", score: 0.8 }),
      makeTraceEntry({ id: "t4", score: 0.9 }),
      makeTraceEntry({ id: "t5", score: 0.95 }),
    ];

    const split = splitHeldOutTraceEntries(entries, 0.4);
    expect(split.train.map((entry) => entry.trace.traceId)).toEqual(["t1", "t2", "t3"]);
    expect(split.heldOut.map((entry) => entry.trace.traceId)).toEqual(["t4", "t5"]);
  });

  it("curates included, excluded, train, and held-out datasets together", () => {
    const dataset = curateTraceEntries([
      makeTraceEntry({ id: "t1", score: 0.3 }),
      makeTraceEntry({ id: "t2", score: 0.7 }),
      makeTraceEntry({ id: "t3", score: 0.8 }),
      makeTraceEntry({ id: "t4", score: 0.9, allowTraining: false }),
    ], normalizeCurationPolicy({ minScore: 0.5, heldOutRatio: 0.5 }));

    expect(dataset.included.map((entry) => entry.trace.traceId)).toEqual(["t2", "t3"]);
    expect(dataset.excluded.map((entry) => entry.trace.traceId)).toEqual(["t1", "t4"]);
    expect(dataset.train.map((entry) => entry.trace.traceId)).toEqual(["t2"]);
    expect(dataset.heldOut.map((entry) => entry.trace.traceId)).toEqual(["t3"]);
  });
});
