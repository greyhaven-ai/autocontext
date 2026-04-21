import { describe, test, expect } from "vitest";
import {
  validateProductionTrace,
  validateProductionTraceDict,
  ValidationError,
} from "../../../src/production-traces/sdk/validate.js";
import { createProductionTrace } from "../../../src/production-traces/contract/factories.js";
import type {
  AppId,
  EnvironmentTag,
} from "../../../src/production-traces/contract/branded-ids.js";

function validTraceDocument() {
  return createProductionTrace({
    source: { emitter: "sdk", sdk: { name: "autocontext-ts", version: "0.0.0" } },
    provider: { name: "openai" },
    model: "gpt-4o-mini",
    env: {
      environmentTag: "production" as EnvironmentTag,
      appId: "my-app" as AppId,
    },
    messages: [
      { role: "user", content: "hi", timestamp: "2026-04-17T12:00:00.000Z" },
    ],
    timing: {
      startedAt: "2026-04-17T12:00:00.000Z",
      endedAt: "2026-04-17T12:00:01.000Z",
      latencyMs: 1000,
    },
    usage: { tokensIn: 10, tokensOut: 5 },
  });
}

describe("validateProductionTrace (throwing)", () => {
  test("returns the validated trace on success", () => {
    const trace = validTraceDocument();
    const out = validateProductionTrace(trace);
    expect(out).toBe(trace); // Same reference; contract is to return, not to clone.
  });

  test("throws ValidationError with fieldErrors on malformed input", () => {
    const bad = { ...validTraceDocument(), provider: { name: "not-a-provider" } };
    expect(() => validateProductionTrace(bad)).toThrow(ValidationError);
    try {
      validateProductionTrace(bad);
    } catch (err) {
      expect(err).toBeInstanceOf(ValidationError);
      const ve = err as ValidationError;
      expect(ve.fieldErrors.length).toBeGreaterThan(0);
      // Message should be non-empty and summarize at least one field error.
      expect(ve.message.length).toBeGreaterThan(0);
    }
  });

  test("throws ValidationError on non-object inputs", () => {
    expect(() => validateProductionTrace(null)).toThrow(ValidationError);
    expect(() => validateProductionTrace("not a trace")).toThrow(ValidationError);
    expect(() => validateProductionTrace(42)).toThrow(ValidationError);
  });
});

describe("validateProductionTraceDict (non-throwing)", () => {
  test("returns { valid: true, errors: [] } on valid input", () => {
    const result = validateProductionTraceDict(validTraceDocument());
    expect(result.valid).toBe(true);
    expect(result.errors).toEqual([]);
  });

  test("returns { valid: false, errors: [...] } on invalid input", () => {
    const bad = { ...validTraceDocument(), provider: { name: "not-a-provider" } };
    const result = validateProductionTraceDict(bad);
    expect(result.valid).toBe(false);
    expect(result.errors.length).toBeGreaterThan(0);
    for (const msg of result.errors) {
      expect(typeof msg).toBe("string");
      expect(msg.length).toBeGreaterThan(0);
    }
  });

  test("does not throw on non-object inputs", () => {
    expect(() => validateProductionTraceDict(null)).not.toThrow();
    expect(() => validateProductionTraceDict(42)).not.toThrow();
    const r = validateProductionTraceDict(null);
    expect(r.valid).toBe(false);
  });
});

describe("ValidationError class shape", () => {
  test("is an Error subclass with readonly fieldErrors", () => {
    const err = new ValidationError("something broke", ["/foo bad"]);
    expect(err).toBeInstanceOf(Error);
    expect(err).toBeInstanceOf(ValidationError);
    expect(err.name).toBe("ValidationError");
    expect(err.message).toBe("something broke");
    expect(err.fieldErrors).toEqual(["/foo bad"]);
  });
});
