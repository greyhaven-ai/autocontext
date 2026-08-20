import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  createBundleComponent,
  createContextBundle,
  createJsonBundleComponent,
  canonicalJson,
  evaluateMatchedTrials,
  matchedTrialPairKey,
  stableDigest,
  type ComponentKind,
  type MatchedTrial,
  validateContextBundle,
} from "../src/context-bundles/index.js";

const fixture = JSON.parse(
  readFileSync(join(import.meta.dirname, "../../fixtures/context-bundles/manifest-parity.json"), "utf8"),
);

interface CanonicalParityFixture {
  canonical_cases: Array<{
    name: string;
    value: unknown;
    canonical: string;
    digest: string;
  }>;
  unsafe_integer_cases: Array<{
    name: string;
    json: string;
  }>;
  invalid_unicode_cases: Array<{
    name: string;
    json: string;
  }>;
  bundle_input: {
    scenario: string;
    evaluator_epoch: string;
    components: Array<{ kind: ComponentKind; key: string; value: unknown }>;
  };
  bundle_manifest: unknown;
  matched_pair_identity: {
    evaluator_epoch: string;
    cohort: string;
    fixture: string;
    fixture_digest: string;
    seed: number;
    pair_key: string;
  };
}

const canonicalFixture = JSON.parse(
  readFileSync(join(import.meta.dirname, "../../fixtures/context-bundles/canonical-parity.json"), "utf8"),
) as CanonicalParityFixture;

