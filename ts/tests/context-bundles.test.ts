import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  createBundleComponent,
  createContextBundle,
  createJsonBundleComponent,
  evaluateMatchedTrials,
  type MatchedTrial,
  validateContextBundle,
} from "../src/context-bundles/index.js";

const fixture = JSON.parse(
  readFileSync(join(import.meta.dirname, "../../fixtures/context-bundles/manifest-parity.json"), "utf8"),
);

describe("context bundle parity", () => {
  it("reproduces Python component and manifest digests", () => {
    const baseline = createContextBundle({
      scenario: "demo",
      evaluatorEpoch: "epoch-1",
      components: [
        createJsonBundleComponent("routing_config", "roles", { competitor: "small" }),
        createBundleComponent("playbook", "playbook", "baseline", "text/markdown"),
      ],
    });
    const candidate = createContextBundle({
      scenario: "demo",
      evaluatorEpoch: "epoch-1",
      parentDigest: baseline.digest,
      components: [
        createJsonBundleComponent("routing_config", "roles", { competitor: "small" }),
        createBundleComponent("playbook", "playbook", "candidate", "text/markdown"),
      ],
    });

    expect(baseline).toEqual(fixture.baseline);
    expect(candidate).toEqual(fixture.candidate);
    expect(validateContextBundle(fixture.candidate)).toEqual(candidate);
  });

  it("confirms only matched candidate/incumbent trial pairs", () => {
    const candidate = validateContextBundle(fixture.candidate);
    const trials: MatchedTrial[] = [];
    for (const [lane, count] of [["screen", 2], ["confirmation", 6], ["heldout", 2]] as const) {
      for (let index = 0; index < count; index += 1) {
        trials.push({
          candidate_digest: candidate.digest,
          incumbent_digest: candidate.parent_digest,
          evaluator_epoch: candidate.evaluator_epoch,
          cohort: "cohort-a",
          fixture: `${lane}-${index}`,
          fixture_digest: `${lane}-digest-${index}`,
          seed: index,
          lane,
          candidate_score: 0.7,
          incumbent_score: 0.5,
          candidate_valid: true,
          incumbent_valid: true,
        });
      }
    }

    expect(evaluateMatchedTrials(candidate, trials).decision).toBe("confirmed");
    expect(() => evaluateMatchedTrials(candidate, [...trials, trials[0]!])).toThrow(/duplicate/);
  });
});
