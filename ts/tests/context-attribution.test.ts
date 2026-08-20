import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  appendReablation,
  attributeControlledTrials,
  attributeManifestVerifiedTrials,
  parseContextAttributionLedger,
  planReablation,
  reconstructCausalCredit,
  reconstructManifestVerifiedCausalCredit,
  renderContextAttributionReport,
  selectPromptComponents,
  type ComponentAttribution,
  type ContextAttributionLedger,
  type ControlledAttributionTrial,
  type ReablationCandidate,
  type ReablationPolicy,
} from "../src/analytics/context-attribution.js";
import {
  attributeCredit,
  ComponentChange,
  formatAttributionForAgent,
  GenerationChangeVector,
} from "../src/analytics/credit-assignment.js";
import {
  contextBundleManifestDiff,
  createBundleComponent,
  createContextBundle,
  validateContextBundle,
} from "../src/context-bundles/index.js";

const fixture = JSON.parse(
  readFileSync(join(import.meta.dirname, "..", "..", "docs", "context-attribution-parity-fixture.json"), "utf-8"),
) as {
  evaluator_epoch: string;
  initial_trials: ControlledAttributionTrial[];
  initial_expected: Record<string, unknown>;
  unicode_trial_order: {
    input_trial_ids: string[];
    expected_trial_ids: string[];
    expected_attribution_id: string;
  };
  reablation_trials: ControlledAttributionTrial[];
  reablation_expected: Record<string, unknown>;
  budget_case: {
    current_generation: number;
    last_reablation_generation: number;
    plateau_length: number;
    current_bundle_digest: string;
    policy: ReablationPolicy;
    candidates: ReablationCandidate[];
    expected_selected: string[];
    expected_deferred: string[];
  };
};

const roundingFixture = JSON.parse(
  readFileSync(join(import.meta.dirname, "..", "..", "fixtures", "numeric-rounding-parity.json"), "utf-8"),
) as {
  cases: Array<{ name: string; value: number; expected: number }>;
};

const bundleManifestFixture = JSON.parse(
  readFileSync(join(import.meta.dirname, "..", "..", "fixtures", "context-bundles", "manifest-parity.json"), "utf-8"),
) as { baseline: unknown; candidate: unknown };

const causalManifestFixture = JSON.parse(
  readFileSync(
    join(
      import.meta.dirname,
      "..",
      "..",
      "fixtures",
      "context-bundles",
      "causal-attribution-manifest-parity.json",
    ),
    "utf-8",
  ),
) as { tested: unknown; comparison: unknown; manifest_diff: { digest: string } };

