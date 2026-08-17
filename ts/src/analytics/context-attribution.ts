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
  comparison_bundle_digest: string | null;
  tested_at: string;
  disposition: ComponentDisposition;
  classification_neutral_effect: number;
  classification_high_token_cost: number;
  trial_ids: string[];
  matched_pair_keys: string[];
  source_trial_digests: string[];
  interaction_component_digests: string[];
  supersedes_attribution_id: string | null;
  legacy_unverified: boolean;
}

export interface ContextAttributionLedger {
  schema_version: 2;
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

function compareUtf16(left: string, right: string): number {
  if (left === right) return 0;
  return left < right ? -1 : 1;
}

function isUnknownRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function readRequiredString(record: Record<string, unknown>, field: string): string {
  const value = record[field];
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`component attribution ${field} must be a non-empty string`);
  }
  return value;
}

function readNumber(record: Record<string, unknown>, field: string): number {
  const value = record[field];
  if (typeof value !== "number") throw new Error(`component attribution ${field} must be a number`);
  return value;
}

function readBoolean(record: Record<string, unknown>, field: string): boolean {
  const value = record[field];
  if (typeof value !== "boolean") throw new Error(`component attribution ${field} must be a boolean`);
  return value;
}

function readNullableString(record: Record<string, unknown>, field: string): string | null {
  const value = record[field];
  if (value === null || typeof value === "string") return value;
  throw new Error(`component attribution ${field} must be a string or null`);
}

function readStringArray(record: Record<string, unknown>, field: string): string[] {
  const value = record[field];
  if (!Array.isArray(value)) throw new Error(`component attribution ${field} must be an array`);
  const strings: string[] = [];
  for (const item of value) {
    if (typeof item !== "string") throw new Error(`component attribution ${field} must contain only strings`);
    strings.push(item);
  }
  return strings;
}

function readEvidenceLevel(record: Record<string, unknown>): EvidenceLevel {
  const value = record.evidence_level;
  if (value !== "causal_ablation" && value !== "paired_shadow" && value !== "component_correlated") {
    throw new Error("component attribution evidence_level is invalid");
  }
  return value;
}

function readDisposition(record: Record<string, unknown>): ComponentDisposition {
  const value = record.disposition;
  if (value !== "retained" && value !== "uncertain" && value !== "demotion_candidate" && value !== "harmful") {
    throw new Error("component attribution disposition is invalid");
  }
  return value;
}

function parseControlledAttributionTrial(value: unknown): ControlledAttributionTrial {
  if (!isUnknownRecord(value)) throw new Error("controlled attribution trial must be an object");
  const evidenceLevel = readEvidenceLevel(value);
  const trial: ControlledAttributionTrial = {
    trial_id: readRequiredString(value, "trial_id"),
    component_kind: readRequiredString(value, "component_kind"),
    component_key: readRequiredString(value, "component_key"),
    component_digest: readRequiredString(value, "component_digest"),
    tested_bundle_digest: readRequiredString(value, "tested_bundle_digest"),
    comparison_bundle_digest: readRequiredString(value, "comparison_bundle_digest"),
    evaluator_epoch: readRequiredString(value, "evaluator_epoch"),
    trial_cohort: readRequiredString(value, "trial_cohort"),
    fixture_digest: readRequiredString(value, "fixture_digest"),
    seed: readNumber(value, "seed"),
    evidence_level: evidenceLevel,
    with_component_score: readNumber(value, "with_component_score"),
    without_component_score: readNumber(value, "without_component_score"),
    token_cost: readNumber(value, "token_cost"),
    tested_at: readRequiredString(value, "tested_at"),
    interaction_component_digests: readStringArray(value, "interaction_component_digests"),
  };
  validateTrial(trial);
  return trial;
}

export function parseContextAttributionLedger(value: unknown): ContextAttributionLedger {
  if (!isUnknownRecord(value)) {
    throw new Error("context attribution ledger must be an object");
  }
  const raw = value;
  if (raw.schema_version !== 1 && raw.schema_version !== 2) {
    throw new Error("context attribution ledger schema_version must be 1 or 2");
  }
  if (typeof raw.scenario !== "string" || raw.scenario.trim().length === 0) {
    throw new Error("context attribution ledger scenario is required");
  }
  if (!Array.isArray(raw.trials) || !Array.isArray(raw.attributions)) {
    throw new Error("context attribution ledger trials and attributions must be arrays");
  }
  const trials = raw.trials.map(parseControlledAttributionTrial);
  const attributions = raw.attributions.map((record) => parseComponentAttribution(
    record,
    raw.schema_version === 1,
  ));
  return {
    schema_version: 2,
    scenario: raw.scenario,
    trials,
    attributions,
  };
}

