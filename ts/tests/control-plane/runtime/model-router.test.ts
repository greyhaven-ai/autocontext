// Unit tests for chooseModel — the pure runtime helper that consults a
// model-routing config and returns a ModelDecision. No I/O, clock injected
// as nowIso. See spec §4 (AC-545).

import { describe, test, expect } from "vitest";
import { chooseModel } from "../../../src/control-plane/runtime/model-router.js";
import type {
  ChooseModelInputs,
  ModelRouterContext,
} from "../../../src/control-plane/runtime/model-router.js";
import type { ModelRoutingPayload } from "../../../src/control-plane/actuators/model-routing/schema.js";

const NOW = "2026-04-17T12:00:00.000Z";

const DEFAULT_TARGET = {
  provider: "anthropic",
  model: "claude-sonnet-4-5",
  endpoint: null,
};

function cfg(overrides: Partial<ModelRoutingPayload> = {}): ModelRoutingPayload {
  return {
    schemaVersion: "1.0",
    default: DEFAULT_TARGET,
    routes: [],
    fallback: [],
    ...overrides,
  };
}

function choose(
  config: ModelRoutingPayload,
  context: ModelRouterContext,
  nowIso: string = NOW,
) {
  const inputs: ChooseModelInputs = { config, context };
  return chooseModel(inputs, nowIso);
}

describe("chooseModel — default path", () => {
  test("no routes → default model with reason=default and evaluatedAt=nowIso", () => {
    const decision = choose(cfg(), {});
    expect(decision.chosen).toEqual({
      provider: "anthropic",
      model: "claude-sonnet-4-5",
      endpoint: undefined,
    });
    expect(decision.reason).toBe("default");
    expect(decision.matchedRouteId).toBeUndefined();
    expect(decision.fallbackReason).toBeUndefined();
    expect(decision.evaluatedAt).toBe(NOW);
  });

  test("evaluatedAt is the injected nowIso verbatim", () => {
    const d = choose(cfg(), {}, "2099-12-31T23:59:59.000Z");
    expect(d.evaluatedAt).toBe("2099-12-31T23:59:59.000Z");
  });

  test("default target preserves endpoint when set (string) and omits when null", () => {
    const withEndpoint = choose(
      cfg({
        default: { provider: "openai-compatible", model: "m", endpoint: "https://ep/v1" },
      }),
      {},
    );
    expect(withEndpoint.chosen).toEqual({
      provider: "openai-compatible",
      model: "m",
      endpoint: "https://ep/v1",
    });
  });
});

describe("chooseModel — first matching route wins", () => {
  test("route with matching taskType wins over default", () => {
    const decision = choose(
      cfg({
        routes: [
          {
            id: "r1",
            match: { "env.taskType": { equals: "checkout" } },
            target: { provider: "o", model: "m-checkout" },
          },
        ],
      }),
      { taskType: "checkout" },
    );
    expect(decision.reason).toBe("matched-route");
    expect(decision.matchedRouteId).toBe("r1");
    expect(decision.chosen.model).toBe("m-checkout");
  });

  test("first route wins when multiple match", () => {
    const decision = choose(
      cfg({
        routes: [
          {
            id: "first",
            match: { "env.taskType": { equals: "checkout" } },
            target: { provider: "o", model: "first-model" },
          },
          {
            id: "second",
            match: { "env.taskType": { equals: "checkout" } },
            target: { provider: "o", model: "second-model" },
          },
        ],
      }),
      { taskType: "checkout" },
    );
    expect(decision.matchedRouteId).toBe("first");
  });

  test("default: true matches any context", () => {
    const decision = choose(
      cfg({
        routes: [
          {
            id: "catchall",
            match: { "env.taskType": { default: true } },
            target: { provider: "x", model: "y" },
          },
        ],
      }),
      {},
    );
    expect(decision.reason).toBe("matched-route");
    expect(decision.matchedRouteId).toBe("catchall");
  });

  test("no-match reports reason=default (not fallback)", () => {
    const decision = choose(
      cfg({
        routes: [
          {
            id: "r1",
            match: { "env.taskType": { equals: "other-task" } },
            target: { provider: "o", model: "m" },
          },
        ],
      }),
      { taskType: "checkout" },
    );
    expect(decision.reason).toBe("default");
  });

  test("empty match expression is non-matching if unchecked config reaches runtime", () => {
    const decision = choose(
      cfg({
        routes: [
          {
            id: "bad-catchall",
            match: {},
            target: { provider: "o", model: "bad" },
          },
        ],
      }),
      { taskType: "checkout" },
    );
    expect(decision.reason).toBe("default");
  });

  test("multi-operator matcher is non-matching if unchecked config reaches runtime", () => {
    const decision = choose(
      cfg({
        routes: [
          {
            id: "bad-multi-op",
            match: {
              "env.taskType": { default: true, equals: "checkout" },
            },
            target: { provider: "o", model: "bad" },
          },
        ],
      }),
      { taskType: "checkout" },
    );
    expect(decision.reason).toBe("default");
  });
});

