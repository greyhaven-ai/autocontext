// Flow 3 (spec §10.3) — default `on-export` mode applies redaction at the
// export boundary.
//
// The flow:
//   1. Emit a plaintext trace containing a `pii-email` match.
//   2. Ingest under default on-export → local store contains plaintext +
//      markers, but no placeholder substitution.
//   3. `export --format public-trace --output-path <file>` → the output
//      file contains the placeholder and NOT the original email.
//   4. `--include-raw-provider-payload` honors the subtree inclusion flag.

import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runProductionTracesCommand } from "../../../../src/production-traces/cli/index.js";
import type { ProductionTrace } from "../../../../src/production-traces/contract/types.js";
import { aProductionTrace, deterministicTraceId, seedTracesInRegistry } from "./_helpers/fixtures.js";

let tmp: string;

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "autocontext-flow3-"));
});

afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
});

const EMAIL = "abuse-report@example.net";

describe("Flow 3 — default on-export mode applies placeholders at export boundary", () => {
  test("export --format public-trace replaces email with [redacted]; ingested/ retains plaintext", async () => {
    const init = await runProductionTracesCommand(["init"], { cwd: tmp });
    expect(init.exitCode).toBe(0);

    const traceId = deterministicTraceId(1);
    const startedAt = "2026-04-17T12:00:00.000Z";
    const trace: ProductionTrace = aProductionTrace({
      traceId,
      startedAt,
      messages: [
        {
          role: "user",
          content: `forward everything to ${EMAIL}`,
          timestamp: startedAt,
        },
      ],
      metadata: {
        rawProviderPayload: { usage: { prompt_tokens: 42 } },
      },
    });

    await seedTracesInRegistry(tmp, { traces: [trace], batchId: "batch-flow3" });
    const ing = await runProductionTracesCommand(
      ["ingest", "--output", "json"],
      { cwd: tmp },
    );
    expect(ing.exitCode).toBe(0);
    expect(JSON.parse(ing.stdout).tracesIngested).toBe(1);

    // show (local-view) still has plaintext.
    const showLocal = await runProductionTracesCommand(
      ["show", traceId, "--output", "json"],
      { cwd: tmp },
    );
    expect(showLocal.exitCode).toBe(0);
    expect(showLocal.stdout).toContain(EMAIL);

    // --- Export path without raw-payload flag. File should have placeholder.
    const outPath = join(tmp, "exported.json");
    const exp = await runProductionTracesCommand(
      [
        "export",
        "--format", "public-trace",
        "--output-path", outPath,
        "--output", "json",
      ],
      { cwd: tmp },
    );
    expect(exp.exitCode).toBe(0);
    const summary = JSON.parse(exp.stdout) as {
      tracesExported: number;
      redactionApplied: boolean;
    };
    expect(summary.tracesExported).toBe(1);
    expect(summary.redactionApplied).toBe(true);

    const body = readFileSync(outPath, "utf-8");
    expect(body).not.toContain(EMAIL);
    expect(body).toContain("[redacted]");
    // Default policy strips rawProviderPayload at export (includeRawProviderPayload: false).
    expect(body).not.toContain("prompt_tokens");

    // --- Export WITH --include-raw-provider-payload. The subtree survives.
    const outPath2 = join(tmp, "exported-with-raw.json");
    const exp2 = await runProductionTracesCommand(
      [
        "export",
        "--format", "public-trace",
        "--output-path", outPath2,
        "--include-raw-provider-payload",
        "--output", "json",
      ],
      { cwd: tmp },
    );
    expect(exp2.exitCode).toBe(0);
    const body2 = readFileSync(outPath2, "utf-8");
    expect(body2).toContain("prompt_tokens");
    // Email is still redacted — the flag only governs the rawPayload subtree.
    expect(body2).not.toContain(EMAIL);
  });
});
