import { describe, test, expect } from "vitest";
import { createProductionTrace } from "../../../../src/production-traces/contract/factories.js";
import { validateProductionTrace } from "../../../../src/production-traces/contract/validators.js";
import type { ProductionTrace } from "../../../../src/production-traces/contract/types.js";
import type { AppId, EnvironmentTag } from "../../../../src/production-traces/contract/branded-ids.js";

describe("createProductionTrace", () => {
  const minInputs = {
    source: { emitter: "sdk", sdk: { name: "autoctx-ts", version: "0.4.3" } },
    provider: { name: "openai" as const },
    model: "gpt-4o-mini",
    env: {
      environmentTag: "production" as EnvironmentTag,
      appId: "my-app" as AppId,
    },
    messages: [{ role: "user" as const, content: "hi", timestamp: "2026-04-17T12:00:00.000Z" }],
    timing: {
      startedAt: "2026-04-17T12:00:00.000Z",
      endedAt: "2026-04-17T12:00:01.000Z",
      latencyMs: 1000,
    },
    usage: { tokensIn: 10, tokensOut: 5 },
  };

  test("returns a schema-valid trace with defaults applied", () => {
    const t = createProductionTrace(minInputs);
    expect(t.schemaVersion).toBe("1.0");
    expect(t.traceId).toMatch(/^[0-9A-HJKMNP-TV-Z]{26}$/);
    expect(t.toolCalls).toEqual([]);
    expect(t.feedbackRefs).toEqual([]);
    expect(t.redactions).toEqual([]);
    expect(t.links).toEqual({});
    const r = validateProductionTrace(t);
    if (!r.valid) {
      // eslint-disable-next-line no-console
      console.error(r.errors);
    }
    expect(r.valid).toBe(true);
  });

  test("uses provided traceId when passed", () => {
    const traceId = "01KFDQ9XZ3M7RT2V8K1PHY4BNC" as ProductionTrace["traceId"];
    const t = createProductionTrace({ ...minInputs, id: traceId });
    expect(t.traceId).toBe(traceId);
  });

  test("preserves caller-supplied arrays and optional fields", () => {
    const t = createProductionTrace({
      ...minInputs,
      toolCalls: [{ toolName: "search", args: { q: "x" } }],
      feedbackRefs: [
        { kind: "thumbs", submittedAt: "2026-04-17T12:05:00.000Z", ref: "fb-1" as ProductionTrace["feedbackRefs"][number]["ref"] },
      ],
      redactions: [
        {
          path: "/messages/0/content",
          reason: "pii-email",
          detectedBy: "client",
          detectedAt: "2026-04-17T12:00:00.000Z",
        },
      ],
      links: { scenarioId: "grid_ctf" as ProductionTrace["links"]["scenarioId"] },
      outcome: { label: "success", score: 0.9 },
      session: { requestId: "req-1" },
      metadata: { customerTier: "pro" },
    });
    expect(t.toolCalls).toHaveLength(1);
    expect(t.feedbackRefs).toHaveLength(1);
    expect(t.redactions).toHaveLength(1);
    expect(t.links.scenarioId).toBe("grid_ctf");
    expect(t.outcome?.label).toBe("success");
    expect(t.session?.requestId).toBe("req-1");
    expect(t.metadata?.customerTier).toBe("pro");
    expect(validateProductionTrace(t).valid).toBe(true);
  });

  test("different calls produce different traceIds (ULID entropy)", () => {
    const a = createProductionTrace(minInputs);
    const b = createProductionTrace(minInputs);
    expect(a.traceId).not.toBe(b.traceId);
  });
});