describe("chooseModel — contains operator", () => {
  test("contains with a string needle matches substring", () => {
    const decision = choose(
      cfg({
        routes: [
          {
            id: "ck",
            match: { "env.taskType": { contains: "check" } },
            target: { provider: "o", model: "m" },
          },
        ],
      }),
      { taskType: "checkout-v2" },
    );
    expect(decision.matchedRouteId).toBe("ck");
  });

  test("contains with an array needle matches any element", () => {
    const decision = choose(
      cfg({
        routes: [
          {
            id: "multi",
            match: { "env.taskType": { contains: ["login", "checkout"] } },
            target: { provider: "o", model: "m" },
          },
        ],
      }),
      { taskType: "checkout" },
    );
    expect(decision.matchedRouteId).toBe("multi");
  });
});

describe("chooseModel — guardrail demotions", () => {
  test("budget exceeded → demotes matched route to fallback", () => {
    const decision = choose(
      cfg({
        routes: [
          {
            id: "r1",
            match: { "env.taskType": { equals: "checkout" } },
            target: { provider: "o", model: "m" },
            budget: { maxCostUsdPerCall: 0.01 },
          },
        ],
        fallback: [{ provider: "anthropic", model: "claude-haiku-4-5" }],
      }),
      { taskType: "checkout", budgetRemainingUsd: 0.005 },
    );
    expect(decision.reason).toBe("fallback");
    expect(decision.fallbackReason).toBe("budget-exceeded");
    expect(decision.chosen.model).toBe("claude-haiku-4-5");
  });

  test("latency budget smaller than route max → demotes to fallback with latency-breached", () => {
    const decision = choose(
      cfg({
        routes: [
          {
            id: "r1",
            match: { "env.taskType": { equals: "checkout" } },
            target: { provider: "o", model: "m" },
            latency: { maxP95Ms: 800 },
          },
        ],
        fallback: [{ provider: "anthropic", model: "claude-haiku-4-5" }],
      }),
      { taskType: "checkout", latencyBudgetMs: 500 },
    );
    expect(decision.reason).toBe("fallback");
    expect(decision.fallbackReason).toBe("latency-breached");
  });

  test("confidence below minScore → route does not match (skip to next / default)", () => {
    const decision = choose(
      cfg({
        routes: [
          {
            id: "low-conf",
            match: { "env.taskType": { equals: "checkout" } },
            target: { provider: "o", model: "m" },
            confidence: { minScore: 0.85 },
          },
        ],
      }),
      { taskType: "checkout", confidenceScore: 0.5 },
    );
    expect(decision.reason).toBe("default");
  });

  test("confidence above minScore → route matches normally", () => {
    const decision = choose(
      cfg({
        routes: [
          {
            id: "high-conf",
            match: { "env.taskType": { equals: "checkout" } },
            target: { provider: "o", model: "m" },
            confidence: { minScore: 0.85 },
          },
        ],
      }),
      { taskType: "checkout", confidenceScore: 0.9 },
    );
    expect(decision.reason).toBe("matched-route");
  });

  test("no confidence context at all + confidence guardrail → skips route (conservative)", () => {
    const decision = choose(
      cfg({
        routes: [
          {
            id: "r1",
            match: { "env.taskType": { equals: "checkout" } },
            target: { provider: "o", model: "m" },
            confidence: { minScore: 0.85 },
          },
        ],
      }),
      { taskType: "checkout" },
    );
    expect(decision.reason).toBe("default");
  });
});

describe("chooseModel — previousFailure short-circuit", () => {
  test("previousFailure=provider-error while route matches → fallback", () => {
    const decision = choose(
      cfg({
        routes: [
          {
            id: "r1",
            match: { "env.taskType": { equals: "checkout" } },
            target: { provider: "o", model: "m" },
          },
        ],
        fallback: [{ provider: "anthropic", model: "claude-haiku-4-5" }],
      }),
      { taskType: "checkout", previousFailure: "provider-error" },
    );
    expect(decision.reason).toBe("fallback");
    expect(decision.fallbackReason).toBe("provider-error");
    expect(decision.chosen.model).toBe("claude-haiku-4-5");
  });

  test("previousFailure without a matched route → still returns default (nothing to fall back from)", () => {
    const decision = choose(cfg({ fallback: [{ provider: "x", model: "y" }] }), {
      previousFailure: "provider-error",
    });
    // No route matched, so there's nothing for previousFailure to demote from.
    expect(decision.reason).toBe("default");
  });
});

