import { describe, test } from "vitest";
import fc from "fast-check";
import { buildTrace, type BuildTraceInputs } from "../../../src/production-traces/sdk/build-trace.js";
import { canonicalJsonStringify } from "../../../src/control-plane/contract/canonical-json.js";
import type {
  AppId,
  EnvironmentTag,
  ProductionTraceId,
} from "../../../src/production-traces/contract/branded-ids.js";

/**
 * P-buildtrace-idempotent — spec §5.4.
 *
 * Given inputs with an injected ``traceId`` and an explicit ``source``,
 * ``buildTrace(x)`` must produce a canonical JSON serialization that equals
 * ``canonicalJsonStringify(buildTrace(x))``. This pins determinism so that
 * any accidental reintroduction of ``new Date()`` / ``ulid()`` / similar
 * non-deterministic defaults into the assembled trace breaks CI loud.
 *
 * 100 runs.
 */

// ---- fast-check arbitraries ----

const isoTimestampArb = fc
  .integer({ min: 1_600_000_000_000, max: 2_100_000_000_000 })
  .map((ms) => new Date(ms).toISOString());

const validBuildTraceInputsArb: fc.Arbitrary<BuildTraceInputs> = fc.record({
  provider: fc.constantFrom(
    "openai",
    "anthropic",
    "openai-compatible",
    "langchain",
    "vercel-ai-sdk",
    "litellm",
    "other",
  ),
  model: fc.string({ minLength: 1, maxLength: 20 }).filter((s) => s.length > 0),
  messages: fc.array(
    fc.record({
      role: fc.constantFrom("user", "assistant", "system", "tool") as fc.Arbitrary<
        "user" | "assistant" | "system" | "tool"
      >,
      content: fc.string({ maxLength: 50 }),
      timestamp: isoTimestampArb,
    }),
    { minLength: 1, maxLength: 4 },
  ),
  timing: fc.record({
    startedAt: isoTimestampArb,
    endedAt: isoTimestampArb,
    latencyMs: fc.integer({ min: 0, max: 60_000 }),
  }),
  usage: fc.record({
    tokensIn: fc.integer({ min: 0, max: 10_000 }),
    tokensOut: fc.integer({ min: 0, max: 10_000 }),
  }),
  env: fc.record({
    environmentTag: fc.constantFrom("production", "staging", "development") as fc.Arbitrary<EnvironmentTag>,
    appId: fc.constantFrom("app-a", "app-b", "my-app") as fc.Arbitrary<AppId>,
  }),
  traceId: fc.constant("01HZ6X2K7M9A3B4C5D6E7F8G9H" as ProductionTraceId),
  source: fc.constant({
    emitter: "sdk" as const,
    sdk: { name: "autocontext-ts", version: "0.0.0" },
  }),
});

describe("P-buildtrace-idempotent (property, 100 runs)", () => {
  test("buildTrace is deterministic given injected traceId + explicit source", () => {
    fc.assert(
      fc.property(validBuildTraceInputsArb, (inputs) => {
        const a = canonicalJsonStringify(buildTrace(inputs));
        const b = canonicalJsonStringify(buildTrace(inputs));
        return a === b;
      }),
      { numRuns: 100 },
    );
  });
});
