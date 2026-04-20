import { describe, test, expect } from "vitest";
import { validateIngestedLine } from "../../../../src/production-traces/ingest/validator.js";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const FIXTURES = join(__dirname, "..", "fixtures");

describe("validateIngestedLine", () => {
  test("accepts a valid minimal trace", () => {
    const raw = readFileSync(join(FIXTURES, "valid-minimal.json"), "utf-8");
    // Collapse to a single line (already compact, but guarantee no stray newlines).
    const line = JSON.stringify(JSON.parse(raw));
    const r = validateIngestedLine(line);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.trace.traceId).toBe("01KPHTAACKMFPPGJWSKRW8W1KA");
      expect(r.trace.schemaVersion).toBe("1.0");
    }
  });

  test("rejects malformed JSON without throwing", () => {
    const r = validateIngestedLine("{not json");
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.reason.toLowerCase()).toContain("json");
      expect(r.attemptedTraceId).toBeUndefined();
    }
  });

  test("rejects a trace that fails schema validation (missing required field)", () => {
    const raw = readFileSync(join(FIXTURES, "invalid-missing-required.json"), "utf-8");
    const line = JSON.stringify(JSON.parse(raw));
    const r = validateIngestedLine(line);
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.reason).toBeTruthy();
    }
  });

  test("rejects a trace that fails timing-sanity (endedAt < startedAt)", () => {
    const raw = readFileSync(join(FIXTURES, "invalid-bad-timing.json"), "utf-8");
    const line = JSON.stringify(JSON.parse(raw));
    const r = validateIngestedLine(line);
    expect(r.ok).toBe(false);
    if (!r.ok) {
      // The fixture fails either schema-level (latencyMs negative) or timing sanity.
      // Either way the per-line reason surfaces the problem.
      expect(r.reason).toBeTruthy();
    }
  });

  test("rejects a trace with a redaction pointer that does not resolve", () => {
    const baseRaw = readFileSync(join(FIXTURES, "valid-minimal.json"), "utf-8");
    const base = JSON.parse(baseRaw) as Record<string, unknown>;
    base.redactions = [
      {
        path: "/messages/0/nonexistent",
        reason: "pii-custom",
        detectedBy: "client",
        detectedAt: "2026-04-17T12:00:02.000Z",
      },
    ];
    const line = JSON.stringify(base);
    const r = validateIngestedLine(line);
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.reason.toLowerCase()).toContain("redact");
    }
  });

  test("surfaces attemptedTraceId when JSON parses but validation fails later", () => {
    const baseRaw = readFileSync(join(FIXTURES, "valid-minimal.json"), "utf-8");
    const base = JSON.parse(baseRaw) as Record<string, unknown>;
    // Break timing sanity without removing traceId.
    (base.timing as Record<string, unknown>).endedAt = "2026-04-17T11:00:00.000Z";
    const line = JSON.stringify(base);
    const r = validateIngestedLine(line);
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.attemptedTraceId).toBe("01KPHTAACKMFPPGJWSKRW8W1KA");
    }
  });

  test("never throws — even on wildly malformed input", () => {
    for (const weird of [
      "",
      "[1,2,3",
      "null",
      "42",
      "\"just a string\"",
      "{\"unclosed\": ",
      "\u0000",
    ]) {
      expect(() => validateIngestedLine(weird)).not.toThrow();
      const r = validateIngestedLine(weird);
      expect(r.ok).toBe(false);
    }
  });
});