describe("chooseModel — rollout cohort hashing", () => {
  test("percent:0 → route never matches, percent:100 → always matches", () => {
    const d0 = choose(
      cfg({
        routes: [
          {
            id: "r1",
            match: { "env.taskType": { equals: "checkout" } },
            target: { provider: "o", model: "m" },
            rollout: { percent: 0, cohortKey: "session.sessionIdHash" },
          },
        ],
      }),
      { taskType: "checkout", sessionIdHash: "abc-xyz" },
    );
    expect(d0.reason).toBe("default");

    const d100 = choose(
      cfg({
        routes: [
          {
            id: "r1",
            match: { "env.taskType": { equals: "checkout" } },
            target: { provider: "o", model: "m" },
            rollout: { percent: 100, cohortKey: "session.sessionIdHash" },
          },
        ],
      }),
      { taskType: "checkout", sessionIdHash: "abc-xyz" },
    );
    expect(d100.reason).toBe("matched-route");
  });

  test("missing cohort value → route does not match (conservative per spec §4)", () => {
    const decision = choose(
      cfg({
        routes: [
          {
            id: "r1",
            match: { "env.taskType": { equals: "checkout" } },
            target: { provider: "o", model: "m" },
            rollout: { percent: 100, cohortKey: "session.sessionIdHash" },
          },
        ],
      }),
      { taskType: "checkout" },
    );
    expect(decision.reason).toBe("default");
  });

  test("same cohort value lands in the same bucket across invocations", () => {
    const config = cfg({
      routes: [
        {
          id: "r1",
          match: { "env.taskType": { equals: "checkout" } },
          target: { provider: "o", model: "m" },
          rollout: { percent: 50, cohortKey: "session.sessionIdHash" },
        },
      ],
    });
    const first = choose(config, { taskType: "checkout", sessionIdHash: "stable-hash" });
    const second = choose(config, { taskType: "checkout", sessionIdHash: "stable-hash" });
    expect(first.reason).toBe(second.reason);
    expect(first.matchedRouteId).toBe(second.matchedRouteId);
  });
});

describe("chooseModel — fallback chain order", () => {
  test("first fallback with no `when` filter is used", () => {
    const decision = choose(
      cfg({
        routes: [
          {
            id: "r1",
            match: { "env.taskType": { equals: "checkout" } },
            target: { provider: "o", model: "m" },
            budget: { maxCostUsdPerCall: 0.01 },
          },
        ],
        fallback: [
          { provider: "a", model: "first" },
          { provider: "a", model: "second" },
        ],
      }),
      { taskType: "checkout", budgetRemainingUsd: 0 },
    );
    expect(decision.chosen.model).toBe("first");
  });

  test("fallback with `when` filter is skipped if reason not listed", () => {
    const decision = choose(
      cfg({
        routes: [
          {
            id: "r1",
            match: { "env.taskType": { equals: "checkout" } },
            target: { provider: "o", model: "m" },
            budget: { maxCostUsdPerCall: 0.01 },
          },
        ],
        fallback: [
          { provider: "a", model: "only-latency", when: ["latency-breached"] },
          { provider: "a", model: "for-budget", when: ["budget-exceeded"] },
        ],
      }),
      { taskType: "checkout", budgetRemainingUsd: 0 },
    );
    expect(decision.chosen.model).toBe("for-budget");
    expect(decision.fallbackReason).toBe("budget-exceeded");
  });

  test("fallback list exhausted with no match → reason still fallback, chosen=default", () => {
    const decision = choose(
      cfg({
        routes: [
          {
            id: "r1",
            match: { "env.taskType": { equals: "checkout" } },
            target: { provider: "o", model: "m" },
            budget: { maxCostUsdPerCall: 0.01 },
          },
        ],
        fallback: [{ provider: "a", model: "not-this", when: ["latency-breached"] }],
      }),
      { taskType: "checkout", budgetRemainingUsd: 0 },
    );
    // No fallback with matching `when`; router falls all the way back to
    // default, but the reason+fallbackReason still record the budget demotion.
    expect(decision.reason).toBe("fallback");
    expect(decision.fallbackReason).toBe("budget-exceeded");
    expect(decision.chosen).toEqual({
      provider: "anthropic",
      model: "claude-sonnet-4-5",
      endpoint: undefined,
    });
  });
});

describe("chooseModel — context lookup (dotted paths)", () => {
  test("supports env.* and session.* paths", () => {
    const decision = choose(
      cfg({
        routes: [
          {
            id: "r1",
            match: {
              "env.taskType": { equals: "checkout" },
              "session.sessionIdHash": { equals: "abc" },
            },
            target: { provider: "o", model: "m-combined" },
          },
        ],
      }),
      { taskType: "checkout", sessionIdHash: "abc" },
    );
    expect(decision.matchedRouteId).toBe("r1");
  });

  test("unknown dotted path → operator non-match → route skipped", () => {
    const decision = choose(
      cfg({
        routes: [
          {
            id: "r1",
            match: { "env.not-a-real-field": { equals: "x" } },
            target: { provider: "o", model: "m" },
          },
        ],
      }),
      { taskType: "checkout" },
    );
    expect(decision.reason).toBe("default");
  });
});
