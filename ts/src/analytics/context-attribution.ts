/** Ablation-backed attribution for immutable context-bundle components (AC-974). */

import {
  stableDigest,
  type BundleComponent,
  type ContextBundle,
} from "../context-bundles/index.js";

export type EvidenceLevel = "causal_ablation" | "paired_shadow" | "component_correlated";
export type ComponentDisposition = "retained" | "uncertain" | "demotion_candidate" | "harmful";

export interface ControlledAttributionTrial {
  trial_id: string;
  component_kind: string;
  component_key: string;
  component_digest: string;
  tested_bundle_digest: string;
  comparison_bundle_digest: string;
  evaluator_epoch: string;
  trial_cohort: string;
  fixture_digest: string;
  seed: number;
  evidence_level: EvidenceLevel;
  with_component_score: number;
  without_component_score: number;
  token_cost: number;
  tested_at: string;
  interaction_component_digests: string[];
}

export interface ComponentAttribution {
  attribution_id: string;
  component_kind: string;
  component_key: string;
  component_digest: string;
  evidence_level: EvidenceLevel;
  effect: number;
  confidence: number;
  evaluator_epoch: string;
  trial_cohort: string;
  token_cost: number;
  last_tested_bundle_digest: string;
  tested_at: string;
  disposition: ComponentDisposition;
  trial_ids: string[];
  interaction_component_digests: string[];
  supersedes_attribution_id: string | null;
}

export interface ContextAttributionLedger {
  schema_version: 1;
  scenario: string;
  trials: ControlledAttributionTrial[];
  attributions: ComponentAttribution[];
}

export interface ReablationCandidate {
  component_kind: string;
  component_key: string;
  component_digest: string;
  token_cost: number;
  estimated_trial_cost: number;
  confidence: number;
  last_tested_generation: number;
  last_tested_bundle_digest: string;
  interaction_risk: number;
}

export interface ReablationPolicy {
  cadence_generations: number;
  plateau_generations: number;
  budget: number;
}

export interface ReablationPlan {
  trigger: "not_due" | "plateau" | "cadence" | "bundle_changed";
  selected: ReablationCandidate[];
  deferred: ReablationCandidate[];
  spent: number;
  budget: number;
}

export interface PromptComponentSelection {
  component_kind: string;
  component_key: string;
  component_digest: string;
  included: boolean;
  disposition: ComponentDisposition;
  evidence_level: EvidenceLevel | null;
  confidence: number;
  evaluator_epoch: string | null;
  trial_cohort: string | null;
  last_tested_bundle_digest: string | null;
  token_cost: number;
  reason: string;
}

export const DEFAULT_REABLATION_POLICY: Readonly<ReablationPolicy> = Object.freeze({
  cadence_generations: 5,
  plateau_generations: 3,
  budget: 10,
});

export function controlledTrialEffect(trial: ControlledAttributionTrial): number {
  return round(trial.with_component_score - trial.without_component_score);
}

export function attributeControlledTrials(
  trials: readonly ControlledAttributionTrial[],
  opts: { evaluatorEpoch: string; neutralEffect?: number; highTokenCost?: number },
): ComponentAttribution[] {
  const groups = new Map<string, ControlledAttributionTrial[]>();
  const trialIds = new Set<string>();
  for (const trial of trials) {
    validateTrial(trial);
    if (trialIds.has(trial.trial_id)) throw new Error(`duplicate attribution trial: ${trial.trial_id}`);
    trialIds.add(trial.trial_id);
    if (trial.evaluator_epoch !== opts.evaluatorEpoch) {
      throw new Error("attribution trial evaluator epoch mismatch");
    }
    if (trial.evidence_level === "component_correlated") {
      throw new Error("component_correlated evidence cannot be constructed from a controlled trial");
    }
    const key = [
      trial.component_kind,
      trial.component_key,
      trial.component_digest,
      trial.tested_bundle_digest,
      trial.trial_cohort,
      trial.evidence_level,
    ].join("\0");
    groups.set(key, [...(groups.get(key) ?? []), trial]);
  }

  return [...groups.values()]
    .sort((left, right) => groupSortKey(left).localeCompare(groupSortKey(right)))
    .map((group) => attributionFromGroup(
      group,
      opts.evaluatorEpoch,
      opts.neutralEffect ?? 0,
      opts.highTokenCost ?? 256,
    ));
}

export function reconstructCausalCredit(
  attribution: ComponentAttribution,
  trials: readonly ControlledAttributionTrial[],
): number {
  if (attribution.evidence_level !== "causal_ablation") {
    throw new Error("only causal_ablation records carry causal credit");
  }
  const byId = new Map(trials.map((trial) => [trial.trial_id, trial]));
  const referenced = attribution.trial_ids.map((trialId) => {
    const trial = byId.get(trialId);
    if (!trial) throw new Error(`missing referenced trial: ${trialId}`);
    return trial;
  });
  if (referenced.length === 0) throw new Error("causal attribution has no referenced trials");
  for (const trial of referenced) {
    if (
      trial.evidence_level !== "causal_ablation"
      || trial.component_digest !== attribution.component_digest
      || trial.tested_bundle_digest !== attribution.last_tested_bundle_digest
      || trial.evaluator_epoch !== attribution.evaluator_epoch
      || trial.trial_cohort !== attribution.trial_cohort
    ) {
      throw new Error("referenced trial does not match the causal attribution");
    }
  }
  return round(mean(referenced.map(controlledTrialEffect)));
}

