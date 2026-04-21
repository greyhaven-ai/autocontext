import { describe, test } from "vitest";
import fc from "fast-check";
import { buildTrace, type BuildTraceInputs } from "../../../src/production-traces/sdk/build-trace.js";
import { canonicalJsonStringify } from "../../../src/control-plane/contract/canonical-json.js";
import {
  callPythonBuildTrace,
  isPythonParityAvailable,
} from "../../_helpers/python-runner.js";
import type {
  AppId,
  EnvironmentTag,
  ProductionTraceId,
} from "../../../src/production-traces/contract/branded-ids.js";

/**
 * P-cross-runtime-emit-parity — spec §5.2, 50 runs.
 *
 * Generates a valid ``BuildTraceInputs`` via fast-check (restricted to the
 * intersection of inputs both SDKs accept). Calls TS ``buildTrace``,
 * canonicalizes; calls Python ``build_trace`` via subprocess helper and
 * captures its canonical output. Asserts byte-for-byte equality.
 *
 * This is THE critical safety invariant for A2-II-a: any divergence here
 * means customer traces drift silently between Python-emit and TS-emit
 * installs. The test is stopped loudly before shipping.
 *
 * Gated on ``isPythonParityAvailable()`` so local contributors without the
 * Python venv can still run the TS-only suite green.
 */

const parity = isPythonParityAvailable();
const maybeSuite = parity ? describe : describe.skip;

// --- Arbitraries restricted to the schema-accepted intersection ---

// Only printable-ASCII content so round-tripping through JSON + stdin is
// lossless; JSON string escaping differs subtly between Python's `json.dumps`
// and TS's `JSON.stringify` for some Unicode edge cases (e.g. lone surrogates).
// For the 50-run parity assertion we stay on the trivially-equal subset.
const asciiStr = (opts: { minLength?: number; maxLength?: number }) =>
  fc
    .string({
      ...opts,
      unit: fc.constantFrom(
        ..."abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 _-.".split(""),
      ),
    })
    .filter((s) => s.length >= (opts.minLength ?? 0));

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
  model: asciiStr({ minLength: 1, maxLength: 20 }),
  messages: fc.array(
    fc.record({
      role: fc.constantFrom("user", "assistant", "system", "tool") as fc.Arbitrary<
        "user" | "assistant" | "system" | "tool"
      >,
      content: asciiStr({ minLength: 0, maxLength: 40 }),
      timestamp: isoTimestampArb,
    }),
    { minLength: 1, maxLength: 3 },
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
    appId: fc.constantFrom("app-a", "app-b", "my-app", "bot-x") as fc.Arbitrary<AppId>,
  }),
  traceId: fc.constant("01HZ6X2K7M9A3B4C5D6E7F8G9H" as ProductionTraceId),
  source: fc.constant({
    emitter: "sdk" as const,
    sdk: { name: "autocontext-ts", version: "0.0.0" },
  }),
});

maybeSuite("P-cross-runtime-emit-parity (property, 50 runs)", () => {
  test("TS buildTrace and Python build_trace produce byte-identical canonical JSON", () => {
    fc.assert(
      fc.property(validBuildTraceInputsArb, (inputs) => {
        const tsCanonical = canonicalJsonStringify(buildTrace(inputs));
        const pyCanonical = callPythonBuildTrace(inputs);
        return tsCanonical === pyCanonical;
      }),
      { numRuns: 50 },
    );
  }, 180_000);
});
