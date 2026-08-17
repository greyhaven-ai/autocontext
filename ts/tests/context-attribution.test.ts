import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  appendReablation,
  attributeControlledTrials,
  planReablation,
  reconstructCausalCredit,
  renderContextAttributionReport,
  selectPromptComponents,
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
import { createBundleComponent, createContextBundle } from "../src/context-bundles/index.js";

const fixture = JSON.parse(
  readFileSync(join(import.meta.dirname, "..", "..", "docs", "context-attribution-parity-fixture.json"), "utf-8"),
) as {
  evaluator_epoch: string;
  initial_trials: ControlledAttributionTrial[];
  initial_expected: Record<string, unknown>;
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

describe("context attribution", () => {
  it("reconstructs single-component causal credit from shared stored trials", () => {
    const record = attributeControlledTrials(fixture.initial_trials, {
      evaluatorEpoch: fixture.evaluator_epoch,
    })[0]!;

    expect(record).toMatchObject(fixture.initial_expected);
    expect(reconstructCausalCredit(record, fixture.initial_trials)).toBe(0.2);
  });

  it("preserves history and finds an interacting component harmful after bundle change", () => {
    const initial = attributeControlledTrials(fixture.initial_trials, {
      evaluatorEpoch: fixture.evaluator_epoch,
    });
    const ledger: ContextAttributionLedger = {
      schema_version: 1,
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
      tested_at: "2026-08-17T12:00:00Z",
      disposition: "uncertain",
      trial_ids: [],
      interaction_component_digests: [],
      supersedes_attribution_id: null,
    }]);
    expect(report).toContain("component_correlated");
    expect(report).toContain("not causal");
  });
});
