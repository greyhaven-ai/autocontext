import { describe, test, expect } from "vitest";
import { markRedactions } from "../../../../src/production-traces/ingest/redaction-phase.js";
import { defaultRedactionPolicy } from "../../../../src/production-traces/redaction/policy.js";
import { createProductionTrace } from "../../../../src/production-traces/contract/factories.js";
import type {
  AppId,
  EnvironmentTag,
} from "../../../../src/production-traces/contract/branded-ids.js";

describe("redaction-phase seam (Layer 4 wiring)", () => {
  const policy = defaultRedactionPolicy();
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

  test("delegates to redaction/mark.ts; returns a trace with expanded redactions[]", () => {
    const trace = createProductionTrace({
      ...minInputs,
      messages: [
        { role: "user", content: "ping alice@example.com", timestamp: "2026-04-17T12:00:00.000Z" },
      ],
    });
    const out = markRedactions(trace, policy);
    const email = out.redactions.find((m) => m.category === "pii-email");
    expect(email).toBeDefined();
    expect(email!.path).toBe("/messages/0/content");
  });

  test("preserves an existing client-provided redactions[] array as-is", () => {
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
    const out = markRedactions(trace, policy);
    expect(out.redactions[0]).toEqual(trace.redactions[0]);
  });
});
