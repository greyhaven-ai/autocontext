// Flow 5 (spec §10.3) — Dedupe under batch retry.
//
// Emits two batches whose traceIds overlap, then runs `ingest` twice. The
// first run ingests every unique traceId once; the second run finds
// nothing new (spec §6.5 "Idempotence"). `seen-ids.jsonl` grows by the
// expected count only during the first run.

import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, existsSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runProductionTracesCommand } from "../../../../src/production-traces/cli/index.js";
import { seenIdsPath } from "../../../../src/production-traces/ingest/paths.js";
import {
  aProductionTrace,
  deterministicTraceId,
  seedTracesInRegistry,
} from "./_helpers/fixtures.js";

let tmp: string;

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "autocontext-flow5-"));
});

afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
});

function seenIdCount(cwd: string): number {
  const path = seenIdsPath(cwd);
  if (!existsSync(path)) return 0;
  const body = readFileSync(path, "utf-8");
  return body.split("\n").filter((l) => l.trim().length > 0).length;
}

describe("Flow 5 — dedupe under batch retry", () => {
  test("two overlapping batches produce one-count-per-trace; re-ingest is zero-new", async () => {
    const init = await runProductionTracesCommand(["init"], { cwd: tmp });
    expect(init.exitCode).toBe(0);

    // Batch A has traces 1..5. Batch B has traces 3..7. Union = 7 unique.
    const batchATraces = [1, 2, 3, 4, 5].map((i) =>
      aProductionTrace({ traceId: deterministicTraceId(i) }),
    );
    const batchBTraces = [3, 4, 5, 6, 7].map((i) =>
      aProductionTrace({ traceId: deterministicTraceId(i) }),
    );

    await seedTracesInRegistry(tmp, { traces: batchATraces, batchId: "batch-A" });
    await seedTracesInRegistry(tmp, { traces: batchBTraces, batchId: "batch-B" });

    // First ingest: both batches present, 7 unique traceIds total.
    const r1 = await runProductionTracesCommand(
      ["ingest", "--output", "json"],
      { cwd: tmp },
    );
    expect(r1.exitCode).toBe(0);
    const rep1 = JSON.parse(r1.stdout) as {
      tracesIngested: number;
      duplicatesSkipped: number;
      batchesSucceeded: number;
    };
    expect(rep1.tracesIngested).toBe(7);
    // 3 overlapping ids between the two batches (3, 4, 5).
    expect(rep1.duplicatesSkipped).toBe(3);
    expect(rep1.batchesSucceeded).toBe(2);
    expect(seenIdCount(tmp)).toBe(7);

    // Second ingest: incoming/ is now empty — no batches to process. The
    // retention phase may still run but dedupe has nothing to work on.
    const r2 = await runProductionTracesCommand(
      ["ingest", "--output", "json"],
      { cwd: tmp },
    );
    expect(r2.exitCode).toBe(0);
    const rep2 = JSON.parse(r2.stdout) as {
      tracesIngested: number;
      duplicatesSkipped: number;
    };
    expect(rep2.tracesIngested).toBe(0);
    expect(rep2.duplicatesSkipped).toBe(0);
    expect(seenIdCount(tmp)).toBe(7);

    // --- Simulate a true "batch retry": re-drop batch A into incoming/ and
    // ingest again. Every traceId is now in seen-ids.jsonl, so nothing new.
    await seedTracesInRegistry(tmp, { traces: batchATraces, batchId: "batch-A-retry" });
    const r3 = await runProductionTracesCommand(
      ["ingest", "--output", "json"],
      { cwd: tmp },
    );
    // All-duplicates batch has zero successes → `batchesFailedEntirely=1`,
    // which per `pickIngestExitCode()` maps to DOMAIN_FAILURE (exit 1). This
    // is the existing Layer 3/7 semantic — dedupe is idempotent at the
    // seen-ids layer, but the batch-level verdict still flags "nothing new
    // landed". Spec §6.5 idempotence describes stored state, not exit code.
    expect(r3.exitCode).toBe(1);
    const rep3 = JSON.parse(r3.stdout) as {
      tracesIngested: number;
      duplicatesSkipped: number;
      batchesFailedEntirely: number;
    };
    expect(rep3.tracesIngested).toBe(0);
    expect(rep3.duplicatesSkipped).toBe(5);
    expect(rep3.batchesFailedEntirely).toBe(1);
    // seen-ids count is stable — dedupe is the gating mechanism.
    expect(seenIdCount(tmp)).toBe(7);
  });
});
