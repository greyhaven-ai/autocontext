// Flow 4 (spec §10.3) — retention enforcement across a 100-day time-travel
// window.
//
// Seeds the ingested/ store with traces spanning 100 days, then drives the
// `prune` command with an injected `now` so retention-age arithmetic is
// deterministic. Asserts:
//
//   - traces older than retentionDays are deleted,
//   - newer traces are preserved,
//   - `gc-log.jsonl` receives one audit entry per deletion,
//   - `preserveCategories: ["failure"]` spares failure-labeled traces
//     regardless of age (spec §6.6 escape hatch).

import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runProductionTracesCommand } from "../../../../src/production-traces/cli/index.js";
import type { ProductionTrace } from "../../../../src/production-traces/contract/types.js";
import { gcLogPath } from "../../../../src/production-traces/ingest/paths.js";
import {
  aProductionTrace,
  buildRetentionPolicy,
  deterministicTraceId,
  seedIngestedTraces,
} from "./_helpers/fixtures.js";

let tmp: string;

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "autocontext-flow4-"));
});

afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
});

const NOW_UTC = "2026-04-17T12:00:00.000Z";

function daysAgo(days: number): string {
  const d = new Date(NOW_UTC);
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString();
}

function traceAtOffset(index: number, daysOld: number, label?: ProductionTrace["outcome"] extends infer O ? O extends { label: infer L } ? L : never : never): ProductionTrace {
  const startedAt = daysAgo(daysOld);
  return aProductionTrace({
    traceId: deterministicTraceId(index),
    startedAt,
    ...(label !== undefined
      ? { outcome: { label: label as "success" | "failure" | "partial", score: 0.5 } }
      : {}),
  });
}

describe("Flow 4 — retention enforcement across 100-day time-travel", () => {
  test("prune with retentionDays=90 deletes old traces, keeps newer; preserveCategories spares failure", async () => {
    const init = await runProductionTracesCommand(["init"], { cwd: tmp });
    expect(init.exitCode).toBe(0);
    await buildRetentionPolicy(tmp, {
      retentionDays: 90,
      preserveAll: false,
      preserveCategories: ["failure"],
    });

    // Build a corpus: one trace per 10-day slice from 0 to 100 days old,
    // alternating between no label (default), success, and failure. Expected
    // retention behavior at retentionDays=90:
    //   - Any trace whose endedAt is older than 90 days is deletable UNLESS
    //     its outcome.label is in preserveCategories.
    //   - Traces newer than 90 days are preserved regardless of label.
    const traces: ProductionTrace[] = [];
    const expectations: Array<{ index: number; daysOld: number; label: string | null; expectedSurvives: boolean }> = [];
    for (let i = 0; i < 11; i++) {
      const daysOld = i * 10; // 0, 10, ..., 100
      const label = i % 3 === 0 ? null : i % 3 === 1 ? "success" : "failure";
      traces.push(traceAtOffset(i, daysOld, label as never));
      const isOld = daysOld > 90;
      const isPreserved = label === "failure";
      expectations.push({
        index: i,
        daysOld,
        label,
        expectedSurvives: !isOld || isPreserved,
      });
    }

    // Distribute into a few date partitions to exercise the multi-dir walk.
    // Use each trace's startedAt-date as the partition key.
    for (const t of traces) {
      const date = t.timing.startedAt.slice(0, 10);
      await seedIngestedTraces(tmp, {
        traces: [t],
        date,
        batchId: `batch-${t.traceId}`,
      });
    }

    // Run prune under an injected now so retention arithmetic is
    // deterministic in CI.
    const prune = await runProductionTracesCommand(
      ["prune", "--output", "json"],
      { cwd: tmp, now: () => NOW_UTC },
    );
    expect(prune.exitCode).toBe(0);
    const report = JSON.parse(prune.stdout) as {
      retentionDays: number;
      scannedTraces: number;
      deletedTraces: number;
      preservedByCategory: number;
      preservedByAge: number;
      preserveAll: boolean;
    };
    expect(report.retentionDays).toBe(90);
    expect(report.preserveAll).toBe(false);

    // Tabulate expectations independently.
    const expectedDeleted = expectations.filter((e) => !e.expectedSurvives).length;
    const expectedPreservedByCategory = expectations.filter(
      (e) => e.daysOld > 90 && e.label === "failure",
    ).length;
    const expectedPreservedByAge = expectations.filter((e) => e.daysOld <= 90).length;

    expect(report.scannedTraces).toBe(expectations.length);
    expect(report.deletedTraces).toBe(expectedDeleted);
    expect(report.preservedByCategory).toBe(expectedPreservedByCategory);
    expect(report.preservedByAge).toBe(expectedPreservedByAge);
    expect(expectedDeleted).toBeGreaterThan(0);

    // gc-log.jsonl has one entry per deletion (retention-expired).
    expect(existsSync(gcLogPath(tmp))).toBe(true);
    const gcLog = readFileSync(gcLogPath(tmp), "utf-8").trim();
    const entries = gcLog.split("\n").filter((l) => l.length > 0);
    expect(entries.length).toBe(expectedDeleted);
    for (const entry of entries) {
      const parsed = JSON.parse(entry) as { reason: string };
      expect(parsed.reason).toBe("retention-expired");
    }

    // --- Subsequent list confirms the survivors match expectations.
    const list = await runProductionTracesCommand(["list", "--output", "json"], { cwd: tmp });
    expect(list.exitCode).toBe(0);
    const rows = JSON.parse(list.stdout) as Array<{ traceId: string }>;
    const survivingIds = new Set(rows.map((r) => r.traceId));
    for (const exp of expectations) {
      const id = deterministicTraceId(exp.index);
      if (exp.expectedSurvives) {
        expect(survivingIds.has(id)).toBe(true);
      } else {
        expect(survivingIds.has(id)).toBe(false);
      }
    }
  });
});
