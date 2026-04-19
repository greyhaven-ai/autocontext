import { describe, test, expect } from "vitest";
import { markRedactions } from "../../../../src/production-traces/ingest/redaction-phase.js";
import { createProductionTrace } from "../../../../src/production-traces/contract/factories.js";
import type {
  AppId,
  EnvironmentTag,
} from "../../../../src/production-traces/contract/branded-ids.js";

describe("markRedactions (Layer 3 passthrough)", () => {
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

  test("returns the trace unchanged (structural equality) in Layer 3", () => {
    const trace = createProductionTrace(minInputs);
    const out = markRedactions(trace);
    expect(out).toBe(trace);
  });

  test("preserves an existing redactions[] array as-is", () => {
    const trace = createProductionTrace({
      ...minInputs,
      redactions: [
        {
          path: "/messages/0/content",
          reason: "pii-custom",
          detectedBy: "client",
          detectedAt: "2026-04-17T12:00:00.500Z",
        },
      ],
    });
    const out = markRedactions(trace);
    expect(out.redactions).toEqual(trace.redactions);
  });
});
