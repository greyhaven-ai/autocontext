import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  createBundleComponent,
  createContextBundle,
  createJsonBundleComponent,
  canonicalJson,
  campaignAdjustedConfirmationPolicy,
  campaignAlphaForCandidate,
  createCampaignFalsePromotionState,
  createCandidateFixtureReservation,
  createCandidateRiskReservation,
  evaluateMatchedTrials,
  evaluateCampaignFalsePromotionEvidence,
  matchedTrialPairKey,
  requiredConfidenceZ,
  stableDigest,
  type ComponentKind,
  type CandidateRiskReservation,
  type CampaignFalsePromotionPolicy,
  type ConfirmationPolicy,
  type FalsePromotionStatus,
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

const falsePromotionFixture = JSON.parse(
  readFileSync(join(import.meta.dirname, "../../fixtures/context-bundles/false-promotion-parity.json"), "utf8"),
) as {
  campaign_policy: CampaignFalsePromotionPolicy;
  base_confirmation_policy: ConfirmationPolicy;
  candidate_allocations: Array<{
    candidate_index: number;
    alpha: number;
    required_confidence_z: number;
  }>;
  persisted_state_case: {
    campaign_id: string;
    candidate_fixture: string;
    expected_confirmation_policy_digest: string;
    expected_state_digest: string;
  };
  reservation_status_cases: Array<{
    status: FalsePromotionStatus;
    reason: string | null;
    evidence_digest: string | null;
    independent_confirmation_blocks: number;
    independent_heldout_blocks: number;
    expected_state_digest: string;
  }>;
  evidence_cases: Array<{
    name: string;
    observations: Array<{ lane: MatchedTrial["lane"]; block: string; seed: number; delta: number }>;
    expected: ReturnType<typeof evaluateCampaignFalsePromotionEvidence>;
  }>;
};

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

  it("matches campaign-wide adaptive and non-IID false-promotion fixtures", () => {
    const campaignPolicy = falsePromotionFixture.campaign_policy;
    const basePolicy = falsePromotionFixture.base_confirmation_policy;
    const evidenceCandidate = validateContextBundle(fixture.candidate);
    for (const allocation of falsePromotionFixture.candidate_allocations) {
      expect(campaignAlphaForCandidate(allocation.candidate_index, campaignPolicy)).toBeCloseTo(
        allocation.alpha,
        15,
      );
      const adjusted = campaignAdjustedConfirmationPolicy(
        basePolicy,
        allocation.candidate_index,
        campaignPolicy,
      );
      expect(adjusted.confidence_z).toBeCloseTo(allocation.required_confidence_z, 10);
    }
    const extremeThreshold = requiredConfidenceZ(Number.MIN_VALUE);
    expect(Number.isFinite(extremeThreshold)).toBe(true);
    expect(extremeThreshold).toBeGreaterThan(38);

    for (const fixtureCase of falsePromotionFixture.evidence_cases) {
      const trials: MatchedTrial[] = fixtureCase.observations.map((observation) => ({
        candidate_digest: evidenceCandidate.digest,
        incumbent_digest: evidenceCandidate.parent_digest,
        evaluator_epoch: evidenceCandidate.evaluator_epoch,
        cohort: "parity-cohort",
        fixture: `fixture-${observation.seed}`,
        fixture_digest: observation.block,
        seed: observation.seed,
        lane: observation.lane,
        candidate_score: 0.5 + observation.delta,
        incumbent_score: 0.5,
        candidate_valid: true,
        incumbent_valid: true,
      }));
      const created = createCandidateRiskReservation(
        `evidence-${fixtureCase.name}`,
        evidenceCandidate,
        0,
        basePolicy,
        campaignPolicy,
      );
      const fixtureReservation = createCandidateFixtureReservation(
        created.reservation.campaign_id,
        evidenceCandidate,
        "parity-cohort",
        trials.map((trial) => ({
          lane: trial.lane,
          fixture_digest: trial.fixture_digest,
          seed: trial.seed,
        })),
      );
      expect(
        evaluateCampaignFalsePromotionEvidence(
          evidenceCandidate,
          trials,
          created.confirmationPolicy,
          created.reservation,
          fixtureReservation,
          campaignPolicy,
        ),
        fixtureCase.name,
      ).toEqual(fixtureCase.expected);
    }

    const stateCase = falsePromotionFixture.persisted_state_case;
    const candidateFixture = JSON.parse(
      readFileSync(join(import.meta.dirname, "../../fixtures/context-bundles", stateCase.candidate_fixture), "utf8"),
    ) as { tested: unknown };
    const candidate = validateContextBundle(candidateFixture.tested);
    const created = createCandidateRiskReservation(
      stateCase.campaign_id,
      candidate,
      0,
      basePolicy,
      campaignPolicy,
    );
    expect(created.reservation.confirmation_policy_digest).toBe(
      stateCase.expected_confirmation_policy_digest,
    );
    expect(
      createCampaignFalsePromotionState(
        stateCase.campaign_id,
        campaignPolicy,
        [created.reservation],
      ).state_digest,
    ).toBe(stateCase.expected_state_digest);
    for (const statusCase of falsePromotionFixture.reservation_status_cases) {
      const reservation: CandidateRiskReservation = {
        ...created.reservation,
        status: statusCase.status,
        reason: statusCase.reason,
        evidence_digest: statusCase.evidence_digest,
        independent_confirmation_blocks: statusCase.independent_confirmation_blocks,
        independent_heldout_blocks: statusCase.independent_heldout_blocks,
      };
      expect(
        createCampaignFalsePromotionState(
          stateCase.campaign_id,
          campaignPolicy,
          [reservation],
        ).state_digest,
        statusCase.status,
      ).toBe(statusCase.expected_state_digest);
    }
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

  it("binds false-promotion evidence to validated manifests, flags, policy, and reservation lineage", () => {
    const candidate = validateContextBundle(fixture.candidate);
    const campaignPolicy = falsePromotionFixture.campaign_policy;
    const created = createCandidateRiskReservation(
      "evidence-binding",
      candidate,
      0,
      falsePromotionFixture.base_confirmation_policy,
      campaignPolicy,
    );
    const makeBoundTrials = (
      lane: MatchedTrial["lane"],
      effects: readonly number[],
      seedOffset: number,
    ): MatchedTrial[] => effects.map((effect, index) => ({
      candidate_digest: candidate.digest,
      incumbent_digest: candidate.parent_digest,
      evaluator_epoch: candidate.evaluator_epoch,
      cohort: "binding-cohort",
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
      ...makeBoundTrials("screen", [0.3], 100),
      ...makeBoundTrials("confirmation", [0.3, 0.3], 110),
      ...makeBoundTrials("heldout", [0.3], 120),
    ];
    const fixtureReservation = createCandidateFixtureReservation(
      created.reservation.campaign_id,
      candidate,
      "binding-cohort",
      trials.map((trial) => ({
        lane: trial.lane,
        fixture_digest: trial.fixture_digest,
        seed: trial.seed,
      })),
    );

    const forgedCandidate = { ...candidate, digest: "0".repeat(64) };
    expect(() => evaluateCampaignFalsePromotionEvidence(
      forgedCandidate,
      trials,
      created.confirmationPolicy,
      created.reservation,
      fixtureReservation,
      campaignPolicy,
    )).toThrow("context bundle digest mismatch");

    const invalidFlags = [{ ...trials[0]!, candidate_valid: 1 }, ...trials.slice(1)];
    expect(() => evaluateCampaignFalsePromotionEvidence(
      candidate,
      invalidFlags,
      created.confirmationPolicy,
      created.reservation,
      fixtureReservation,
      campaignPolicy,
    )).toThrow("matched trial has an invalid shape");

    expect(() => evaluateCampaignFalsePromotionEvidence(
      candidate,
      trials,
      { ...created.confirmationPolicy, min_effect: 0.01 },
      created.reservation,
      fixtureReservation,
      campaignPolicy,
    )).toThrow("policy different from its risk reservation");

    expect(() => evaluateCampaignFalsePromotionEvidence(
      candidate,
      trials,
      created.confirmationPolicy,
      { ...created.reservation, incumbent_digest: "f".repeat(64) },
      fixtureReservation,
      campaignPolicy,
    )).toThrow("candidate lineage");

    expect(() => evaluateCampaignFalsePromotionEvidence(
      candidate,
      trials,
      created.confirmationPolicy,
      { ...created.reservation, status: "blocked" },
      fixtureReservation,
      campaignPolicy,
    )).toThrow("only a pristine reserved");

    const arbitraryFixture = [
      ...trials.slice(0, 1),
      { ...trials[1]!, fixture_digest: "adaptively-selected" },
      ...trials.slice(2),
    ];
    expect(() => evaluateCampaignFalsePromotionEvidence(
      candidate,
      arbitraryFixture,
      created.confirmationPolicy,
      created.reservation,
      fixtureReservation,
      campaignPolicy,
    )).toThrow("outside its reserved plan");

    const relabeledLane = [
      ...trials.slice(0, 1),
      { ...trials[1]!, lane: "heldout" },
      ...trials.slice(2),
    ];
    expect(() => evaluateCampaignFalsePromotionEvidence(
      candidate,
      relabeledLane,
      created.confirmationPolicy,
      created.reservation,
      fixtureReservation,
      campaignPolicy,
    )).toThrow("relabels a reserved fixture lane");

    expect(() => evaluateCampaignFalsePromotionEvidence(
      candidate,
      trials,
      created.confirmationPolicy,
      created.reservation,
      { ...fixtureReservation, plan_digest: "0".repeat(64) },
      campaignPolicy,
    )).toThrow("fixture plan digest mismatch");
  });
});
