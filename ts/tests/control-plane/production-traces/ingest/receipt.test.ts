import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import fc from "fast-check";
import {
  writeReceipt,
  writeErrorFile,
} from "../../../../src/production-traces/ingest/receipt.js";

describe("writeReceipt", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "autocontext-receipt-"));
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  test("writes a canonical JSON file with the expected fields", () => {
    const path = join(dir, "batch.receipt.json");
    writeReceipt(path, {
      count: 5,
      tracesIngested: 5,
      duplicatesSkipped: 0,
      ingestedAt: "2026-04-17T12:00:00.000Z",
      schemaVersion: "1.0",
    });

    const raw = readFileSync(path, "utf-8");
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    expect(parsed).toEqual({
      count: 5,
      duplicatesSkipped: 0,
      ingestedAt: "2026-04-17T12:00:00.000Z",
      schemaVersion: "1.0",
      tracesIngested: 5,
    });
  });

  test("emits keys in sorted order (canonical JSON)", () => {
    const path = join(dir, "batch.receipt.json");
    writeReceipt(path, {
      count: 1,
      tracesIngested: 1,
      duplicatesSkipped: 0,
      ingestedAt: "2026-04-17T00:00:00.000Z",
      schemaVersion: "1.0",
    });
    const raw = readFileSync(path, "utf-8");
    expect(raw).toBe(
      '{"count":1,"duplicatesSkipped":0,"ingestedAt":"2026-04-17T00:00:00.000Z","schemaVersion":"1.0","tracesIngested":1}',
    );
  });

  test("byte-deterministic across identical-content runs (property test, 50 iters)", () => {
    fc.assert(
      fc.property(
        fc.record({
          count: fc.integer({ min: 0, max: 10_000 }),
          tracesIngested: fc.integer({ min: 0, max: 10_000 }),
          duplicatesSkipped: fc.integer({ min: 0, max: 10_000 }),
          ingestedAt: fc.constantFrom(
            "2026-04-17T12:00:00.000Z",
            "2025-01-01T00:00:00.000Z",
            "2027-06-30T23:59:59.999Z",
          ),
          schemaVersion: fc.constantFrom("1.0"),
        }),
        (fields) => {
          const a = join(dir, "a.receipt.json");
          const b = join(dir, "b.receipt.json");
          writeReceipt(a, fields);
          writeReceipt(b, fields);
          return readFileSync(a) .equals(readFileSync(b));
        },
      ),
      { numRuns: 50 },
    );
  });
});

describe("writeErrorFile", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "autocontext-error-"));
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  test("writes a canonical JSON file with the expected fields", () => {
    const path = join(dir, "batch.error.json");
    writeErrorFile(path, {
      perLineErrors: [
        { lineNo: 2, reasons: ["json parse error: Unexpected token"] },
        {
          lineNo: 4,
          attemptedTraceId: "01KPHTAACKMFPPGJWSKRW8W1KA",
          reasons: ["schema: /timing latencyMs must be >= 0"],
        },
      ],
    });
    const raw = readFileSync(path, "utf-8");
    const parsed = JSON.parse(raw);
    expect(parsed).toEqual({
      perLineErrors: [
        { lineNo: 2, reasons: ["json parse error: Unexpected token"] },
        {
          attemptedTraceId: "01KPHTAACKMFPPGJWSKRW8W1KA",
          lineNo: 4,
          reasons: ["schema: /timing latencyMs must be >= 0"],
        },
      ],
    });
  });

  test("byte-deterministic across identical error payloads", () => {
    const input = {
      perLineErrors: [
        { lineNo: 1, reasons: ["r1", "r2"] },
        { lineNo: 3, attemptedTraceId: "01KPHTAACKMFPPGJWSKRW8W1KA", reasons: ["r3"] },
      ],
    };
    const a = join(dir, "a.error.json");
    const b = join(dir, "b.error.json");
    writeErrorFile(a, input);
    writeErrorFile(b, input);
    expect(readFileSync(a).equals(readFileSync(b))).toBe(true);
  });
});