export function appendReablation(
  ledger: ContextAttributionLedger,
  trials: readonly ControlledAttributionTrial[],
  attributions: readonly ComponentAttribution[],
): ContextAttributionLedger {
  const latest = new Map<string, ComponentAttribution>();
  for (const record of ledger.attributions) latest.set(componentIdentity(record), record);
  const linked = attributions.map((record) => ({
    ...record,
    supersedes_attribution_id: latest.get(componentIdentity(record))?.attribution_id ?? null,
  }));
  return {
    ...ledger,
    trials: [...ledger.trials, ...trials],
    attributions: [...ledger.attributions, ...linked],
  };
}

export function planReablation(
  candidates: readonly ReablationCandidate[],
  input: {
    currentGeneration: number;
    lastReablationGeneration: number;
    plateauLength: number;
    currentBundleDigest: string;
    policy?: ReablationPolicy;
  },
): ReablationPlan {
  const policy = input.policy ?? DEFAULT_REABLATION_POLICY;
  const cadenceDue = input.currentGeneration - input.lastReablationGeneration >= policy.cadence_generations;
  const plateauDue = input.plateauLength >= policy.plateau_generations;
  const bundleChanged = candidates.some(
    (candidate) => candidate.last_tested_bundle_digest !== input.currentBundleDigest,
  );
  if (!cadenceDue && !plateauDue && !bundleChanged) {
    return { trigger: "not_due", selected: [], deferred: [...candidates], spent: 0, budget: policy.budget };
  }
  const trigger = plateauDue ? "plateau" : cadenceDue ? "cadence" : "bundle_changed";
  const ordered = [...candidates].sort((left, right) => {
    const difference = reablationPriority(right, input.currentGeneration, input.currentBundleDigest)
      - reablationPriority(left, input.currentGeneration, input.currentBundleDigest);
    return difference || componentIdentity(left).localeCompare(componentIdentity(right));
  });
  const selected: ReablationCandidate[] = [];
  const deferred: ReablationCandidate[] = [];
  let spent = 0;
  for (const candidate of ordered) {
    if (spent + candidate.estimated_trial_cost <= policy.budget) {
      selected.push(candidate);
      spent += candidate.estimated_trial_cost;
    } else {
      deferred.push(candidate);
    }
  }
  return { trigger, selected, deferred, spent, budget: policy.budget };
}

export function selectPromptComponents(
  bundle: ContextBundle,
  attributions: readonly ComponentAttribution[],
  tokenCosts: Readonly<Record<string, number>> = {},
): PromptComponentSelection[] {
  const latest = new Map<string, ComponentAttribution>();
  for (const record of attributions) {
    if (record.evaluator_epoch === bundle.evaluator_epoch) latest.set(record.component_digest, record);
  }
  return bundle.components.map((component) => selectComponent(
    component,
    latest.get(component.digest),
    bundle.digest,
    Math.max(0, tokenCosts[component.digest] ?? component.content.split(/\s+/u).filter(Boolean).length),
  ));
}

export function renderContextAttributionReport(attributions: readonly ComponentAttribution[]): string {
  const lines = ["# Context attribution"];
  for (const record of attributions) {
    const qualifier: Record<EvidenceLevel, string> = {
      causal_ablation: "controlled with/without-component effect",
      paired_shadow: "matched shadow effect; causal isolation not established",
      component_correlated: "edit-size correlation only; not causal",
    };
    lines.push(
      `- ${record.component_kind}/${record.component_key} \`${record.component_digest}\`: `
      + `${signed(record.effect)}, ${record.disposition}, evidence=${record.evidence_level} `
      + `(${qualifier[record.evidence_level]}), confidence=${record.confidence.toFixed(2)}, `
      + `evaluator=${record.evaluator_epoch}, cohort=${record.trial_cohort}, tokens=${record.token_cost}, `
      + `last_bundle=${record.last_tested_bundle_digest}`,
    );
  }
  return lines.join("\n");
}

