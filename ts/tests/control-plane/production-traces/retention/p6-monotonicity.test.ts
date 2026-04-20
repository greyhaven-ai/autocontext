// P6 — Retention monotonicity (spec §10.1).
//
// Over a random generator of {trace-age profile, policy config}, enforce
// retention once and assert:
//   (a) No trace whose age is STRICTLY LESS than `retentionDays` is deleted.
//   (b) No trace whose age is STRICTLY GREATER than `retentionDays +
//       (gcBatchSize-worth-of-backlog)` remains — equivalently: at most
//       `gcBatchSize` eligible traces survive a single enforcement run.
//
// The second bound encodes the batched-work semantic: the enforcement run
// caps deletions at `gcBatchSize` so a very large backlog drains across
// multiple runs. For property-test purposes we generate fixtures smaller
// than `gcBatchSize` so a single run is expected to delete ALL eligible
// traces, but we also exercise a smaller `gcBatchSize` to confirm the cap
// is respected.
//
// Additional sub-property: repeated enforcement runs fully drain the backlog
// of eligible traces (monotonic convergence).

import { describe, test } from "vitest";
import fc from "fast-check";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  enforceRetention,
  type RetentionPolicy,
} from "../../../../src/production-traces/retention/index.js";
import { ingestedDir } from "../../../../src/production-traces/ingest/paths.js";
import type { ProductionTrace } from "../../../../src/production-traces/contract/types.js";
import { newProductionTraceId } from "../../../../src/production-traces/contract/branded-ids.js";

const REFERENCE_NOW = new Date("2026-04-17T12:00:00.000Z");
const MS_PER_DAY = 24 * 60 * 60 * 1000;

function makeTrace(ageDays: number, label: "success" | "failure"): ProductionTrace {
  const endedMs = REFERENCE_NOW.getTime() - ageDays * MS_PER_DAY;
  const endedAt = new Date(endedMs).toISOString();
  const startedAt = new Date(endedMs - 500).toISOString();
  return {
    schemaVersion: "1.0",
    traceId: newProductionTraceId(),
    source: { emitter: "sdk", sdk: { name: "autoctx-ts", version: "0.4.3" } },
    provider: { name: "openai" },
    model: "gpt-4o-mini",
    env: {
      environmentTag: "production" as ProductionTrace["env"]["environmentTag"],
      appId: "my-app" as ProductionTrace["env"]["appId"],
    },
    messages: [{ role: "user", content: "x", timestamp: startedAt }],
    toolCalls: [],
    timing: { startedAt, endedAt, latencyMs: 500 },
    usage: { tokensIn: 1, tokensOut: 1 },
    feedbackRefs: [],
    links: {},
    redactions: [],
    outcome: { label },
  };
}

describe("P6 retention monotonicity (property)", () => {
  test("no trace younger than retentionDays is deleted; deletions capped by gcBatchSize", async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.record({
          retentionDays: fc.integer({ min: 1, max: 180 }),
          gcBatchSize: fc.integer({ min: 1, max: 50 }),
          // Each fixture holds up to 20 traces with random ages and labels.
          traces: fc.array(
            fc.record({
              ageDays: fc.integer({ min: 0, max: 365 }),
              label: fc.constantFrom<"success" | "failure">("success", "failure"),
            }),
            { minLength: 0, maxLength: 20 },
          ),
          preserveFailures: fc.boolean(),
        }),
        async ({ retentionDays, gcBatchSize, traces, preserveFailures }) => {
          if (traces.length === 0) return true;
          const cwd = mkdtempSync(join(tmpdir(), "autocontext-pt-p6-"));
          try {
            const built = traces.map((t) => makeTrace(t.ageDays, t.label));
            const dir = ingestedDir(cwd, "2026-04-17");
            mkdirSync(dir, { recursive: true });
            writeFileSync(
              join(dir, "batch.jsonl"),
              built.map((b) => JSON.stringify(b)).join("\n") + "\n",
              "utf-8",
            );

            const policy: RetentionPolicy = {
              schemaVersion: "1.0",
              retentionDays,
              preserveAll: false,
              preserveCategories: preserveFailures ? ["failure"] : [],
              gcBatchSize,
            };

            const report = await enforceRetention({
              cwd,
              policy,
              nowUtc: REFERENCE_NOW,
              dryRun: false,
            });

            // `traces[i].ageDays >= retentionDays` is the "eligible for deletion"
            // predicate (matches enforce.ts: endedMs <= thresholdMs).
            const eligibleCount = traces.filter((t) => {
              if (t.ageDays < retentionDays) return false;
              if (preserveFailures && t.label === "failure") return false;
              return true;
            }).length;

            // (a) `deleted` must never exceed the count of eligible traces.
            if (report.deleted > eligibleCount) return false;
            // (b) `deleted` must never exceed the gcBatchSize cap.
            if (report.deleted > gcBatchSize) return false;
            // Conservation: evaluated == traces.length (all lines parseable).
            if (report.evaluated !== traces.length) return false;
            // Conservation across buckets: preserved + tooYoung + deleted +
            // (eligible-not-yet-deleted) = evaluated
            const leftover = eligibleCount - report.deleted;
            if (
              report.deleted + report.preserved + report.tooYoung + leftover !==
              report.evaluated
            ) {
              return false;
            }
            // If gcBatchSize exceeds the eligible set, a single run must delete
            // ALL eligible traces.
            if (gcBatchSize >= eligibleCount && report.deleted !== eligibleCount) {
              return false;
            }
            return true;
          } finally {
            rmSync(cwd, { recursive: true, force: true });
          }
        },
      ),
      { numRuns: 100 },
    );
  });

  test("deletions monotonic across runs: re-enforcement never resurrects data", async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.record({
          retentionDays: fc.integer({ min: 1, max: 90 }),
          gcBatchSize: fc.integer({ min: 1, max: 5 }), // small to force multi-run drains
          ageDays: fc.array(fc.integer({ min: 0, max: 365 }), { minLength: 0, maxLength: 15 }),
        }),
        async ({ retentionDays, gcBatchSize, ageDays }) => {
          if (ageDays.length === 0) return true;
          const cwd = mkdtempSync(join(tmpdir(), "autocontext-pt-p6b-"));
          try {
            const built = ageDays.map((a) => makeTrace(a, "success"));
            const dir = ingestedDir(cwd, "2026-04-17");
            mkdirSync(dir, { recursive: true });
            writeFileSync(
              join(dir, "batch.jsonl"),
              built.map((b) => JSON.stringify(b)).join("\n") + "\n",
              "utf-8",
            );

            const policy: RetentionPolicy = {
              schemaVersion: "1.0",
              retentionDays,
              preserveAll: false,
              preserveCategories: [],
              gcBatchSize,
            };

            let totalDeleted = 0;
            // Run enforcement repeatedly (bounded) — each run deletes at most
            // gcBatchSize. After enough runs, the backlog is fully drained.
            const maxRuns = Math.max(1, Math.ceil(ageDays.length / gcBatchSize) + 2);
            for (let i = 0; i < maxRuns; i += 1) {
              const r = await enforceRetention({
                cwd,
                policy,
                nowUtc: REFERENCE_NOW,
                dryRun: false,
              });
              if (r.deleted > gcBatchSize) return false;
              totalDeleted += r.deleted;
              if (r.deleted === 0) break;
            }
            const eligibleCount = ageDays.filter((a) => a >= retentionDays).length;
            return totalDeleted === eligibleCount;
          } finally {
            rmSync(cwd, { recursive: true, force: true });
          }
        },
      ),
      { numRuns: 30 },
    );
  });
});