export function controlledTrialEffect(trial: ControlledAttributionTrial): number {
  if (!Number.isFinite(trial.with_component_score) || !Number.isFinite(trial.without_component_score)) {
    throw new Error("controlled attribution trial scores must be finite");
  }
  const effect = trial.with_component_score - trial.without_component_score;
  if (!Number.isFinite(effect)) throw new Error("controlled attribution trial effect must be finite");
  return roundSix(effect);
}

export function attributeControlledTrials(
  trials: readonly ControlledAttributionTrial[],
  opts: { evaluatorEpoch: string; neutralEffect?: number; highTokenCost?: number },
): ComponentAttribution[] {
  const neutralEffect = opts.neutralEffect ?? 0;
  if (!Number.isFinite(neutralEffect) || neutralEffect < 0) {
    throw new Error("neutralEffect must be finite and non-negative");
  }
  const highTokenCost = opts.highTokenCost ?? 256;
  if (!Number.isSafeInteger(highTokenCost) || highTokenCost < 0) {
    throw new Error("highTokenCost must be a non-negative safe integer");
  }
  const groups = new Map<string, ControlledAttributionTrial[]>();
  const trialIds = new Set<string>();
  const pairKeys = new Set<string>();
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
    if (trial.tested_bundle_digest === trial.comparison_bundle_digest) {
      throw new Error("controlled attribution requires distinct tested and comparison bundles");
    }
    const pairKey = controlledTrialPairKey(trial);
    if (pairKeys.has(pairKey)) throw new Error("duplicate matched attribution pair");
    pairKeys.add(pairKey);
    const key = [
      trial.component_kind,
      trial.component_key,
      trial.component_digest,
      trial.tested_bundle_digest,
      trial.evidence_level,
    ].join("\0");
    groups.set(key, [...(groups.get(key) ?? []), trial]);
  }

  return [...groups.values()]
    .sort((left, right) => {
      const leftKey = groupSortKey(left);
      const rightKey = groupSortKey(right);
      return leftKey === rightKey ? 0 : leftKey < rightKey ? -1 : 1;
    })
    .map((group) => attributionFromGroup(
      group,
      opts.evaluatorEpoch,
      neutralEffect,
      highTokenCost,
    ));
}

export function reconstructCausalCredit(
  attribution: ComponentAttribution,
  trials: readonly ControlledAttributionTrial[],
): number {
  if (attribution.evidence_level !== "causal_ablation") {
    throw new Error("only causal_ablation records carry causal credit");
  }
  const byId = new Map<string, ControlledAttributionTrial>();
  for (const trial of trials) {
    if (byId.has(trial.trial_id)) throw new Error(`duplicate attribution trial: ${trial.trial_id}`);
    byId.set(trial.trial_id, trial);
  }
  if (!isUniqueSorted(attribution.trial_ids)) {
    throw new Error("causal attribution trial IDs must be unique and sorted");
  }
  const referenced = attribution.trial_ids.map((trialId) => {
    const trial = byId.get(trialId);
    if (!trial) throw new Error(`missing referenced trial: ${trialId}`);
    return trial;
  });
  if (referenced.length === 0) throw new Error("causal attribution has no referenced trials");
  const effect = validateControlledAttributionBinding(attribution, referenced);
  if (effect !== attribution.effect) {
    throw new Error("causal attribution effect does not match its source trials");
  }
  return effect;
}

