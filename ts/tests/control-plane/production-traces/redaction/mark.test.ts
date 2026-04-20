import { describe, test, expect } from "vitest";
import fc from "fast-check";
import { markRedactions } from "../../../../src/production-traces/redaction/mark.js";
import { defaultRedactionPolicy } from "../../../../src/production-traces/redaction/policy.js";
import { createProductionTrace } from "../../../../src/production-traces/contract/factories.js";
import type {
  ProductionTrace,
  RedactionMarker,
} from "../../../../src/production-traces/contract/types.js";
import type {
  AppId,
  EnvironmentTag,
} from "../../../../src/production-traces/contract/branded-ids.js";
import type { LoadedRedactionPolicy } from "../../../../src/production-traces/redaction/types.js";

function baseInputs() {
  return {
    source: { emitter: "sdk", sdk: { name: "autoctx-ts", version: "0.4.3" } },
    provider: { name: "openai" as const },
    model: "gpt-4o-mini",
    env: {
      environmentTag: "production" as EnvironmentTag,
      appId: "my-app" as AppId,
    },
    timing: {
      startedAt: "2026-04-17T12:00:00.000Z",
      endedAt: "2026-04-17T12:00:01.000Z",
      latencyMs: 1000,
    },
    usage: { tokensIn: 10, tokensOut: 5 },
  };
}

function traceWith(overrides: Partial<Parameters<typeof createProductionTrace>[0]>): ProductionTrace {
  return createProductionTrace({
    ...baseInputs(),
    messages: [
      { role: "user", content: "hi", timestamp: "2026-04-17T12:00:00.000Z" },
    ],
    ...overrides,
  });
}

const FIXED_NOW = "2026-04-17T12:00:00.500Z";

