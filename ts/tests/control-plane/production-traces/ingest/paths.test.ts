import { describe, test, expect } from "vitest";
import { join } from "node:path";
import {
  productionTracesRoot,
  incomingDir,
  ingestedDir,
  failedDir,
  seenIdsPath,
  gcLogPath,
  dateOf,
} from "../../../../src/production-traces/ingest/paths.js";

describe("production-traces path helpers", () => {
  const cwd = "/tmp/autocontext-fixture";

  test("productionTracesRoot joins cwd with .autocontext/production-traces", () => {
    expect(productionTracesRoot(cwd)).toBe(join(cwd, ".autocontext", "production-traces"));
  });

  test("incomingDir defaults to today's UTC date when no date given", () => {
    const p = incomingDir(cwd);
    // Today (UTC) — we only assert shape + that it's under incoming/.
    expect(p).toMatch(/incoming\/\d{4}-\d{2}-\d{2}$/);
    expect(p.startsWith(join(cwd, ".autocontext", "production-traces", "incoming"))).toBe(true);
  });

  test("incomingDir uses explicit date when given", () => {
    const p = incomingDir(cwd, "2026-04-17");
    expect(p).toBe(join(cwd, ".autocontext", "production-traces", "incoming", "2026-04-17"));
  });

  test("ingestedDir uses explicit date", () => {
    const p = ingestedDir(cwd, "2026-04-17");
    expect(p).toBe(join(cwd, ".autocontext", "production-traces", "ingested", "2026-04-17"));
  });

  test("failedDir uses explicit date", () => {
    const p = failedDir(cwd, "2026-04-17");
    expect(p).toBe(join(cwd, ".autocontext", "production-traces", "failed", "2026-04-17"));
  });

  test("seenIdsPath points at <root>/seen-ids.jsonl", () => {
    expect(seenIdsPath(cwd)).toBe(
      join(cwd, ".autocontext", "production-traces", "seen-ids.jsonl"),
    );
  });

  test("gcLogPath points at <root>/gc-log.jsonl", () => {
    expect(gcLogPath(cwd)).toBe(
      join(cwd, ".autocontext", "production-traces", "gc-log.jsonl"),
    );
  });

  test("dateOf extracts YYYY-MM-DD in UTC", () => {
    expect(dateOf("2026-04-17T12:00:00.000Z")).toBe("2026-04-17");
  });

  test("dateOf is stable at UTC midnight boundary", () => {
    // 00:00:00.000Z is the start of the day it names.
    expect(dateOf("2026-04-17T00:00:00.000Z")).toBe("2026-04-17");
    // 23:59:59.999Z is still the same day in UTC.
    expect(dateOf("2026-04-17T23:59:59.999Z")).toBe("2026-04-17");
  });

  test("dateOf normalizes non-Z offsets to UTC", () => {
    // 2026-04-17T02:00:00+04:00 = 2026-04-16T22:00:00Z
    expect(dateOf("2026-04-17T02:00:00+04:00")).toBe("2026-04-16");
  });

  test("dateOf throws on unparseable input", () => {
    expect(() => dateOf("not a date")).toThrow();
  });
});
