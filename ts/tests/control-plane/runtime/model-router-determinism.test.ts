// Property test P-det (spec §4, AC-545 stated property test).
//
// Over a fast-check generator of (config, context) pairs, chooseModel returns
// identical decisions when given identical inputs + same nowIso. 100 runs.

import { describe, test } from "vitest";
import fc from "fast-check";
import { chooseModel } from "../../../src/control-plane/runtime/model-router.js";
import type {
  ChooseModelInputs,
  ModelRouterContext,
} from "../../../src/control-plane/runtime/model-router.js";
import type { ModelRoutingPayload } from "../../../src/control-plane/actuators/model-routing/schema.js";

const NOW = "2026-04-17T12:00:00.000Z";

// Small arbitrary of model targets.
const arbTarget = fc.record({
  provider: fc.constantFrom("anthropic", "openai", "openai-compatible"),
  model: fc.stringMatching(/^[a-z0-9-]+$/).filter((s) => s.length > 0 && s.length < 40),
  endpoint: fc.option(fc.webUrl(), { nil: null }),
});

const arbMatch = fc.oneof(
  fc.record({ "env.taskType": fc.record({ equals: fc.string() }) }),
  fc.record({ "env.taskType": fc.record({ default: fc.constant(true as const) }) }),
  fc.record({ "session.sessionIdHash": fc.record({ equals: fc.string() }) }),
  fc.constant<Record<string, never>>({}),
);

const arbRoute = fc.record({
  id: fc.stringMatching(/^[a-z][a-z0-9-]*$/).filter((s) => s.length > 0 && s.length < 30),
  match: arbMatch,
  target: arbTarget,
  rollout: fc.option(
    fc.record({
      percent: fc.integer({ min: 0, max: 100 }),
      cohortKey: fc.constantFrom("session.sessionIdHash", "env.tenant"),
    }),
    { nil: undefined },
  ),
  budget: fc.option(fc.record({ maxCostUsdPerCall: fc.double({ min: 0, max: 1, noNaN: true }) }), {
    nil: undefined,
  }),
  latency: fc.option(fc.record({ maxP95Ms: fc.integer({ min: 0, max: 10000 }) }), {
    nil: undefined,
  }),
  confidence: fc.option(
    fc.record({ minScore: fc.double({ min: 0, max: 1, noNaN: true }) }),
    { nil: undefined },
  ),
});

const arbConfig = fc.record({
  schemaVersion: fc.constant("1.0" as const),
  default: arbTarget,
  routes: fc.array(arbRoute, { maxLength: 5 }),
  fallback: fc.array(
    fc.record({
      provider: fc.constantFrom("anthropic", "openai"),
      model: fc.stringMatching(/^[a-z0-9-]+$/).filter((s) => s.length > 0 && s.length < 40),
      when: fc.option(
        fc.array(
          fc.constantFrom("budget-exceeded", "latency-breached", "provider-error", "no-match"),
        ),
        { nil: undefined },
      ),
    }),
    { maxLength: 5 },
  ),
});

const arbContext = fc.record({
  taskType: fc.option(fc.string(), { nil: undefined }),
  tenant: fc.option(fc.string(), { nil: undefined }),
  budgetRemainingUsd: fc.option(fc.double({ min: 0, max: 10, noNaN: true }), { nil: undefined }),
  latencyBudgetMs: fc.option(fc.integer({ min: 0, max: 10000 }), { nil: undefined }),
  sessionIdHash: fc.option(fc.string(), { nil: undefined }),
  confidenceScore: fc.option(fc.double({ min: 0, max: 1, noNaN: true }), { nil: undefined }),
  previousFailure: fc.option(
    fc.constantFrom("provider-error", "latency-breached", "budget-exceeded"),
    { nil: undefined },
  ),
});

// fast-check's `.option(..., { nil: undefined })` yields T | undefined which
// is structurally equivalent to optional fields; TS is happy via `as`.
function toInputs(config: unknown, context: unknown): ChooseModelInputs {
  return {
    config: config as ModelRoutingPayload,
    context: context as ModelRouterContext,
  };
}

describe("P-det — chooseModel is deterministic", () => {
  test("same inputs + same nowIso → identical ModelDecision (100 runs)", () => {
    fc.assert(
      fc.property(arbConfig, arbContext, (config, context) => {
        const inputs = toInputs(config, context);
        const a = chooseModel(inputs, NOW);
        const b = chooseModel(inputs, NOW);
        return JSON.stringify(a) === JSON.stringify(b);
      }),
      { numRuns: 100 },
    );
  });

  test("evaluatedAt in the output is exactly the injected nowIso (100 runs)", () => {
    fc.assert(
      fc.property(
        arbConfig,
        arbContext,
        fc.date({ min: new Date("2000-01-01"), max: new Date("2100-01-01"), noInvalidDate: true }),
        (config, context, d) => {
          const inputs = toInputs(config, context);
          const nowIso = d.toISOString();
          return chooseModel(inputs, nowIso).evaluatedAt === nowIso;
        },
      ),
      { numRuns: 100 },
    );
  });
});
