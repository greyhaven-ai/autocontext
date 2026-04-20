import { describe, test, expect } from "vitest";
import fc from "fast-check";
import { createHash } from "node:crypto";
import { applyRedactions } from "../../../../src/production-traces/redaction/apply.js";
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

const SALT = "a".repeat(64);

describe("applyRedactions", () => {
  const policy = defaultRedactionPolicy();

  test("default action `redact` replaces targeted field with placeholder", () => {
    const trace = traceWith({
      messages: [
        { role: "user", content: "contact alice@example.com", timestamp: "2026-04-17T12:00:00.000Z" },
      ],
      redactions: [
        {
          path: "/messages/0/content",
          reason: "pii-email",
          category: "pii-email",
          detectedBy: "ingestion",
          detectedAt: "2026-04-17T12:00:00.500Z",
        },
      ],
    });
    const out = applyRedactions(trace, policy, SALT);
    expect(out.messages[0].content).toBe("[redacted]");
  });

  test("custom `placeholder` on categoryOverrides is used", () => {
    const custom: LoadedRedactionPolicy = {
      ...policy,
      exportPolicy: {
        ...policy.exportPolicy,
        categoryOverrides: {
          "pii-email": { action: "redact", placeholder: "[EMAIL]" },
        },
      },
    };
    const trace = traceWith({
      messages: [
        { role: "user", content: "alice@example.com", timestamp: "2026-04-17T12:00:00.000Z" },
      ],
      redactions: [
        {
          path: "/messages/0/content",
          reason: "pii-email",
          category: "pii-email",
          detectedBy: "ingestion",
          detectedAt: "2026-04-17T12:00:00.500Z",
        },
      ],
    });
    const out = applyRedactions(trace, custom, SALT);
    expect(out.messages[0].content).toBe("[EMAIL]");
  });

  test("action `hash` produces deterministic sha256:<hex> with the install salt", () => {
    const custom: LoadedRedactionPolicy = {
      ...policy,
      exportPolicy: {
        ...policy.exportPolicy,
        categoryOverrides: {
          "pii-email": { action: "hash" },
        },
      },
    };
    const trace = traceWith({
      messages: [
        { role: "user", content: "alice@example.com", timestamp: "2026-04-17T12:00:00.000Z" },
      ],
      redactions: [
        {
          path: "/messages/0/content",
          reason: "pii-email",
          category: "pii-email",
          detectedBy: "ingestion",
          detectedAt: "2026-04-17T12:00:00.500Z",
        },
      ],
    });
    const out = applyRedactions(trace, custom, SALT);
    const expectedHex = createHash("sha256").update(SALT + "alice@example.com").digest("hex");
    expect(out.messages[0].content).toBe(`sha256:${expectedHex}`);
  });

  test("action `preserve` leaves the field unchanged", () => {
    const custom: LoadedRedactionPolicy = {
      ...policy,
      exportPolicy: {
        ...policy.exportPolicy,
        categoryOverrides: {
          "pii-email": { action: "preserve" },
        },
      },
    };
    const trace = traceWith({
      messages: [
        { role: "user", content: "alice@example.com", timestamp: "2026-04-17T12:00:00.000Z" },
      ],
      redactions: [
        {
          path: "/messages/0/content",
          reason: "pii-email",
          category: "pii-email",
          detectedBy: "ingestion",
          detectedAt: "2026-04-17T12:00:00.500Z",
        },
      ],
    });
    const out = applyRedactions(trace, custom, SALT);
    expect(out.messages[0].content).toBe("alice@example.com");
  });

  test("action `drop` removes the field entirely", () => {
    const custom: LoadedRedactionPolicy = {
      ...policy,
      exportPolicy: {
        ...policy.exportPolicy,
        categoryOverrides: {
          "pii-phone": { action: "drop" },
        },
      },
    };
    const trace = traceWith({
      outcome: {
        label: "success",
        reasoning: "customer called about issue",
      },
      redactions: [
        {
          path: "/outcome/reasoning",
          reason: "pii-custom",
          category: "pii-phone",
          detectedBy: "ingestion",
          detectedAt: "2026-04-17T12:00:00.500Z",
        },
      ],
    });
    const out = applyRedactions(trace, custom, SALT);
    // reasoning should be removed; outcome.label still there.
    expect(out.outcome?.reasoning).toBeUndefined();
    expect(out.outcome?.label).toBe("success");
  });

  test("preserveLength: true produces same-length placeholder via deterministic fill", () => {
    const custom: LoadedRedactionPolicy = {
      ...policy,
      exportPolicy: {
        ...policy.exportPolicy,
        placeholder: "#",
        preserveLength: true,
      },
    };
    const original = "alice@example.com"; // 17 chars
    const trace = traceWith({
      messages: [
        { role: "user", content: original, timestamp: "2026-04-17T12:00:00.000Z" },
      ],
      redactions: [
        {
          path: "/messages/0/content",
          reason: "pii-email",
          category: "pii-email",
          detectedBy: "ingestion",
          detectedAt: "2026-04-17T12:00:00.500Z",
        },
      ],
    });
    const out = applyRedactions(trace, custom, SALT);
    expect(out.messages[0].content.length).toBe(original.length);
  });

  test("rawProviderPayload subtree is stripped by default (includeRawProviderPayload: false)", () => {
    const trace = traceWith({
      metadata: {
        rawProviderPayload: { some: "provider-specific-data" },
        other: "keep-me",
      },
      redactions: [
        {
          path: "/metadata/rawProviderPayload",
          reason: "pii-custom",
          category: "raw-provider-payload",
          detectedBy: "ingestion",
          detectedAt: "2026-04-17T12:00:00.500Z",
        },
      ],
    });
    const out = applyRedactions(trace, policy, SALT);
    const meta = out.metadata as Record<string, unknown>;
    expect(meta).toBeDefined();
    expect("rawProviderPayload" in meta).toBe(false);
    expect(meta.other).toBe("keep-me");
  });

  test("rawProviderPayload preserved when includeRawProviderPayload: true", () => {
    const custom: LoadedRedactionPolicy = {
      ...policy,
      exportPolicy: { ...policy.exportPolicy, includeRawProviderPayload: true },
    };
    const trace = traceWith({
      metadata: {
        rawProviderPayload: { some: "data" },
      },
      redactions: [
        {
          path: "/metadata/rawProviderPayload",
          reason: "pii-custom",
          category: "raw-provider-payload",
          detectedBy: "ingestion",
          detectedAt: "2026-04-17T12:00:00.500Z",
        },
      ],
    });
    const out = applyRedactions(trace, custom, SALT);
    const meta = out.metadata as Record<string, unknown>;
    expect(meta.rawProviderPayload).toEqual({ some: "data" });
  });

  test("does not mutate input trace", () => {
    const trace = traceWith({
      messages: [
        { role: "user", content: "alice@example.com", timestamp: "2026-04-17T12:00:00.000Z" },
      ],
      redactions: [
        {
          path: "/messages/0/content",
          reason: "pii-email",
          category: "pii-email",
          detectedBy: "ingestion",
          detectedAt: "2026-04-17T12:00:00.500Z",
        },
      ],
    });
    const snapshot = JSON.stringify(trace);
    applyRedactions(trace, policy, SALT);
    expect(JSON.stringify(trace)).toBe(snapshot);
  });

  test("round-trip mark+apply redacts detected emails with default policy", () => {
    const trace = traceWith({
      messages: [
        { role: "user", content: "ping alice@example.com please", timestamp: "2026-04-17T12:00:00.000Z" },
      ],
    });
    const marked = markRedactions(trace, policy);
    const exported = applyRedactions(marked, policy, SALT);
    expect(exported.messages[0].content).toBe("[redacted]");
  });

  test("handles markers on tool call args (nested path, dropped)", () => {
    const trace = traceWith({
      toolCalls: [
        {
          toolName: "send_email",
          args: { to: "foo@bar.com", nested: { cc: "baz@qux.com" } },
        },
      ],
      redactions: [
        {
          path: "/toolCalls/0/args/nested/cc",
          reason: "pii-email",
          category: "pii-email",
          detectedBy: "ingestion",
          detectedAt: "2026-04-17T12:00:00.500Z",
        },
      ],
    });
    const out = applyRedactions(trace, policy, SALT);
    // The nested.cc field should be replaced with the placeholder.
    const nested = out.toolCalls[0].args.nested as Record<string, unknown>;
    expect(nested.cc).toBe("[redacted]");
    // The sibling `to` field is untouched.
    expect(out.toolCalls[0].args.to).toBe("foo@bar.com");
  });

  test("hash determinism property (50 runs): same input+salt → same hash", () => {
    fc.assert(
      fc.property(
        fc.string({ minLength: 1, maxLength: 80 }),
        fc.string({ minLength: 8, maxLength: 80 }),
        (content, salt) => {
          const custom: LoadedRedactionPolicy = {
            ...policy,
            exportPolicy: {
              ...policy.exportPolicy,
              categoryOverrides: {
                "pii-email": { action: "hash" },
              },
            },
          };
          const trace = traceWith({
            messages: [
              { role: "user", content, timestamp: "2026-04-17T12:00:00.000Z" },
            ],
            redactions: [
              {
                path: "/messages/0/content",
                reason: "pii-email",
                category: "pii-email",
                detectedBy: "ingestion",
                detectedAt: "2026-04-17T12:00:00.500Z",
              },
            ],
          });
          const a = applyRedactions(trace, custom, salt).messages[0].content;
          const b = applyRedactions(trace, custom, salt).messages[0].content;
          expect(a).toBe(b);
          expect(a.startsWith("sha256:")).toBe(true);
        },
      ),
      { numRuns: 50 },
    );
  });

  test("category lookup falls back to `reason` when `category` is absent", () => {
    const custom: LoadedRedactionPolicy = {
      ...policy,
      exportPolicy: {
        ...policy.exportPolicy,
        categoryOverrides: {
          "pii-email": { action: "preserve" },
        },
      },
    };
    const trace = traceWith({
      messages: [
        { role: "user", content: "alice@example.com", timestamp: "2026-04-17T12:00:00.000Z" },
      ],
      redactions: [
        {
          path: "/messages/0/content",
          reason: "pii-email",
          detectedBy: "client",
          detectedAt: "2026-04-17T11:59:00.000Z",
        } as RedactionMarker,
      ],
    });
    const out = applyRedactions(trace, custom, SALT);
    // Preserve via reason match on "pii-email"
    expect(out.messages[0].content).toBe("alice@example.com");
  });

  test("hash action without install salt: falls back to unsalted hashing (documented)", () => {
    const custom: LoadedRedactionPolicy = {
      ...policy,
      exportPolicy: {
        ...policy.exportPolicy,
        categoryOverrides: {
          "pii-email": { action: "hash" },
        },
      },
    };
    const trace = traceWith({
      messages: [
        { role: "user", content: "alice@example.com", timestamp: "2026-04-17T12:00:00.000Z" },
      ],
      redactions: [
        {
          path: "/messages/0/content",
          reason: "pii-email",
          category: "pii-email",
          detectedBy: "ingestion",
          detectedAt: "2026-04-17T12:00:00.500Z",
        },
      ],
    });
    const out = applyRedactions(trace, custom, null);
    const expectedHex = createHash("sha256").update("alice@example.com").digest("hex");
    expect(out.messages[0].content).toBe(`sha256:${expectedHex}`);
  });

  test("hashSalt override in categoryOverride takes precedence over install salt", () => {
    const OVERRIDE_SALT = "b".repeat(32);
    const custom: LoadedRedactionPolicy = {
      ...policy,
      exportPolicy: {
        ...policy.exportPolicy,
        categoryOverrides: {
          "pii-email": { action: "hash", hashSalt: OVERRIDE_SALT },
        },
      },
    };
    const trace = traceWith({
      messages: [
        { role: "user", content: "alice@example.com", timestamp: "2026-04-17T12:00:00.000Z" },
      ],
      redactions: [
        {
          path: "/messages/0/content",
          reason: "pii-email",
          category: "pii-email",
          detectedBy: "ingestion",
          detectedAt: "2026-04-17T12:00:00.500Z",
        },
      ],
    });
    const out = applyRedactions(trace, custom, SALT);
    const expectedHex = createHash("sha256").update(OVERRIDE_SALT + "alice@example.com").digest("hex");
    expect(out.messages[0].content).toBe(`sha256:${expectedHex}`);
  });

  test("marker with unresolvable path is silently ignored (never throws)", () => {
    const trace = traceWith({
      messages: [
        { role: "user", content: "alice@example.com", timestamp: "2026-04-17T12:00:00.000Z" },
      ],
      redactions: [
        {
          path: "/nonexistent/path",
          reason: "pii-email",
          category: "pii-email",
          detectedBy: "ingestion",
          detectedAt: "2026-04-17T12:00:00.500Z",
        },
        {
          path: "/messages/0/content",
          reason: "pii-email",
          category: "pii-email",
          detectedBy: "ingestion",
          detectedAt: "2026-04-17T12:00:00.500Z",
        },
      ],
    });
    expect(() => applyRedactions(trace, policy, SALT)).not.toThrow();
    const out = applyRedactions(trace, policy, SALT);
    expect(out.messages[0].content).toBe("[redacted]");
  });
});
