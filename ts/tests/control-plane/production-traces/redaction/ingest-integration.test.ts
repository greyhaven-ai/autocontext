import { describe, test, expect, beforeEach, afterEach, vi } from "vitest";
import {
  mkdtempSync,
  rmSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { ingestBatches } from "../../../../src/production-traces/ingest/scan-workflow.js";
import {
  incomingDir,
  ingestedDir,
} from "../../../../src/production-traces/ingest/paths.js";
import {
  defaultRedactionPolicy,
  saveRedactionPolicy,
} from "../../../../src/production-traces/redaction/policy.js";
import { initializeInstallSalt } from "../../../../src/production-traces/redaction/install-salt.js";
import {
  newProductionTraceId,
  type ProductionTraceId,
} from "../../../../src/production-traces/contract/branded-ids.js";
import type { ProductionTrace } from "../../../../src/production-traces/contract/types.js";

const DATE = "2026-04-17";

function makeTrace(overrides: Partial<ProductionTrace> = {}): ProductionTrace {
  const id: ProductionTraceId = newProductionTraceId();
  const base: ProductionTrace = {
    schemaVersion: "1.0",
    traceId: id,
    source: { emitter: "sdk", sdk: { name: "autocontext-ts", version: "0.4.3" } },
    provider: { name: "openai" },
    model: "gpt-4o-mini",
    env: {
      environmentTag: "production" as ProductionTrace["env"]["environmentTag"],
      appId: "my-app" as ProductionTrace["env"]["appId"],
    },
    messages: [
      {
        role: "user",
        content: "reach me at alice@example.com",
        timestamp: `${DATE}T12:00:00.000Z`,
      },
    ],
    toolCalls: [],
    timing: {
      startedAt: `${DATE}T12:00:00.000Z`,
      endedAt: `${DATE}T12:00:01.000Z`,
      latencyMs: 1000,
    },
    usage: { tokensIn: 10, tokensOut: 5 },
    feedbackRefs: [],
    links: {},
    redactions: [],
  };
  return { ...base, ...overrides };
}

function writeBatch(cwd: string, batchId: string, traces: ProductionTrace[]): string {
  const dir = incomingDir(cwd, DATE);
  mkdirSync(dir, { recursive: true });
  const path = join(dir, `${batchId}.jsonl`);
  writeFileSync(path, traces.map((t) => JSON.stringify(t)).join("\n") + "\n");
  return path;
}

describe("ingest + redaction integration", () => {
  let cwd: string;

  beforeEach(() => {
    cwd = mkdtempSync(join(tmpdir(), "autocontext-redaction-integration-"));
  });

  afterEach(() => {
    rmSync(cwd, { recursive: true, force: true });
  });

  test("default mode (on-export): trace stored with plaintext email + marker", async () => {
    const trace = makeTrace();
    writeBatch(cwd, "batch-default", [trace]);

    const report = await ingestBatches(cwd, {});
    expect(report.tracesIngested).toBe(1);

    const stored = JSON.parse(
      readFileSync(join(ingestedDir(cwd, DATE), "batch-default.jsonl"), "utf-8").trim(),
    ) as ProductionTrace;

    // Plaintext email survives to disk.
    expect(stored.messages[0].content).toContain("alice@example.com");
    // But the marker is populated.
    const emailMarker = stored.redactions.find((m) => m.category === "pii-email");
    expect(emailMarker).toBeDefined();
    expect(emailMarker!.detectedBy).toBe("ingestion");
  });

  test("on-ingest mode: trace stored with placeholder email + marker; plaintext never written", async () => {
    // Configure on-ingest and initialize a salt.
    const policy = {
      ...defaultRedactionPolicy(),
      mode: "on-ingest" as const,
    };
    await saveRedactionPolicy(cwd, policy);
    await initializeInstallSalt(cwd);

    // Silence the advisory warning for test cleanliness.
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

    try {
      const trace = makeTrace();
      writeBatch(cwd, "batch-oni", [trace]);

      const report = await ingestBatches(cwd, {});
      expect(report.tracesIngested).toBe(1);

      const stored = JSON.parse(
        readFileSync(join(ingestedDir(cwd, DATE), "batch-oni.jsonl"), "utf-8").trim(),
      ) as ProductionTrace;

      // Plaintext email MUST NOT survive to disk.
      expect(stored.messages[0].content).not.toContain("alice@example.com");
      // Replaced with the default placeholder.
      expect(stored.messages[0].content).toBe("[redacted]");
      // Marker still populated.
      expect(stored.redactions.find((m) => m.category === "pii-email")).toBeDefined();

      // On-ingest mode should emit the advisory warning (spec §7.4).
      expect(warnSpy).toHaveBeenCalled();
      const warned = warnSpy.mock.calls.map((c) => String(c[0])).join("\n");
      expect(warned).toMatch(/on-ingest/);
    } finally {
      warnSpy.mockRestore();
    }
  });

  test("client marker preserved through full ingest flow", async () => {
    const trace = makeTrace({
      messages: [
        {
          role: "user",
          content: "reach me at alice@example.com",
          timestamp: `${DATE}T12:00:00.000Z`,
        },
      ],
      redactions: [
        {
          path: "/messages/0/content",
          reason: "pii-custom",
          detectedBy: "client",
          detectedAt: `${DATE}T11:59:00.000Z`,
        },
      ],
    });
    writeBatch(cwd, "batch-client", [trace]);

    const report = await ingestBatches(cwd, {});
    expect(report.tracesIngested).toBe(1);

    const stored = JSON.parse(
      readFileSync(join(ingestedDir(cwd, DATE), "batch-client.jsonl"), "utf-8").trim(),
    ) as ProductionTrace;

    const clientMarker = stored.redactions.find((m) => m.detectedBy === "client");
    expect(clientMarker).toBeDefined();
    expect(clientMarker).toEqual({
      path: "/messages/0/content",
      reason: "pii-custom",
      detectedBy: "client",
      detectedAt: `${DATE}T11:59:00.000Z`,
    });
  });

  test("malformed redaction-policy.json prevents ingest (fails fast, no trace written)", async () => {
    mkdirSync(join(cwd, ".autocontext", "production-traces"), { recursive: true });
    writeFileSync(
      join(cwd, ".autocontext", "production-traces", "redaction-policy.json"),
      JSON.stringify({ bogus: true }),
    );

    const trace = makeTrace();
    writeBatch(cwd, "batch-broken-policy", [trace]);

    await expect(ingestBatches(cwd, {})).rejects.toThrow(/redaction-policy/i);
  });
});