function attributionFromGroup(
  group: ControlledAttributionTrial[],
  evaluatorEpoch: string,
  neutralEffect: number,
  highTokenCost: number,
): ComponentAttribution {
  const first = group[0];
  if (!first) throw new Error("controlled attribution group cannot be empty");
  const effects = group.map(controlledTrialEffect);
  const effect = round(mean(effects));
  const tokenCost = Math.max(...group.map((trial) => trial.token_cost));
  const ids = group.map((trial) => trial.trial_id).sort();
  const interactions = [...new Set(group.flatMap((trial) => trial.interaction_component_digests))].sort();
  const attributionId = stableDigest({
    component_digest: first.component_digest,
    tested_bundle_digest: first.tested_bundle_digest,
    evaluator_epoch: evaluatorEpoch,
    trial_ids: ids,
    evidence_level: first.evidence_level,
  });
  return {
    attribution_id: attributionId,
    component_kind: first.component_kind,
    component_key: first.component_key,
    component_digest: first.component_digest,
    evidence_level: first.evidence_level,
    effect,
    confidence: trialConfidence(effects),
    evaluator_epoch: evaluatorEpoch,
    trial_cohort: first.trial_cohort,
    token_cost: tokenCost,
    last_tested_bundle_digest: first.tested_bundle_digest,
    tested_at: group.map((trial) => trial.tested_at).sort().at(-1) ?? first.tested_at,
    disposition: classify(effect, tokenCost, neutralEffect, highTokenCost),
    trial_ids: ids,
    interaction_component_digests: interactions,
    supersedes_attribution_id: null,
  };
}

function validateTrial(trial: ControlledAttributionTrial): void {
  const required = [
    trial.trial_id,
    trial.component_kind,
    trial.component_key,
    trial.component_digest,
    trial.tested_bundle_digest,
    trial.comparison_bundle_digest,
    trial.evaluator_epoch,
    trial.trial_cohort,
    trial.fixture_digest,
    trial.tested_at,
  ];
  if (required.some((value) => !value.trim())) throw new Error("controlled attribution trial identity is required");
  if (!Number.isInteger(trial.seed)) throw new Error("controlled attribution trial seed must be an integer");
  if (trial.token_cost < 0) throw new Error("controlled attribution trial token cost cannot be negative");
}

function trialConfidence(effects: readonly number[]): number {
  if (effects.length === 0) return 0;
  const nonzero = effects.filter((effect) => effect !== 0);
  const agreement = nonzero.length === 0
    ? 1
    : Math.max(nonzero.filter((effect) => effect > 0).length, nonzero.filter((effect) => effect < 0).length)
      / nonzero.length;
  return round(Math.min(1, effects.length / 4) * agreement);
}

function classify(
  effect: number,
  tokenCost: number,
  neutralEffect: number,
  highTokenCost: number,
): ComponentDisposition {
  if (effect < -neutralEffect) return "harmful";
  if (effect > neutralEffect) return "retained";
  if (tokenCost >= highTokenCost) return "demotion_candidate";
  return "uncertain";
}

function reablationPriority(
  candidate: ReablationCandidate,
  currentGeneration: number,
  currentBundleDigest: string,
): number {
  const age = Math.max(0, currentGeneration - candidate.last_tested_generation);
  const bundleChange = candidate.last_tested_bundle_digest === currentBundleDigest ? 0 : 1;
  return candidate.token_cost / 16
    + (1 - candidate.confidence) * 10
    + age
    + candidate.interaction_risk * 10
    + bundleChange * 15;
}

function selectComponent(
  component: BundleComponent,
  record: ComponentAttribution | undefined,
  currentBundleDigest: string,
  tokenCost: number,
): PromptComponentSelection {
  const identity = {
    component_kind: component.kind,
    component_key: component.key,
    component_digest: component.digest,
    token_cost: tokenCost,
  };
  if (!record) {
    return {
      ...identity,
      included: true,
      disposition: "uncertain",
      evidence_level: null,
      confidence: 0,
      evaluator_epoch: null,
      trial_cohort: null,
      last_tested_bundle_digest: null,
      reason: "no attribution evidence; retain pending a controlled test",
    };
  }
  if (record.last_tested_bundle_digest !== currentBundleDigest) {
    return {
      ...identity,
      included: true,
      disposition: "uncertain",
      evidence_level: record.evidence_level,
      confidence: record.confidence,
      evaluator_epoch: record.evaluator_epoch,
      trial_cohort: record.trial_cohort,
      last_tested_bundle_digest: record.last_tested_bundle_digest,
      reason: "bundle composition changed; retain until interaction re-ablation",
    };
  }
  const demoted = record.disposition === "demotion_candidate" || record.disposition === "harmful";
  return {
    ...identity,
    included: !demoted,
    disposition: record.disposition,
    evidence_level: record.evidence_level,
    confidence: record.confidence,
    evaluator_epoch: record.evaluator_epoch,
    trial_cohort: record.trial_cohort,
    last_tested_bundle_digest: record.last_tested_bundle_digest,
    reason: demoted
      ? "demoted from prompt assembly; attribution history retained"
      : "retained by current-bundle attribution",
  };
}

function componentIdentity(value: {
  component_kind: string;
  component_key: string;
  component_digest: string;
}): string {
  return `${value.component_kind}\0${value.component_key}\0${value.component_digest}`;
}

function groupSortKey(group: readonly ControlledAttributionTrial[]): string {
  const first = group[0];
  return first ? componentIdentity(first) : "";
}

function mean(values: readonly number[]): number {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function round(value: number): number {
  return Math.round(value * 1_000_000) / 1_000_000;
}

function signed(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(4)}`;
}
