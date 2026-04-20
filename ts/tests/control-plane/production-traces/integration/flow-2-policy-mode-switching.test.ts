// Flow 2 (spec §10.3) — Policy mode switching: on-export ↔ on-ingest.
//
// Verifies the §7.4 contract that redaction-mode changes are forward-only:
//
//   a. Emit + ingest under default `on-export` → local `show` renders
//      plaintext for the sensitive field (markers exist but no placeholder
//      is applied at local-view time).
//   b. `policy set --mode on-ingest` → prints a stderr warning.
//   c. Emit + ingest a NEW trace → the sensitive field in the NEW trace
//      is replaced by the placeholder in `show` output (on-ingest mode
//      rewrites before it hits ingested/).
//   d. The OLD trace remains plaintext — on-ingest is NOT retroactive.

import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runProductionTracesCommand } from "../../../../src/production-traces/cli/index.js";
import type { ProductionTrace } from "../../../../src/production-traces/contract/types.js";
import { aProductionTrace, deterministicTraceId, seedTracesInRegistry } from "./_helpers/fixtures.js";

let tmp: string;

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "autocontext-flow2-"));
});

afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
});

const EMAIL = "support+critical@example.com";

function traceWithEmail(traceId: ReturnType<typeof deterministicTraceId>, startedAt: string): ProductionTrace {
  return aProductionTrace({
    traceId,
    startedAt,
    messages: [
      {
        role: "user",
        content: `contact me at ${EMAIL} when ready`,
        timestamp: startedAt,
      },
    ],
  });
}

describe("Flow 2 — policy-mode switching (on-export ↔ on-ingest)", () => {
  test("switching on-export → on-ingest warns; new trace is redacted, old trace stays plaintext", async () => {
    // Bootstrap: default policy (on-export). Auto-detect is enabled by default.
    const init = await runProductionTracesCommand(["init"], { cwd: tmp });
    expect(init.exitCode).toBe(0);

    // --- (a) Emit + ingest under on-export. The email survives the ingest
    // phase; the `show` command renders the raw local-view content verbatim.
    const oldTraceId = deterministicTraceId(1);
    const oldTrace = traceWithEmail(oldTraceId, "2026-04-17T12:00:00.000Z");
    await seedTracesInRegistry(tmp, {
      traces: [oldTrace],
      batchId: "batch-old",
      date: "2026-04-17",
    });
    const ing1 = await runProductionTracesCommand(
      ["ingest", "--output", "json"],
      { cwd: tmp },
    );
    expect(ing1.exitCode).toBe(0);
    expect(JSON.parse(ing1.stdout).tracesIngested).toBe(1);

    const showOld = await runProductionTracesCommand(
      ["show", oldTraceId, "--output", "json"],
      { cwd: tmp },
    );
    expect(showOld.exitCode).toBe(0);
    const showOldTrace = JSON.parse(showOld.stdout) as ProductionTrace;
    // Local-view: plaintext is preserved (spec §7.5).
    expect(JSON.stringify(showOldTrace.messages)).toContain(EMAIL);

    // --- (b) Switch mode to on-ingest. Spec §7.4 requires a stderr warning.
    const setIngest = await runProductionTracesCommand(
      ["policy", "set", "--mode", "on-ingest"],
      { cwd: tmp },
    );
    expect(setIngest.exitCode).toBe(0);
    expect(setIngest.stderr).toMatch(/on-export .* on-ingest/i);
    expect(setIngest.stderr.toLowerCase()).toContain("ingested");

    // --- (c) Emit + ingest a NEW trace under on-ingest. The email must be
    // rewritten to the default placeholder before it reaches ingested/.
    const newTraceId = deterministicTraceId(2);
    const newTrace = traceWithEmail(newTraceId, "2026-04-17T13:00:00.000Z");
    await seedTracesInRegistry(tmp, {
      traces: [newTrace],
      batchId: "batch-new",
      date: "2026-04-17",
    });
    const ing2 = await runProductionTracesCommand(
      ["ingest", "--output", "json"],
      { cwd: tmp },
    );
    expect(ing2.exitCode).toBe(0);
    expect(JSON.parse(ing2.stdout).tracesIngested).toBe(1);

    const showNew = await runProductionTracesCommand(
      ["show", newTraceId, "--output", "json"],
      { cwd: tmp },
    );
    expect(showNew.exitCode).toBe(0);
    const showNewTrace = JSON.parse(showNew.stdout) as ProductionTrace;
    // on-ingest mode: the stored bytes are already redacted.
    const newSerialized = JSON.stringify(showNewTrace.messages);
    expect(newSerialized).not.toContain(EMAIL);
    expect(newSerialized).toContain("[redacted]");

    // --- (d) The OLD trace is unchanged — non-retroactive per spec §7.4.
    const showOldAgain = await runProductionTracesCommand(
      ["show", oldTraceId, "--output", "json"],
      { cwd: tmp },
    );
    expect(showOldAgain.exitCode).toBe(0);
    const showOldAgainTrace = JSON.parse(showOldAgain.stdout) as ProductionTrace;
    expect(JSON.stringify(showOldAgainTrace.messages)).toContain(EMAIL);
  });
});
