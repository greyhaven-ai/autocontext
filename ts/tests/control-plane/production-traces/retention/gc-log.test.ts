import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, readFileSync, existsSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  appendGcLogEntry,
  readGcLog,
} from "../../../../src/production-traces/retention/index.js";
import { gcLogPath, productionTracesRoot } from "../../../../src/production-traces/ingest/paths.js";

let cwd: string;

beforeEach(() => {
  cwd = mkdtempSync(join(tmpdir(), "autocontext-pt-retention-gclog-"));
});
afterEach(() => {
  rmSync(cwd, { recursive: true, force: true });
});

describe("retention/gc-log", () => {
  test("appendGcLogEntry creates gc-log.jsonl with one line per entry", () => {
    appendGcLogEntry(cwd, {
      traceId: "trace-a",
      batchPath: "ingested/2025-09-01/batch-old.jsonl",
      deletedAt: "2026-04-17T00:00:00.000Z",
      reason: "retention-expired",
    });
    appendGcLogEntry(cwd, {
      traceId: "trace-b",
      batchPath: "ingested/2025-09-02/batch-old.jsonl",
      deletedAt: "2026-04-17T00:00:00.000Z",
      reason: "retention-expired",
    });

    const lines = readFileSync(gcLogPath(cwd), "utf-8").trim().split("\n");
    expect(lines.length).toBe(2);
    expect(JSON.parse(lines[0]!).traceId).toBe("trace-a");
    expect(JSON.parse(lines[1]!).traceId).toBe("trace-b");
  });

  test("appendGcLogEntry emits canonical JSON (keys sorted lexicographically)", () => {
    appendGcLogEntry(cwd, {
      // Insertion order deliberately scrambled.
      reason: "retention-expired",
      traceId: "trace-x",
      deletedAt: "2026-04-17T00:00:00.000Z",
      batchPath: "ingested/2025-09-01/batch.jsonl",
    });
    const line = readFileSync(gcLogPath(cwd), "utf-8").trim();
    // Keys must appear in lexicographic order.
    expect(line).toBe(
      '{"batchPath":"ingested/2025-09-01/batch.jsonl","deletedAt":"2026-04-17T00:00:00.000Z","reason":"retention-expired","traceId":"trace-x"}',
    );
  });

  test("appendGcLogEntry is append-only: existing lines are never rewritten", () => {
    // Pre-seed gc-log with a historical entry using arbitrary (non-canonical) JSON.
    mkdirSync(productionTracesRoot(cwd), { recursive: true });
    const pre = '{"legacy":"ok","traceId":"trace-legacy"}\n';
    writeFileSync(gcLogPath(cwd), pre, "utf-8");

    appendGcLogEntry(cwd, {
      traceId: "trace-new",
      batchPath: "ingested/2026-04-17/batch.jsonl",
      deletedAt: "2026-04-17T00:00:00.000Z",
      reason: "retention-expired",
    });

    const raw = readFileSync(gcLogPath(cwd), "utf-8");
    // Pre-existing line preserved verbatim.
    expect(raw.startsWith(pre)).toBe(true);
    // New entry appended afterwards.
    expect(raw.includes("trace-new")).toBe(true);
  });

  test("readGcLog returns [] when the file does not exist", () => {
    expect(existsSync(gcLogPath(cwd))).toBe(false);
    expect(readGcLog(cwd)).toEqual([]);
  });

  test("readGcLog parses each line as JSON and returns entries in order", () => {
    appendGcLogEntry(cwd, {
      traceId: "a",
      batchPath: "p/a",
      deletedAt: "2026-04-17T00:00:00.000Z",
      reason: "retention-expired",
    });
    appendGcLogEntry(cwd, {
      traceId: "b",
      batchPath: "p/b",
      deletedAt: "2026-04-17T00:00:01.000Z",
      reason: "retention-expired",
    });
    const entries = readGcLog(cwd);
    expect(entries.length).toBe(2);
    expect(entries[0]!.traceId).toBe("a");
    expect(entries[1]!.traceId).toBe("b");
  });
});