export function appendReablation(
  ledger: ContextAttributionLedger,
  trials: readonly ControlledAttributionTrial[],
  attributions: readonly ComponentAttribution[],
): ContextAttributionLedger {
  const existingTrialIds = new Set(ledger.trials.map((trial) => trial.trial_id));
  if (existingTrialIds.size !== ledger.trials.length) {
    throw new Error("attribution ledger contains duplicate trial IDs");
  }
  const existingPairKeys = new Set(ledger.trials.map(controlledTrialPairKey));
  if (existingPairKeys.size !== ledger.trials.length) {
    throw new Error("attribution ledger contains duplicate matched pairs");
  }
  const newById = new Map<string, ControlledAttributionTrial>();
  const newPairKeys = new Set<string>();
  for (const trial of trials) {
    if (existingTrialIds.has(trial.trial_id) || newById.has(trial.trial_id)) {
      throw new Error(`duplicate attribution trial: ${trial.trial_id}`);
    }
    const pairKey = controlledTrialPairKey(trial);
    if (existingPairKeys.has(pairKey) || newPairKeys.has(pairKey)) {
      throw new Error("duplicate matched attribution pair");
    }
    newById.set(trial.trial_id, trial);
    newPairKeys.add(pairKey);
  }

  const referencedTrialIds = new Set<string>();
  const seenAttributionIds = new Set(ledger.attributions.map((record) => record.attribution_id));
  for (const record of attributions) {
    if (seenAttributionIds.has(record.attribution_id)) {
      throw new Error(`duplicate component attribution: ${record.attribution_id}`);
    }
    seenAttributionIds.add(record.attribution_id);
    if (record.evidence_level === "component_correlated") {
      throw new Error("re-ablation evidence must come from controlled trials");
    }
    if (!isUniqueSorted(record.trial_ids)) {
      throw new Error("controlled attribution trial IDs must be unique and sorted");
    }
    const referenced = record.trial_ids.map((trialId) => {
      const trial = newById.get(trialId);
      if (!trial) throw new Error(`re-ablation attribution references a non-new trial: ${trialId}`);
      if (referencedTrialIds.has(trialId)) {
        throw new Error(`re-ablation trial is referenced more than once: ${trialId}`);
      }
      referencedTrialIds.add(trialId);
      return trial;
    });
    if (referenced.length === 0) throw new Error("re-ablation attribution has no referenced trials");
    const effect = validateControlledAttributionBinding(record, referenced);
    if (effect !== record.effect) {
      throw new Error("controlled attribution effect does not match its source trials");
    }
  }
  const unreferenced = [...newById.keys()]
    .filter((trialId) => !referencedTrialIds.has(trialId))
    .sort(compareUtf16);
  if (unreferenced[0]) {
    throw new Error(`re-ablation trial is not bound to an attribution: ${unreferenced[0]}`);
  }

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
    return difference || compareUtf16(componentIdentity(left), componentIdentity(right));
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
  const comparisonDigests = new Set(group.map((trial) => trial.comparison_bundle_digest));
  if (comparisonDigests.size !== 1) {
    throw new Error("controlled attribution group mixes comparison bundles");
  }
  const cohorts = new Set(group.map((trial) => trial.trial_cohort));
  if (cohorts.size !== 1) throw new Error("controlled attribution group mixes trial cohorts");
  const comparisonDigest = first.comparison_bundle_digest;
  const effects = group.map(controlledTrialEffect);
  const effect = roundSix(mean(effects));
  const tokenCost = Math.max(...group.map((trial) => trial.token_cost));
  const ids = group.map((trial) => trial.trial_id).sort(compareUtf16);
  const pairKeys = group.map(controlledTrialPairKey).sort(compareUtf16);
  const sourceTrialDigests = group.map(controlledTrialDigest).sort(compareUtf16);
  const interactions = [...new Set(group.flatMap((trial) => trial.interaction_component_digests))]
    .sort(compareUtf16);
  const attributionId = controlledAttributionId({
    componentKind: first.component_kind,
    componentKey: first.component_key,
    componentDigest: first.component_digest,
    testedBundleDigest: first.tested_bundle_digest,
    comparisonBundleDigest: comparisonDigest,
    evaluatorEpoch,
    trialCohort: first.trial_cohort,
    evidenceLevel: first.evidence_level,
    classificationNeutralEffect: neutralEffect,
    classificationHighTokenCost: highTokenCost,
    trialIds: ids,
    matchedPairKeys: pairKeys,
    sourceTrialDigests,
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
    comparison_bundle_digest: comparisonDigest,
    tested_at: group.map((trial) => trial.tested_at).sort().at(-1) ?? first.tested_at,
    disposition: classify(effect, tokenCost, neutralEffect, highTokenCost),
    classification_neutral_effect: neutralEffect,
    classification_high_token_cost: highTokenCost,
    trial_ids: ids,
    matched_pair_keys: pairKeys,
    source_trial_digests: sourceTrialDigests,
    interaction_component_digests: interactions,
    supersedes_attribution_id: null,
    legacy_unverified: false,
  };
}

function controlledTrialPairKey(trial: ControlledAttributionTrial): string {
  return stableDigest({
    component_kind: trial.component_kind,
    component_key: trial.component_key,
    component_digest: trial.component_digest,
    tested_bundle_digest: trial.tested_bundle_digest,
    comparison_bundle_digest: trial.comparison_bundle_digest,
    evaluator_epoch: trial.evaluator_epoch,
    trial_cohort: trial.trial_cohort,
    fixture_digest: trial.fixture_digest,
    seed: trial.seed,
  });
}

function controlledTrialDigest(trial: ControlledAttributionTrial): string {
  return stableDigest({
    trial_id: trial.trial_id,
    component_kind: trial.component_kind,
    component_key: trial.component_key,
    component_digest: trial.component_digest,
    tested_bundle_digest: trial.tested_bundle_digest,
    comparison_bundle_digest: trial.comparison_bundle_digest,
    evaluator_epoch: trial.evaluator_epoch,
    trial_cohort: trial.trial_cohort,
    fixture_digest: trial.fixture_digest,
    seed: trial.seed,
    evidence_level: trial.evidence_level,
    with_component_score: trial.with_component_score,
    without_component_score: trial.without_component_score,
    token_cost: trial.token_cost,
    tested_at: trial.tested_at,
    interaction_component_digests: trial.interaction_component_digests,
  });
}

function controlledAttributionId(input: {
  componentKind: string;
  componentKey: string;
  componentDigest: string;
  testedBundleDigest: string;
  comparisonBundleDigest: string;
  evaluatorEpoch: string;
  trialCohort: string;
  evidenceLevel: EvidenceLevel;
  classificationNeutralEffect: number;
  classificationHighTokenCost: number;
  trialIds: readonly string[];
  matchedPairKeys: readonly string[];
  sourceTrialDigests: readonly string[];
}): string {
  return stableDigest({
    component_kind: input.componentKind,
    component_key: input.componentKey,
    component_digest: input.componentDigest,
    tested_bundle_digest: input.testedBundleDigest,
    comparison_bundle_digest: input.comparisonBundleDigest,
    evaluator_epoch: input.evaluatorEpoch,
    trial_cohort: input.trialCohort,
    evidence_level: input.evidenceLevel,
    classification_neutral_effect: input.classificationNeutralEffect,
    classification_high_token_cost: input.classificationHighTokenCost,
    trial_ids: input.trialIds,
    matched_pair_keys: input.matchedPairKeys,
    source_trial_digests: input.sourceTrialDigests,
  });
}

function validateControlledAttributionBinding(
  attribution: ComponentAttribution,
  trials: readonly ControlledAttributionTrial[],
): number {
  if (attribution.legacy_unverified) {
    throw new Error("legacy attribution lacks verified controlled-trial provenance");
  }
  if (attribution.comparison_bundle_digest === null) {
    throw new Error("controlled attribution is missing its comparison bundle");
  }
  if (trials.length === 0) throw new Error("controlled attribution has no referenced trials");
  validateClassificationPolicy(attribution);

  const pairKeys: string[] = [];
  for (const trial of trials) {
    if (
      trial.evidence_level !== attribution.evidence_level
      || trial.component_kind !== attribution.component_kind
      || trial.component_key !== attribution.component_key
      || trial.component_digest !== attribution.component_digest
      || trial.tested_bundle_digest !== attribution.last_tested_bundle_digest
      || trial.comparison_bundle_digest !== attribution.comparison_bundle_digest
      || trial.evaluator_epoch !== attribution.evaluator_epoch
      || trial.trial_cohort !== attribution.trial_cohort
    ) {
      throw new Error("referenced trial does not match the controlled attribution");
    }
    if (trial.evidence_level === "component_correlated") {
      throw new Error("component_correlated evidence cannot back a controlled attribution");
    }
    if (trial.tested_bundle_digest === trial.comparison_bundle_digest) {
      throw new Error("controlled attribution requires distinct tested and comparison bundles");
    }
    pairKeys.push(controlledTrialPairKey(trial));
  }

  if (new Set(pairKeys).size !== pairKeys.length) {
    throw new Error("controlled attribution references duplicate matched pairs");
  }
  const expectedPairKeys = pairKeys.sort(compareUtf16);
  if (!sameStrings(attribution.matched_pair_keys, expectedPairKeys)) {
    throw new Error("controlled attribution matched-pair binding mismatch");
  }
  const expectedSourceDigests = trials.map(controlledTrialDigest).sort(compareUtf16);
  if (!sameStrings(attribution.source_trial_digests, expectedSourceDigests)) {
    throw new Error("controlled attribution source-trial binding mismatch");
  }

  const expectedId = controlledAttributionId({
    componentKind: attribution.component_kind,
    componentKey: attribution.component_key,
    componentDigest: attribution.component_digest,
    testedBundleDigest: attribution.last_tested_bundle_digest,
    comparisonBundleDigest: attribution.comparison_bundle_digest,
    evaluatorEpoch: attribution.evaluator_epoch,
    trialCohort: attribution.trial_cohort,
    evidenceLevel: attribution.evidence_level,
    classificationNeutralEffect: attribution.classification_neutral_effect,
    classificationHighTokenCost: attribution.classification_high_token_cost,
    trialIds: attribution.trial_ids,
    matchedPairKeys: attribution.matched_pair_keys,
    sourceTrialDigests: attribution.source_trial_digests,
  });
  if (attribution.attribution_id !== expectedId) {
    throw new Error("controlled attribution identity does not match its source trials");
  }

  const effects = trials.map(controlledTrialEffect);
  if (attribution.confidence !== trialConfidence(effects)) {
    throw new Error("controlled attribution confidence does not match its source trials");
  }
  const expectedTokenCost = Math.max(...trials.map((trial) => trial.token_cost));
  if (attribution.token_cost !== expectedTokenCost) {
    throw new Error("controlled attribution token cost does not match its source trials");
  }
  const testedAt = trials.map((trial) => trial.tested_at).sort().at(-1);
  if (attribution.tested_at !== testedAt) {
    throw new Error("controlled attribution test time does not match its source trials");
  }
  const interactions = [...new Set(trials.flatMap((trial) => trial.interaction_component_digests))]
    .sort(compareUtf16);
  if (!sameStrings(attribution.interaction_component_digests, interactions)) {
    throw new Error("controlled attribution interactions do not match its source trials");
  }
  const effect = roundSix(mean(effects));
  const expectedDisposition = classify(
    effect,
    expectedTokenCost,
    attribution.classification_neutral_effect,
    attribution.classification_high_token_cost,
  );
  if (attribution.disposition !== expectedDisposition) {
    throw new Error("controlled attribution disposition does not match its classification policy");
  }
  return effect;
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
  if (!Number.isSafeInteger(trial.token_cost) || trial.token_cost < 0) {
    throw new Error("controlled attribution trial token cost must be a non-negative safe integer");
  }
  controlledTrialEffect(trial);
}

function parseComponentAttribution(value: unknown, legacyLedger: boolean): ComponentAttribution {
  if (!isUnknownRecord(value)) {
    throw new Error("component attribution must be an object");
  }
  const raw = value;
  const provenanceFields = [
    "comparison_bundle_digest",
    "classification_neutral_effect",
    "classification_high_token_cost",
    "matched_pair_keys",
    "source_trial_digests",
  ];
  const hasVerifiedShape = provenanceFields.every((field) => field in raw);
  const migrated: Record<string, unknown> = legacyLedger
    ? {
      ...raw,
      comparison_bundle_digest: raw.comparison_bundle_digest ?? null,
      classification_neutral_effect: raw.classification_neutral_effect ?? 0,
      classification_high_token_cost: raw.classification_high_token_cost ?? 256,
      matched_pair_keys: raw.matched_pair_keys ?? [],
      source_trial_digests: raw.source_trial_digests ?? [],
      supersedes_attribution_id: raw.supersedes_attribution_id ?? null,
      legacy_unverified: raw.legacy_unverified === true || !hasVerifiedShape,
    }
    : raw;
  const parsed: ComponentAttribution = {
    attribution_id: readRequiredString(migrated, "attribution_id"),
    component_kind: readRequiredString(migrated, "component_kind"),
    component_key: readRequiredString(migrated, "component_key"),
    component_digest: readRequiredString(migrated, "component_digest"),
    evidence_level: readEvidenceLevel(migrated),
    effect: readNumber(migrated, "effect"),
    confidence: readNumber(migrated, "confidence"),
    evaluator_epoch: readRequiredString(migrated, "evaluator_epoch"),
    trial_cohort: readRequiredString(migrated, "trial_cohort"),
    token_cost: readNumber(migrated, "token_cost"),
    last_tested_bundle_digest: readRequiredString(migrated, "last_tested_bundle_digest"),
    comparison_bundle_digest: readNullableString(migrated, "comparison_bundle_digest"),
    tested_at: readRequiredString(migrated, "tested_at"),
    disposition: readDisposition(migrated),
    classification_neutral_effect: readNumber(migrated, "classification_neutral_effect"),
    classification_high_token_cost: readNumber(migrated, "classification_high_token_cost"),
    trial_ids: readStringArray(migrated, "trial_ids"),
    matched_pair_keys: readStringArray(migrated, "matched_pair_keys"),
    source_trial_digests: readStringArray(migrated, "source_trial_digests"),
    interaction_component_digests: readStringArray(migrated, "interaction_component_digests"),
    supersedes_attribution_id: readNullableString(migrated, "supersedes_attribution_id"),
    legacy_unverified: readBoolean(migrated, "legacy_unverified"),
  };
  validateClassificationPolicy(parsed);
  if (
    !Number.isFinite(parsed.effect)
    || !Number.isFinite(parsed.confidence)
    || parsed.confidence < 0
    || parsed.confidence > 1
  ) {
    throw new Error("component attribution effect and confidence must be finite");
  }
  if (!Number.isSafeInteger(parsed.token_cost) || parsed.token_cost < 0) {
    throw new Error("component attribution token cost must be a non-negative safe integer");
  }
  return parsed;
}

function validateClassificationPolicy(attribution: ComponentAttribution): void {
  if (
    !Number.isFinite(attribution.classification_neutral_effect)
    || attribution.classification_neutral_effect < 0
  ) {
    throw new Error("classification neutral effect must be finite and non-negative");
  }
  if (
    !Number.isSafeInteger(attribution.classification_high_token_cost)
    || attribution.classification_high_token_cost < 0
  ) {
    throw new Error("classification high token cost must be a non-negative safe integer");
  }
}

function verifiedDisposition(attribution: ComponentAttribution): ComponentDisposition {
  if (attribution.legacy_unverified) {
    throw new Error("legacy attribution lacks verified classification provenance");
  }
  validateClassificationPolicy(attribution);
  if (!Number.isFinite(attribution.effect)) throw new Error("attribution effect must be finite");
  if (!Number.isSafeInteger(attribution.token_cost) || attribution.token_cost < 0) {
    throw new Error("attribution token cost must be a non-negative safe integer");
  }
  return classify(
    attribution.effect,
    attribution.token_cost,
    attribution.classification_neutral_effect,
    attribution.classification_high_token_cost,
  );
}

function trialConfidence(effects: readonly number[]): number {
  if (effects.length === 0) return 0;
  const nonzero = effects.filter((effect) => effect !== 0);
  const agreement = nonzero.length === 0
    ? 1
    : Math.max(nonzero.filter((effect) => effect > 0).length, nonzero.filter((effect) => effect < 0).length)
      / nonzero.length;
  return roundSix(Math.min(1, effects.length / 4) * agreement);
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
  let disposition: ComponentDisposition = "uncertain";
  let policyValid = true;
  try {
    disposition = verifiedDisposition(record);
  } catch {
    policyValid = false;
  }
  if (!policyValid || record.disposition !== disposition) {
    return {
      ...identity,
      included: true,
      disposition: "uncertain",
      evidence_level: record.evidence_level,
      confidence: record.confidence,
      evaluator_epoch: record.evaluator_epoch,
      trial_cohort: record.trial_cohort,
      last_tested_bundle_digest: record.last_tested_bundle_digest,
      reason: "attribution disposition failed classification-policy verification; retain pending re-ablation",
    };
  }
  const demoted = disposition === "demotion_candidate" || disposition === "harmful";
  return {
    ...identity,
    included: !demoted,
    disposition,
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
  return first
    ? [
      first.component_kind,
      first.component_key,
      first.component_digest,
      first.tested_bundle_digest,
      first.evidence_level,
    ].join("\0")
    : "";
}

function sameStrings(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function isUniqueSorted(values: readonly string[]): boolean {
  return sameStrings(values, [...new Set(values)].sort(compareUtf16));
}

function mean(values: readonly number[]): number {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function roundSix(value: number): number {
  const scaled = Math.abs(value) * 1_000_000;
  if (!Number.isFinite(scaled)) return value;
  const result = Math.sign(value) * Math.floor(scaled + 0.5) / 1_000_000;
  return Object.is(result, -0) ? 0 : result;
}

function signed(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(4)}`;
}
