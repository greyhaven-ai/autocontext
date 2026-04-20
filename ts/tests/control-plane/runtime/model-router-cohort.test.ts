// Property test P-cohort (spec §4, AC-545 stated property test).
//
// For a fixed cohortKey value across many invocations with rollout percent N,
// the "matches vs. doesn't match" answer is stable — same cohort always lands
// in the same bucket. 100 runs.

import { describe, test } from "vitest";
import fc from "fast-check";
import { chooseModel } from "../../../src/control-plane/runtime/model-router.js";
import type { ModelRoutingPayload } from "../../../src/control-plane/actuators/model-routing/schema.js";

const NOW = "2026-04-17T12:00:00.000Z";

function configWithRollout(percent: number): ModelRoutingPayload {
  return {
    schemaVersion: "1.0",
    default: { provider: "anthropic", model: "default-model", endpoint: null },
    routes: [
      {
        id: "cohort-route",
        match: { "env.taskType": { equals: "checkout" } },
        target: { provider: "openai-compatible", model: "cohort-model" },
        rollout: { percent, cohortKey: "session.sessionIdHash" },
      },
    ],
    fallback: [],
  };
}

describe("P-cohort — rollout bucketing is stable per cohort value", () => {
  test("same cohort value → same decision across invocations (100 runs)", () => {
    fc.assert(
      fc.property(
        fc.string({ minLength: 1 }),
        fc.integer({ min: 0, max: 100 }),
        (sessionIdHash, percent) => {
          const config = configWithRollout(percent);
          const ctx = { taskType: "checkout", sessionIdHash };
          const first = chooseModel({ config, context: ctx }, NOW);
          const second = chooseModel({ config, context: ctx }, NOW);
          const third = chooseModel({ config, context: ctx }, NOW);
          const fourth = chooseModel({ config, context: ctx }, NOW);
          return (
            first.reason === second.reason
            && second.reason === third.reason
            && third.reason === fourth.reason
            && first.matchedRouteId === fourth.matchedRouteId
          );
        },
      ),
      { numRuns: 100 },
    );
  });

  test("percent:0 → never matches regardless of cohort value (100 runs)", () => {
    const config = configWithRollout(0);
    fc.assert(
      fc.property(fc.string({ minLength: 1 }), (sessionIdHash) => {
        const decision = chooseModel(
          { config, context: { taskType: "checkout", sessionIdHash } },
          NOW,
        );
        return decision.reason === "default";
      }),
      { numRuns: 100 },
    );
  });

  test("percent:100 → always matches when cohort value is provided (100 runs)", () => {
    const config = configWithRollout(100);
    fc.assert(
      fc.property(fc.string({ minLength: 1 }), (sessionIdHash) => {
        const decision = chooseModel(
          { config, context: { taskType: "checkout", sessionIdHash } },
          NOW,
        );
        return decision.reason === "matched-route";
      }),
      { numRuns: 100 },
    );
  });

  test("missing cohort value → never matches regardless of percent (100 runs)", () => {
    fc.assert(
      fc.property(fc.integer({ min: 0, max: 100 }), (percent) => {
        const config = configWithRollout(percent);
        const decision = chooseModel({ config, context: { taskType: "checkout" } }, NOW);
        return decision.reason === "default";
      }),
      { numRuns: 100 },
    );
  });
});