describe("context bundle parity", () => {
  it("reproduces Python component and manifest digests", () => {
    const baseline = createContextBundle({
      scenario: "demo",
      evaluatorEpoch: "epoch-1",
      components: [
        createJsonBundleComponent("routing_config", "roles", { model_competitor: "small" }),
        createBundleComponent("playbook", "playbook", "baseline", "text/markdown"),
      ],
    });
    const candidate = createContextBundle({
      scenario: "demo",
      evaluatorEpoch: "epoch-1",
      parentDigest: baseline.digest,
      components: [
        createJsonBundleComponent("routing_config", "roles", { model_competitor: "small" }),
        createBundleComponent("playbook", "playbook", "candidate", "text/markdown"),
      ],
    });

    expect(baseline).toEqual(fixture.baseline);
    expect(candidate).toEqual(fixture.candidate);
    expect(validateContextBundle(fixture.candidate)).toEqual(candidate);
  });

  it("matches shared Unicode ordering and ECMAScript-number fixtures", () => {
    for (const fixtureCase of canonicalFixture.canonical_cases) {
      expect(canonicalJson(fixtureCase.value), fixtureCase.name).toBe(fixtureCase.canonical);
      expect(stableDigest(fixtureCase.value), fixtureCase.name).toBe(fixtureCase.digest);
    }
    for (const fixtureCase of canonicalFixture.unsafe_integer_cases) {
      const value: unknown = JSON.parse(fixtureCase.json);
      expect(() => canonicalJson(value), fixtureCase.name).toThrow(/safe integer/);
      expect(() => stableDigest(value), fixtureCase.name).toThrow(/safe integer/);
    }
    for (const fixtureCase of canonicalFixture.invalid_unicode_cases) {
      const value: unknown = JSON.parse(fixtureCase.json);
      expect(() => canonicalJson(value), fixtureCase.name).toThrow(/lone UTF-16 surrogate/);
      expect(() => stableDigest(value), fixtureCase.name).toThrow(/lone UTF-16 surrogate/);
    }

    const input = canonicalFixture.bundle_input;
    const bundle = createContextBundle({
      scenario: input.scenario,
      evaluatorEpoch: input.evaluator_epoch,
      components: input.components.map((component) =>
        createJsonBundleComponent(component.kind, component.key, component.value),
      ),
    });

    expect(bundle.components.map((component) => component.key)).toEqual(["😀", "דּ"]);
    expect(bundle).toEqual(canonicalFixture.bundle_manifest);
    expect(validateContextBundle(canonicalFixture.bundle_manifest)).toEqual(bundle);
  });

  it("rejects forged component digests and unknown component kinds", () => {
    const forgedDigest = structuredClone(fixture.candidate);
    forgedDigest.components[0].digest = "0".repeat(64);
    expect(() => validateContextBundle(forgedDigest)).toThrow(/component digest mismatch/);

    const unknownKind = structuredClone(fixture.candidate);
    unknownKind.components[0].kind = "unknown_kind";
    expect(() => validateContextBundle(unknownKind)).toThrow(/invalid shape/);
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

  it("matches Python Student-t bounds and Bonferroni control across adaptive looks", () => {
    const candidate = validateContextBundle(fixture.candidate);
    const makeTrials = (lane: MatchedTrial["lane"], effects: readonly number[], seedOffset: number) =>
      effects.map((effect, index): MatchedTrial => ({
        candidate_digest: candidate.digest,
        incumbent_digest: candidate.parent_digest,
        evaluator_epoch: candidate.evaluator_epoch,
        cohort: "cohort-a",
        fixture: `${lane}-${seedOffset + index}`,
        fixture_digest: `${lane}-digest-${seedOffset + index}`,
        seed: seedOffset + index,
        lane,
        candidate_score: 0.5 + effect,
        incumbent_score: 0.5,
        candidate_valid: true,
        incumbent_valid: true,
      }));
    const trials = [
      ...makeTrials("screen", [0.02, 0.02], 0),
      ...makeTrials("confirmation", [0.014, 0.014, 0.034, 0.034], 10),
    ];
    const fixedLook = evaluateMatchedTrials(candidate, trials, {
      min_screen_pairs: 2,
      min_confirmation_pairs: 4,
      max_confirmation_pairs: 4,
      min_heldout_pairs: 2,
      min_effect: 0.01,
      confidence_z: 1.96,
    });

    // Python paired_confidence_interval(..., max_looks=1). The previous normal
    // interval had a lower bound above 0.01 and would have advanced this case.
    expect(fixedLook.decision).toBe("inconclusive");
    expect(fixedLook.mean_effect).toBeCloseTo(0.02400000000000002, 14);
    expect(fixedLook.confidence_low).toBeCloseTo(0.005625504524827234, 13);
    expect(fixedLook.confidence_high).toBeCloseTo(0.04237449547517281, 13);

    const adaptiveLooks = evaluateMatchedTrials(candidate, trials, {
      min_screen_pairs: 2,
      min_confirmation_pairs: 4,
      max_confirmation_pairs: 12,
      min_heldout_pairs: 2,
      min_effect: 0.01,
      confidence_z: 1.96,
    });

    // Nine possible looks (4..12) spend alpha by Bonferroni, matching Python.
    expect(adaptiveLooks.decision).toBe("needs_confirmation");
    expect(adaptiveLooks.confidence_low).toBeCloseTo(-0.017483077826851476, 13);
    expect(adaptiveLooks.confidence_high).toBeCloseTo(0.06548307782685152, 13);

    const highConfidence = evaluateMatchedTrials(candidate, trials, {
      min_screen_pairs: 2,
      min_confirmation_pairs: 4,
      max_confirmation_pairs: 18,
      min_heldout_pairs: 2,
      min_effect: 0.01,
      confidence_z: 8,
    });

    // Fifteen possible looks make the upper-tail probability smaller than the
    // spacing below 1. Invert the survival function directly so it stays
    // representable instead of rounding `1 - tail` to 1 and rejecting it.
    expect(highConfidence.decision).toBe("needs_confirmation");
    expect(highConfidence.confidence_low).not.toBeNull();
    expect(highConfidence.confidence_high).not.toBeNull();
    expect(Number.isFinite(highConfidence.confidence_low)).toBe(true);
    expect(Number.isFinite(highConfidence.confidence_high)).toBe(true);

    const underflowedTail = evaluateMatchedTrials(candidate, trials, {
      min_screen_pairs: 2,
      min_confirmation_pairs: 4,
      max_confirmation_pairs: 18,
      min_heldout_pairs: 2,
      min_effect: 0.01,
      confidence_z: 40,
    });

    expect(underflowedTail.confidence_low).toBe(-Number.MAX_VALUE);
    expect(underflowedTail.confidence_high).toBe(Number.MAX_VALUE);

    const decisiveOverBudget = [
      ...makeTrials("screen", [0.2, 0.2], 30),
      ...makeTrials("confirmation", [0.2, 0.2, 0.2, 0.2, 0.2], 40),
      ...makeTrials("heldout", [0.2, 0.2], 50),
    ];
    expect(() => evaluateMatchedTrials(candidate, decisiveOverBudget, {
      min_screen_pairs: 2,
      min_confirmation_pairs: 4,
      max_confirmation_pairs: 4,
      min_heldout_pairs: 2,
      min_effect: 0,
      confidence_z: 1.96,
    })).toThrow(/confirmation pairs exceed/);
  });

  it("rejects lane relabeling, invalid policy bounds, and non-finite scores", () => {
    const candidate = validateContextBundle(fixture.candidate);
    const identity = canonicalFixture.matched_pair_identity;
    const screen: MatchedTrial = {
      candidate_digest: candidate.digest,
      incumbent_digest: candidate.parent_digest,
      evaluator_epoch: identity.evaluator_epoch,
      cohort: identity.cohort,
      fixture: identity.fixture,
      fixture_digest: identity.fixture_digest,
      seed: identity.seed,
      lane: "screen",
      candidate_score: 0.7,
      incumbent_score: 0.5,
      candidate_valid: true,
      incumbent_valid: true,
    };
    const relabeled: MatchedTrial = {
      ...screen,
      fixture: "display-confirmation",
      lane: "confirmation",
    };

    expect(matchedTrialPairKey(screen)).toBe(identity.pair_key);
    expect(matchedTrialPairKey(relabeled)).toBe(identity.pair_key);
    expect(relabeled.lane).toBe("confirmation");
    expect(() => evaluateMatchedTrials(candidate, [screen, relabeled])).toThrow(/duplicate/);

    const validPolicy = {
      min_screen_pairs: 2,
      min_confirmation_pairs: 4,
      max_confirmation_pairs: 12,
      min_heldout_pairs: 2,
      min_effect: 0.01,
      confidence_z: 1.96,
    };
    const invalidPolicies = [
      { ...validPolicy, min_screen_pairs: 0 },
      { ...validPolicy, min_confirmation_pairs: 1 },
      { ...validPolicy, max_confirmation_pairs: 3 },
      { ...validPolicy, min_heldout_pairs: 0 },
      { ...validPolicy, min_effect: Number.NaN },
      { ...validPolicy, confidence_z: Number.POSITIVE_INFINITY },
      { ...validPolicy, min_screen_pairs: 1.5 },
    ];
    for (const policy of invalidPolicies) {
      expect(() => evaluateMatchedTrials(candidate, [], policy)).toThrow();
    }

    const invalidTrials = [
      { ...screen, candidate_score: Number.NaN },
      { ...screen, incumbent_score: Number.POSITIVE_INFINITY },
      { ...screen, candidate_score: Number.MAX_VALUE, incumbent_score: -Number.MAX_VALUE },
    ];
    for (const trial of invalidTrials) {
      expect(() => evaluateMatchedTrials(candidate, [trial])).toThrow(/finite/);
    }
  });
});