describe("markRedactions", () => {
  const policy = defaultRedactionPolicy();

  test("no sensitive content, no rawProviderPayload → no markers added", () => {
    const trace = traceWith({});
    const out = markRedactions(trace, policy);
    expect(out.redactions).toEqual([]);
  });

  test("detects email addresses in message content", () => {
    const trace = traceWith({
      messages: [
        { role: "user", content: "contact me at alice@example.com please", timestamp: "2026-04-17T12:00:00.000Z" },
      ],
    });
    const out = markRedactions(trace, policy);
    const emailMarker = out.redactions.find((m) => m.category === "pii-email");
    expect(emailMarker).toBeDefined();
    expect(emailMarker!.path).toBe("/messages/0/content");
    expect(emailMarker!.reason).toBe("pii-email");
    expect(emailMarker!.detectedBy).toBe("ingestion");
  });

  test("detects phone numbers", () => {
    const trace = traceWith({
      messages: [
        { role: "user", content: "call me at +1 555-123-4567", timestamp: "2026-04-17T12:00:00.000Z" },
      ],
    });
    const out = markRedactions(trace, policy);
    const phoneMarker = out.redactions.find((m) => m.category === "pii-phone");
    expect(phoneMarker).toBeDefined();
    expect(phoneMarker!.path).toBe("/messages/0/content");
    expect(phoneMarker!.reason).toBe("pii-custom");
  });

  test("detects US SSN patterns", () => {
    const trace = traceWith({
      messages: [
        { role: "user", content: "SSN: 123-45-6789", timestamp: "2026-04-17T12:00:00.000Z" },
      ],
    });
    const out = markRedactions(trace, policy);
    const ssnMarker = out.redactions.find((m) => m.category === "pii-ssn");
    expect(ssnMarker).toBeDefined();
    expect(ssnMarker!.reason).toBe("pii-ssn");
  });

  test("detects credit-card-shaped numbers", () => {
    const trace = traceWith({
      messages: [
        { role: "user", content: "CC 4111 1111 1111 1111", timestamp: "2026-04-17T12:00:00.000Z" },
      ],
    });
    const out = markRedactions(trace, policy);
    const ccMarker = out.redactions.find((m) => m.category === "pii-credit-card");
    expect(ccMarker).toBeDefined();
    expect(ccMarker!.reason).toBe("pii-custom");
  });

  test("detects API-token-shaped strings (secret-token)", () => {
    const trace = traceWith({
      messages: [
        { role: "assistant", content: "using key sk-ant-ABCDEFGHIJKLMNOPQRSTUVWXYZ", timestamp: "2026-04-17T12:00:00.000Z" },
      ],
    });
    const out = markRedactions(trace, policy);
    const tokMarker = out.redactions.find((m) => m.category === "secret-token");
    expect(tokMarker).toBeDefined();
    expect(tokMarker!.reason).toBe("secret-token");
  });

  test("preserves client-provided markers (detectedBy === 'client') unchanged", () => {
    const clientMarker: RedactionMarker = {
      path: "/messages/0/content",
      reason: "pii-custom",
      detectedBy: "client",
      detectedAt: "2026-04-17T11:59:00.000Z",
    };
    const trace = traceWith({
      messages: [
        { role: "user", content: "custom pii we redacted upstream", timestamp: "2026-04-17T12:00:00.000Z" },
      ],
      redactions: [clientMarker],
    });
    const out = markRedactions(trace, policy);

    // Original marker must be present, byte-for-byte.
    expect(out.redactions).toContainEqual(clientMarker);
    // And the first marker must be the client one (client markers come first).
    expect(out.redactions[0]).toEqual(clientMarker);
  });

  test("scans toolCalls[].args for sensitive data (recursive)", () => {
    const trace = traceWith({
      toolCalls: [
        {
          toolName: "send_email",
          args: { to: "user@example.com", nested: { cc: "cc@example.com" } },
        },
      ],
    });
    const out = markRedactions(trace, policy);
    const paths = out.redactions.map((m) => m.path).sort();
    // Both email paths should be detected.
    expect(paths).toEqual(
      expect.arrayContaining([
        "/toolCalls/0/args/nested/cc",
        "/toolCalls/0/args/to",
      ]),
    );
  });

  test("scans outcome.reasoning and feedbackRefs[].comment", () => {
    const trace = traceWith({
      outcome: {
        label: "success",
        reasoning: "user provided email foo@bar.com",
      },
      feedbackRefs: [
        {
          kind: "custom",
          submittedAt: "2026-04-17T12:00:02.000Z",
          ref: "fbref_abc",
          comment: "followup at bob@baz.com",
        } as ProductionTrace["feedbackRefs"][number],
      ],
    });
    const out = markRedactions(trace, policy);
    const paths = out.redactions.map((m) => m.path);
    expect(paths).toContain("/outcome/reasoning");
    expect(paths).toContain("/feedbackRefs/0/comment");
  });

  test("adds blanket rawProviderPayload marker when field is present, not otherwise", () => {
    const traceWithoutRaw = traceWith({});
    const outA = markRedactions(traceWithoutRaw, policy);
    expect(outA.redactions.find((m) => m.path === "/metadata/rawProviderPayload")).toBeUndefined();

    const traceWithRaw = traceWith({
      metadata: { rawProviderPayload: { anything: "here" } },
    });
    const outB = markRedactions(traceWithRaw, policy);
    const rawMarker = outB.redactions.find((m) => m.path === "/metadata/rawProviderPayload");
    expect(rawMarker).toBeDefined();
    expect(rawMarker!.reason).toBe("pii-custom");
    expect(rawMarker!.category).toBe("raw-provider-payload");
    expect(rawMarker!.detectedBy).toBe("ingestion");
  });

  test("does not descend into rawProviderPayload subtree (only blanket marker at that path)", () => {
    const trace = traceWith({
      metadata: {
        rawProviderPayload: {
          deep: { contact: "alice@example.com" },
        },
      },
    });
    const out = markRedactions(trace, policy);
    // No child markers under /metadata/rawProviderPayload.
    const descendants = out.redactions.filter(
      (m) =>
        m.path.startsWith("/metadata/rawProviderPayload/") && m.path !== "/metadata/rawProviderPayload",
    );
    expect(descendants.length).toBe(0);
  });

  test("applies custom patterns from policy", () => {
    const custom: LoadedRedactionPolicy = {
      ...policy,
      customPatterns: [
        {
          name: "internal-ticket-id",
          regex: "TICKET-\\d{6,}",
          category: "pii-custom",
          reason: "pii-custom",
        },
      ],
    };
    const trace = traceWith({
      messages: [
        { role: "user", content: "see TICKET-123456 for details", timestamp: "2026-04-17T12:00:00.000Z" },
      ],
    });
    const out = markRedactions(trace, custom);
    const m = out.redactions.find((mk) => mk.category === "pii-custom" && mk.path === "/messages/0/content");
    expect(m).toBeDefined();
  });

  test("deduplicates same (path, category) into one marker", () => {
    // Two different email matches on the same field — we expect one marker
    // per (path, category), not per match.
    const trace = traceWith({
      messages: [
        {
          role: "user",
          content: "ping alice@example.com AND bob@example.com",
          timestamp: "2026-04-17T12:00:00.000Z",
        },
      ],
    });
    const out = markRedactions(trace, policy);
    const emailMarkers = out.redactions.filter(
      (m) => m.category === "pii-email" && m.path === "/messages/0/content",
    );
    expect(emailMarkers.length).toBe(1);
  });

  test("deterministic output for same input+policy (50 runs)", () => {
    // `detectedAt` is a wall-clock stamp that naturally varies between calls
    // — the determinism guarantee is over the marker identity (path, reason,
    // category, detectedBy). Pin the timestamp via the optional `nowIso`
    // parameter to make equality checks crisp.
    fc.assert(
      fc.property(
        fc.constantFrom(
          "hi alice@example.com",
          "ssn 123-45-6789",
          "no sensitive data",
          "call 555-123-4567 or foo@bar.com",
        ),
        (content) => {
          const trace = traceWith({
            messages: [
              { role: "user", content, timestamp: "2026-04-17T12:00:00.000Z" },
            ],
          });
          const a = markRedactions(trace, policy, FIXED_NOW).redactions;
          const b = markRedactions(trace, policy, FIXED_NOW).redactions;
          expect(a).toEqual(b);
        },
      ),
      { numRuns: 50 },
    );
  });

  test("P4 property: client markers survive unchanged across many random inputs (100 runs)", () => {
    const clientMarkerArb = fc.record({
      path: fc.constantFrom(
        "/messages/0/content",
        "/metadata",
        "/outcome/reasoning",
      ),
      reason: fc.constantFrom<RedactionMarker["reason"]>(
        "pii-email",
        "pii-name",
        "pii-ssn",
        "secret-token",
        "pii-custom",
      ),
      detectedBy: fc.constant<"client">("client"),
      detectedAt: fc.constant("2026-04-17T11:59:00.000Z"),
    });

    fc.assert(
      fc.property(fc.array(clientMarkerArb, { minLength: 0, maxLength: 10 }), (markers) => {
        const trace = traceWith({
          messages: [
            {
              role: "user",
              content: "maybe contains pii like alice@example.com; maybe not",
              timestamp: "2026-04-17T12:00:00.000Z",
            },
          ],
          redactions: markers as RedactionMarker[],
        });
        const out = markRedactions(trace, policy);
        // Every client marker is in the output, field-by-field equal.
        for (const m of markers) {
          expect(out.redactions).toContainEqual(m);
        }
      }),
      { numRuns: 100 },
    );
  });

  test("returns a new trace object (does not mutate input)", () => {
    const trace = traceWith({
      messages: [
        { role: "user", content: "email me at alice@example.com", timestamp: "2026-04-17T12:00:00.000Z" },
      ],
    });
    const snapshot = JSON.stringify(trace);
    const out = markRedactions(trace, policy);
    expect(out).not.toBe(trace);
    expect(JSON.stringify(trace)).toBe(snapshot);
  });

  test("autoDetect.enabled: false skips auto-detection but keeps client markers and raw-provider blanket", () => {
    const disabled: LoadedRedactionPolicy = {
      ...policy,
      autoDetect: { ...policy.autoDetect, enabled: false },
    };
    const clientMarker: RedactionMarker = {
      path: "/messages/0/content",
      reason: "pii-custom",
      detectedBy: "client",
      detectedAt: "2026-04-17T11:59:00.000Z",
    };
    const trace = traceWith({
      messages: [
        { role: "user", content: "alice@example.com", timestamp: "2026-04-17T12:00:00.000Z" },
      ],
      redactions: [clientMarker],
      metadata: { rawProviderPayload: { x: 1 } },
    });
    const out = markRedactions(trace, disabled);
    // Auto-detection markers should NOT appear (no pii-email marker from ingestion).
    expect(out.redactions.find((m) => m.category === "pii-email" && m.detectedBy === "ingestion")).toBeUndefined();
    // Client marker preserved.
    expect(out.redactions).toContainEqual(clientMarker);
    // Blanket rawProviderPayload marker still added (policy §7.2 step 3 is independent of autoDetect.enabled).
    expect(out.redactions.find((m) => m.path === "/metadata/rawProviderPayload")).toBeDefined();
  });
});