describe("context attribution", () => {
  it("binds causal attribution to the exact shared manifest diff", () => {
    const tested = validateContextBundle(causalManifestFixture.tested);
    const comparison = validateContextBundle(causalManifestFixture.comparison);
    const component = tested.components.find((item) => item.kind === "playbook")!;
    const trial: ControlledAttributionTrial = {
      trial_id: "manifest-bound-1",
      component_kind: component.kind,
      component_key: component.key,
      component_digest: component.digest,
      tested_bundle_digest: tested.digest,
      comparison_bundle_digest: comparison.digest,
      evaluator_epoch: tested.evaluator_epoch,
      trial_cohort: "manifest-cohort",
      fixture_digest: "fixture-1",
      seed: 1,
      evidence_level: "causal_ablation",
      with_component_score: 0.8,
      without_component_score: 0.6,
      token_cost: 100,
      tested_at: "2026-08-20T00:00:00Z",
      interaction_component_digests: [],
    };
    const manifests = new Map([[tested.digest, tested], [comparison.digest, comparison]]);

    const record = attributeManifestVerifiedTrials([trial], {
      evaluatorEpoch: tested.evaluator_epoch,
      bundleManifests: manifests,
    })[0]!;

    expect(contextBundleManifestDiff(tested, comparison).digest).toBe(causalManifestFixture.manifest_diff.digest);
    expect(record.manifest_diff_digest).toBe(causalManifestFixture.manifest_diff.digest);
    expect(reconstructManifestVerifiedCausalCredit(record, [trial], manifests)).toBe(0.2);

    const multiChange = createContextBundle({
      scenario: tested.scenario,
      evaluatorEpoch: tested.evaluator_epoch,
      components: [...tested.components, createBundleComponent("hints", "hints", "also changed")],
    });
    expect(() => attributeManifestVerifiedTrials(
      [{ ...trial, tested_bundle_digest: multiChange.digest }],
      {
        evaluatorEpoch: tested.evaluator_epoch,
        bundleManifests: new Map([[multiChange.digest, multiChange], [comparison.digest, comparison]]),
      },
    )).toThrow("exact single-component manifest diff");

    const replacement = validateContextBundle(bundleManifestFixture.baseline);
    expect(() => attributeManifestVerifiedTrials(
      [{ ...trial, comparison_bundle_digest: replacement.digest }],
      {
        evaluatorEpoch: tested.evaluator_epoch,
        bundleManifests: new Map([[tested.digest, tested], [replacement.digest, replacement]]),
      },
    )).toThrow("target does not match");

    const forgedTested = { ...tested, digest: "0".repeat(64) };
    expect(() => attributeManifestVerifiedTrials(
      [{ ...trial, tested_bundle_digest: forgedTested.digest }],
      {
        evaluatorEpoch: tested.evaluator_epoch,
        bundleManifests: new Map([[forgedTested.digest, forgedTested], [comparison.digest, comparison]]),
      },
    )).toThrow("context bundle digest mismatch");
  });

  it("reconstructs single-component causal credit from shared stored trials", () => {
    const record = attributeControlledTrials(fixture.initial_trials, {
      evaluatorEpoch: fixture.evaluator_epoch,
    })[0]!;

    expect(record).toMatchObject(fixture.initial_expected);
    expect(reconstructCausalCredit(record, fixture.initial_trials)).toBe(0.2);
  });

  it("uses the shared UTF-16 order when trial IDs bind attribution identity", () => {
    const trials = fixture.initial_trials.map((trial, index) => ({
      ...trial,
      trial_id: fixture.unicode_trial_order.input_trial_ids[index]!,
    }));

    const record = attributeControlledTrials(trials, {
      evaluatorEpoch: fixture.evaluator_epoch,
    })[0]!;

    expect(record.trial_ids).toEqual(fixture.unicode_trial_order.expected_trial_ids);
    expect(record.attribution_id).toBe(fixture.unicode_trial_order.expected_attribution_id);
  });

  it("matches shared half-away-from-zero effect rounding", () => {
    const base = fixture.initial_trials[0]!;
    for (const [index, fixtureCase] of roundingFixture.cases.entries()) {
      const trial: ControlledAttributionTrial = {
        ...base,
        trial_id: `rounding-${index}`,
        fixture_digest: `sha256:rounding-${index}`,
        seed: index,
        with_component_score: fixtureCase.value,
        without_component_score: 0,
      };
      const record = attributeControlledTrials([trial], { evaluatorEpoch: trial.evaluator_epoch })[0]!;
      expect(record.effect, fixtureCase.name).toBe(fixtureCase.expected);
    }
  });

  it("preserves history and finds an interacting component harmful after bundle change", () => {
    const initial = attributeControlledTrials(fixture.initial_trials, {
      evaluatorEpoch: fixture.evaluator_epoch,
    });
    const ledger: ContextAttributionLedger = {
      schema_version: 2,
      scenario: "grid_ctf",
      trials: fixture.initial_trials,
      attributions: initial,
    };
    const reablation = attributeControlledTrials(fixture.reablation_trials, {
      evaluatorEpoch: fixture.evaluator_epoch,
    });

    const updated = appendReablation(ledger, fixture.reablation_trials, reablation);

    expect(updated.attributions).toHaveLength(2);
    expect(updated.attributions[1]).toMatchObject(fixture.reablation_expected);
    expect(updated.attributions[1]!.supersedes_attribution_id).toBe(initial[0]!.attribution_id);
    expect(updated.attributions[0]!.disposition).toBe("retained");
  });

  it("respects the re-ablation budget and rejects evaluator mismatch", () => {
    const item = fixture.budget_case;
    const plan = planReablation(item.candidates, {
      currentGeneration: item.current_generation,
      lastReablationGeneration: item.last_reablation_generation,
      plateauLength: item.plateau_length,
      currentBundleDigest: item.current_bundle_digest,
      policy: item.policy,
    });
    expect(plan.selected.map((candidate) => candidate.component_digest)).toEqual(item.expected_selected);
    expect(plan.deferred.map((candidate) => candidate.component_digest)).toEqual(item.expected_deferred);
    expect(plan.spent).toBeLessThanOrEqual(plan.budget);

    const mismatched = { ...fixture.initial_trials[0]!, evaluator_epoch: "eval-8" };
    expect(() => attributeControlledTrials([mismatched], { evaluatorEpoch: "eval-7" })).toThrow(
      /evaluator epoch mismatch/,
    );
  });

  it("rejects self-comparisons, duplicate pairs, and mixed matching context", () => {
    const first = fixture.initial_trials[0]!;
    const second = fixture.initial_trials[1]!;

    expect(() => attributeControlledTrials([{
      ...first,
      comparison_bundle_digest: first.tested_bundle_digest,
    }], { evaluatorEpoch: fixture.evaluator_epoch })).toThrow(/distinct tested and comparison bundles/);

    expect(() => attributeControlledTrials([
      first,
      { ...first, trial_id: "duplicate-pair-with-a-new-id" },
    ], { evaluatorEpoch: fixture.evaluator_epoch })).toThrow(/duplicate matched attribution pair/);

    expect(() => attributeControlledTrials([
      first,
      { ...second, comparison_bundle_digest: "sha256:other-comparison" },
    ], { evaluatorEpoch: fixture.evaluator_epoch })).toThrow(/mixes comparison bundles/);

    expect(() => attributeControlledTrials([
      first,
      { ...second, trial_cohort: "cohort-b" },
    ], { evaluatorEpoch: fixture.evaluator_epoch })).toThrow(/mixes trial cohorts/);
  });

  it("rejects duplicate, tampered, and self-asserted causal evidence during replay", () => {
    const record = attributeControlledTrials(fixture.initial_trials, {
      evaluatorEpoch: fixture.evaluator_epoch,
    })[0]!;

    expect(record.comparison_bundle_digest).toBe("sha256:bundle-a-minus-playbook");
    expect(record.classification_neutral_effect).toBe(0);
    expect(record.classification_high_token_cost).toBe(256);
    expect(record.matched_pair_keys).toHaveLength(fixture.initial_trials.length);
    expect(record.source_trial_digests).toHaveLength(fixture.initial_trials.length);

    expect(() => reconstructCausalCredit(record, [
      ...fixture.initial_trials,
      fixture.initial_trials[0]!,
    ])).toThrow(/duplicate attribution trial/);

    expect(() => reconstructCausalCredit(record, [
      { ...fixture.initial_trials[0]!, fixture_digest: "sha256:different-fixture" },
      fixture.initial_trials[1]!,
    ])).toThrow(/binding mismatch/);

    expect(() => reconstructCausalCredit({ ...record, effect: 1 }, fixture.initial_trials)).toThrow(
      /effect does not match/,
    );
    expect(() => reconstructCausalCredit({
      ...record,
      comparison_bundle_digest: "sha256:invented",
    }, fixture.initial_trials)).toThrow(/does not match the controlled attribution/);
    const selfAssertedDisposition = { ...record, disposition: "harmful" as const };
    expect(() => reconstructCausalCredit(selfAssertedDisposition, fixture.initial_trials)).toThrow(
      /disposition does not match/,
    );
    const { classification_neutral_effect: _missingPolicy, ...missingPolicy } = record;
    expect(() => reconstructCausalCredit(
      missingPolicy as ComponentAttribution,
      fixture.initial_trials,
    )).toThrow(/classification neutral effect/);

    const ledger: ContextAttributionLedger = {
      schema_version: 2,
      scenario: "grid_ctf",
      trials: [],
      attributions: [],
    };
    expect(() => appendReablation(ledger, fixture.initial_trials, [{
      ...record,
      source_trial_digests: [],
    }])).toThrow(/source-trial binding mismatch/);
    expect(() => appendReablation(ledger, fixture.initial_trials, [selfAssertedDisposition])).toThrow(
      /disposition does not match/,
    );
  });

  it("migrates schema-v1 history without trusting invented controlled provenance", () => {
    const current = attributeControlledTrials(fixture.initial_trials, {
      evaluatorEpoch: fixture.evaluator_epoch,
    })[0]!;
    const legacyRecord: Record<string, unknown> = { ...current };
    for (const field of [
      "comparison_bundle_digest",
      "classification_neutral_effect",
      "classification_high_token_cost",
      "matched_pair_keys",
      "source_trial_digests",
      "legacy_unverified",
    ]) delete legacyRecord[field];
    const migrated = parseContextAttributionLedger({
      schema_version: 1,
      scenario: "grid_ctf",
      trials: fixture.initial_trials,
      attributions: [legacyRecord],
    });

    expect(migrated.schema_version).toBe(2);
    expect(migrated.attributions[0]!.legacy_unverified).toBe(true);
    expect(() => reconstructCausalCredit(migrated.attributions[0]!, fixture.initial_trials)).toThrow(
      /legacy attribution lacks verified controlled-trial provenance/,
    );

    const newRecords = attributeControlledTrials(fixture.reablation_trials, {
      evaluatorEpoch: fixture.evaluator_epoch,
    });
    const updated = appendReablation(migrated, fixture.reablation_trials, newRecords);
    expect(updated.attributions.at(-1)).toMatchObject({
      legacy_unverified: false,
      supersedes_attribution_id: migrated.attributions[0]!.attribution_id,
    });
  });

  it.each([Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY])(
    "rejects non-finite attribution score and threshold %s",
    (score) => {
      const trial = fixture.initial_trials[0]!;
      expect(() => attributeControlledTrials([{
        ...trial,
        with_component_score: score,
      }], { evaluatorEpoch: fixture.evaluator_epoch })).toThrow(/scores must be finite/);

      expect(() => attributeControlledTrials([{
        ...trial,
        with_component_score: 1e308,
        without_component_score: -1e308,
      }], { evaluatorEpoch: fixture.evaluator_epoch })).toThrow(/effect must be finite/);

      expect(() => attributeControlledTrials([trial], {
        evaluatorEpoch: fixture.evaluator_epoch,
        neutralEffect: score,
      })).toThrow(/neutralEffect must be finite/);
    },
  );

  it("demotes neutral high-cost context but re-tests it after composition changes", () => {
    const playbook = createBundleComponent("playbook", "main", "costly guidance ".repeat(30));
    const originalBundle = createContextBundle({
      scenario: "grid_ctf",
      evaluatorEpoch: "eval-7",
      components: [playbook],
    });
    const trial: ControlledAttributionTrial = {
      trial_id: "neutral-playbook",
      component_kind: playbook.kind,
      component_key: playbook.key,
      component_digest: playbook.digest,
      tested_bundle_digest: originalBundle.digest,
      comparison_bundle_digest: "sha256:without-playbook",
      evaluator_epoch: "eval-7",
      trial_cohort: "cohort-a",
      fixture_digest: "sha256:fixture",
      seed: 1,
      evidence_level: "causal_ablation",
      with_component_score: 0.7,
      without_component_score: 0.7,
      token_cost: 500,
      tested_at: "2026-08-17T12:00:00Z",
      interaction_component_digests: [],
    };
    const record = attributeControlledTrials([trial], { evaluatorEpoch: "eval-7" })[0]!;

    expect(selectPromptComponents(originalBundle, [record])[0]).toMatchObject({
      disposition: "demotion_candidate",
      included: false,
    });

    const hints = createBundleComponent("hints", "coach", "new interaction");
    const changedBundle = createContextBundle({
      scenario: "grid_ctf",
      evaluatorEpoch: "eval-7",
      components: [playbook, hints],
    });
    const changed = selectPromptComponents(changedBundle, [record]).find(
      (selection) => selection.component_digest === playbook.digest,
    );
    expect(changed).toMatchObject({ included: true, disposition: "uncertain" });
    expect(changed?.reason).toContain("interaction re-ablation");

    const positiveTrial: ControlledAttributionTrial = {
      ...trial,
      trial_id: "positive-playbook",
      fixture_digest: "sha256:positive-fixture",
      with_component_score: 0.8,
      without_component_score: 0.7,
    };
    const retained = attributeControlledTrials([positiveTrial], { evaluatorEpoch: "eval-7" })[0]!;
    expect(retained.disposition).toBe("retained");
    expect(selectPromptComponents(originalBundle, [{ ...retained, disposition: "harmful" }])[0]).toMatchObject({
      included: true,
      disposition: "uncertain",
      reason: expect.stringContaining("failed classification-policy verification"),
    });
  });

  it("labels edit-size and promotion reports as noncausal", () => {
    const correlated = attributeCredit(new GenerationChangeVector(
      1,
      0.2,
      [new ComponentChange("playbook", 1, "changed")],
    ));
    expect(correlated.metadata).toEqual({ evidence_level: "component_correlated", causal: false });
    expect(formatAttributionForAgent(correlated, "coach")).toContain("not causal");

    const report = renderContextAttributionReport([{
      attribution_id: "correlated-1",
      component_kind: "playbook",
      component_key: "main",
      component_digest: "sha256:playbook",
      evidence_level: "component_correlated",
      effect: 0.2,
      confidence: 0.2,
      evaluator_epoch: "eval-7",
      trial_cohort: "generation-1",
      token_cost: 100,
      last_tested_bundle_digest: "sha256:bundle",
      comparison_bundle_digest: null,
      tested_at: "2026-08-17T12:00:00Z",
      disposition: "uncertain",
      classification_neutral_effect: 0,
      classification_high_token_cost: 256,
      trial_ids: [],
      matched_pair_keys: [],
      source_trial_digests: [],
      interaction_component_digests: [],
      supersedes_attribution_id: null,
      legacy_unverified: false,
    }]);
    expect(report).toContain("component_correlated");
    expect(report).toContain("not causal");
  });
});
