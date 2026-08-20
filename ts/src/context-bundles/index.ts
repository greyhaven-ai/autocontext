/** Immutable, outcome-gated context bundle contracts (AC-973). */

import { createHash } from "node:crypto";

export const CONTEXT_BUNDLE_SCHEMA_VERSION = 1 as const;

export type ComponentKind =
  | "playbook"
  | "hints"
  | "prompt_fragment"
  | "context_policy"
  | "completion_check"
  | "tool_guidance"
  | "tool_spec"
  | "harness_validator"
  | "routing_config";

export type BundleLifecycle =
  | "proposed"
  | "screened"
  | "confirmed"
  | "active"
  | "rejected"
  | "superseded";

export type TrialLane = "screen" | "confirmation" | "heldout";
export type ComparisonDecision =
  | "needs_screen"
  | "needs_confirmation"
  | "needs_heldout"
  | "confirmed"
  | "rejected"
  | "inconclusive";

export interface BundleComponent {
  kind: ComponentKind;
  key: string;
  content: string;
  media_type: string;
  digest: string;
}

export interface ContextBundle {
  schema_version: typeof CONTEXT_BUNDLE_SCHEMA_VERSION;
  scenario: string;
  evaluator_epoch: string;
  parent_digest: string | null;
  components: readonly BundleComponent[];
  digest: string;
}

export interface BundleManifestChange {
  component_kind: string;
  component_key: string;
  tested_component_digest: string | null;
  comparison_component_digest: string | null;
}

export interface ContextBundleManifestDiff {
  schema_version: 1;
  tested_bundle_digest: string;
  comparison_bundle_digest: string;
  evaluator_epoch: string;
  changes: readonly BundleManifestChange[];
  digest: string;
}

export interface MatchedTrial {
  candidate_digest: string;
  incumbent_digest: string | null;
  evaluator_epoch: string;
  cohort: string;
  fixture: string;
  fixture_digest: string;
  seed: number;
  lane: TrialLane;
  candidate_score: number;
  incumbent_score: number;
  candidate_valid: boolean;
  incumbent_valid: boolean;
  pair_key?: string;
}

export interface ConfirmationPolicy {
  min_screen_pairs: number;
  min_confirmation_pairs: number;
  max_confirmation_pairs: number;
  min_heldout_pairs: number;
  min_effect: number;
  confidence_z: number;
}

export interface ComparisonResult {
  decision: ComparisonDecision;
  reason: string;
  screen_pairs: number;
  confirmation_pairs: number;
  heldout_pairs: number;
  mean_effect: number | null;
  confidence_low: number | null;
  confidence_high: number | null;
}

export interface CampaignFalsePromotionPolicy {
  familywise_alpha: number;
  allocation_decay: number;
  min_independent_confirmation_blocks: number;
  require_disjoint_lane_blocks: boolean;
  robust_method: "cluster_t" | "bounded_hoeffding";
  effect_lower_bound: number;
  effect_upper_bound: number;
}

export interface CampaignFalsePromotionEvidenceResult {
  authorized: boolean;
  reason: string;
  independent_confirmation_blocks: number;
  independent_heldout_blocks: number;
}

export type FalsePromotionStatus = "reserved" | "authorized" | "rejected" | "inconclusive" | "blocked";

export interface CandidateRiskReservation {
  campaign_id: string;
  candidate_digest: string;
  incumbent_digest: string;
  evaluator_epoch: string;
  candidate_index: number;
  allocated_alpha: number;
  required_confidence_z: number;
  confirmation_policy_digest: string;
  status: FalsePromotionStatus;
  reason: string | null;
  evidence_digest: string | null;
  independent_confirmation_blocks: number;
  independent_heldout_blocks: number;
}

export interface CampaignFixtureUnit {
  lane: TrialLane;
  fixture_digest: string;
  seed: number;
}

export interface CandidateFixtureReservation {
  campaign_id: string;
  candidate_digest: string;
  evaluator_epoch: string;
  cohort: string;
  units: readonly CampaignFixtureUnit[];
  plan_digest: string;
}

export interface CampaignFalsePromotionState {
  schema_version: 2;
  campaign_id: string;
  policy: CampaignFalsePromotionPolicy;
  reservations: readonly CandidateRiskReservation[];
  fixture_reservations: readonly CandidateFixtureReservation[];
  fixture_history_complete: boolean;
  state_digest: string;
}

export const DEFAULT_CONFIRMATION_POLICY: Readonly<ConfirmationPolicy> = Object.freeze({
  min_screen_pairs: 2,
  min_confirmation_pairs: 6,
  max_confirmation_pairs: 20,
  min_heldout_pairs: 2,
  min_effect: 0,
  confidence_z: 1.96,
});

export const DEFAULT_CAMPAIGN_FALSE_PROMOTION_POLICY: Readonly<CampaignFalsePromotionPolicy> = Object.freeze({
  familywise_alpha: 0.05,
  allocation_decay: 0.5,
  min_independent_confirmation_blocks: 2,
  require_disjoint_lane_blocks: true,
  robust_method: "cluster_t",
  effect_lower_bound: -1,
  effect_upper_bound: 1,
});

const COMPONENT_KIND_SET = new Set<string>([
  "playbook",
  "hints",
  "prompt_fragment",
  "context_policy",
  "completion_check",
  "tool_guidance",
  "tool_spec",
  "harness_validator",
  "routing_config",
]);
const TRIAL_LANE_SET = new Set<string>(["screen", "confirmation", "heldout"]);

function compareUtf16(left: string, right: string): number {
  if (left === right) return 0;
  return left < right ? -1 : 1;
}

export function canonicalJson(value: unknown): string {
  if (value === null || typeof value === "boolean") {
    return JSON.stringify(value);
  }
  if (typeof value === "string") {
    validateUnicodeScalars(value);
    return JSON.stringify(value);
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new TypeError("canonical JSON does not permit non-finite numbers");
    if (Number.isInteger(value) && !Number.isSafeInteger(value)) {
      throw new TypeError("canonical JSON integers must be within the JavaScript safe integer range");
    }
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (typeof value === "object") {
    return `{${Object.entries(value)
      .sort(([left], [right]) => compareUtf16(left, right))
      .map(([key, item]) => `${canonicalJson(key)}:${canonicalJson(item)}`)
      .join(",")}}`;
  }
  throw new TypeError(`value is not canonical-JSON serializable: ${typeof value}`);
}

export function stableDigest(value: unknown): string {
  return createHash("sha256").update(canonicalJson(value), "utf8").digest("hex");
}

export function createBundleComponent(
  kind: ComponentKind,
  key: string,
  content: string,
  mediaType = "text/plain",
): BundleComponent {
  if (!key.trim()) throw new Error("bundle component key is required");
  if (!mediaType.trim()) throw new Error("bundle component media_type is required");
  const payload = { kind, key, content, media_type: mediaType };
  return Object.freeze({ ...payload, digest: stableDigest(payload) });
}

export function createJsonBundleComponent(
  kind: ComponentKind,
  key: string,
  value: unknown,
): BundleComponent {
  return createBundleComponent(kind, key, canonicalJson(value), "application/json");
}

export function createContextBundle(input: {
  scenario: string;
  evaluatorEpoch: string;
  parentDigest?: string | null;
  components: readonly BundleComponent[];
}): ContextBundle {
  if (!input.scenario.trim()) throw new Error("context bundle scenario is required");
  if (!input.evaluatorEpoch.trim()) throw new Error("context bundle evaluator_epoch is required");
  const components = [...input.components].sort((left, right) => {
    const kindOrder = compareUtf16(left.kind, right.kind);
    return kindOrder === 0 ? compareUtf16(left.key, right.key) : kindOrder;
  });
  const identities = components.map((component) => `${component.kind}\0${component.key}`);
  if (new Set(identities).size !== identities.length) {
    throw new Error("context bundle component kind/key pairs must be unique");
  }
  const payload = {
    schema_version: CONTEXT_BUNDLE_SCHEMA_VERSION,
    scenario: input.scenario,
    evaluator_epoch: input.evaluatorEpoch,
    parent_digest: input.parentDigest ?? null,
    components,
  };
  return Object.freeze({ ...payload, components: Object.freeze(components), digest: stableDigest(payload) });
}

export function contextBundleManifestDiff(
  tested: ContextBundle,
  comparison: ContextBundle,
): ContextBundleManifestDiff {
  if (tested.scenario !== comparison.scenario) {
    throw new Error("context bundle manifest diff requires the same scenario");
  }
  if (tested.evaluator_epoch !== comparison.evaluator_epoch) {
    throw new Error("context bundle manifest diff requires the same evaluator epoch");
  }
  const testedComponents = new Map(tested.components.map((component) => [
    `${component.kind}\0${component.key}`,
    component,
  ]));
  const comparisonComponents = new Map(comparison.components.map((component) => [
    `${component.kind}\0${component.key}`,
    component,
  ]));
  const identities = [...new Set([...testedComponents.keys(), ...comparisonComponents.keys()])]
    .sort(compareUtf16);
  const changes: BundleManifestChange[] = [];
  for (const identity of identities) {
    const testedComponent = testedComponents.get(identity);
    const comparisonComponent = comparisonComponents.get(identity);
    if (testedComponent?.digest === comparisonComponent?.digest) continue;
    const separator = identity.indexOf("\0");
    changes.push({
      component_kind: identity.slice(0, separator),
      component_key: identity.slice(separator + 1),
      tested_component_digest: testedComponent?.digest ?? null,
      comparison_component_digest: comparisonComponent?.digest ?? null,
    });
  }
  const payload = {
    schema_version: 1 as const,
    tested_bundle_digest: tested.digest,
    comparison_bundle_digest: comparison.digest,
    evaluator_epoch: tested.evaluator_epoch,
    changes,
  };
  return Object.freeze({ ...payload, changes: Object.freeze(changes), digest: stableDigest(payload) });
}

export function validateContextBundle(value: unknown): ContextBundle {
  if (!isContextBundle(value)) throw new Error("context bundle has an invalid shape");
  const raw = value;
  const components = raw.components.map((component) => {
    const rebuilt = createBundleComponent(component.kind, component.key, component.content, component.media_type);
    if (rebuilt.digest !== component.digest) throw new Error(`component digest mismatch for ${component.key}`);
    return rebuilt;
  });
  const rebuilt = createContextBundle({
    scenario: raw.scenario,
    evaluatorEpoch: raw.evaluator_epoch,
    parentDigest: raw.parent_digest,
    components,
  });
  if (rebuilt.digest !== raw.digest) throw new Error("context bundle digest mismatch");
  return rebuilt;
}

function isContextBundle(value: unknown): value is ContextBundle {
  if (!value || typeof value !== "object") return false;
  if (!("schema_version" in value) || value.schema_version !== CONTEXT_BUNDLE_SCHEMA_VERSION) return false;
  if (!("scenario" in value) || typeof value.scenario !== "string") return false;
  if (!("evaluator_epoch" in value) || typeof value.evaluator_epoch !== "string") return false;
  if (!("parent_digest" in value) || (value.parent_digest !== null && typeof value.parent_digest !== "string")) {
    return false;
  }
  if (!("digest" in value) || typeof value.digest !== "string") return false;
  return "components" in value && Array.isArray(value.components) && value.components.every(isBundleComponent);
}

function isBundleComponent(value: unknown): value is BundleComponent {
  if (!value || typeof value !== "object") return false;
  return (
    "kind" in value &&
    typeof value.kind === "string" &&
    COMPONENT_KIND_SET.has(value.kind) &&
    "key" in value &&
    typeof value.key === "string" &&
    "content" in value &&
    typeof value.content === "string" &&
    "media_type" in value &&
    typeof value.media_type === "string" &&
    "digest" in value &&
    typeof value.digest === "string"
  );
}

function validateUnicodeScalars(value: string): void {
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xD800 && unit <= 0xDBFF) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xDC00 && next <= 0xDFFF)) {
        throw new TypeError("canonical JSON does not permit lone UTF-16 surrogates");
      }
      index += 1;
    } else if (unit >= 0xDC00 && unit <= 0xDFFF) {
      throw new TypeError("canonical JSON does not permit lone UTF-16 surrogates");
    }
  }
}

export function matchedTrialPairKey(trial: MatchedTrial): string {
  // Lane and display name are metadata, not independent evidence. The same
  // evaluation unit must collide if either is relabeled.
  return stableDigest({
    evaluator_epoch: trial.evaluator_epoch,
    cohort: trial.cohort,
    fixture_digest: trial.fixture_digest,
    seed: trial.seed,
  });
}

export function evaluateMatchedTrials(
  candidate: ContextBundle,
  trials: readonly MatchedTrial[],
  policy: ConfirmationPolicy = DEFAULT_CONFIRMATION_POLICY,
): ComparisonResult {
  validateConfirmationPolicy(policy);
  validateTrials(candidate, trials);
  const screen = trials.filter((trial) => trial.lane === "screen");
  const confirmation = trials.filter((trial) => trial.lane === "confirmation");
  const heldout = trials.filter((trial) => trial.lane === "heldout");
  const counts = {
    screen_pairs: screen.length,
    confirmation_pairs: confirmation.length,
    heldout_pairs: heldout.length,
  };
  const result = (
    decision: ComparisonDecision,
    reason: string,
    meanEffect: number | null = null,
    low: number | null = null,
    high: number | null = null,
  ): ComparisonResult => ({
    decision,
    reason,
    ...counts,
    mean_effect: meanEffect,
    confidence_low: low,
    confidence_high: high,
  });
  if (confirmation.length > policy.max_confirmation_pairs) {
    throw new Error("confirmation pairs exceed the configured maximum");
  }
  if (screen.length < policy.min_screen_pairs) return result("needs_screen", "insufficient matched screen pairs");
  if (screen.some((trial) => !trial.candidate_valid || !trial.incumbent_valid)) {
    return result("rejected", "screen validity failure");
  }
  const screenEffect = mean(screen.map(delta));
  if (screenEffect <= policy.min_effect) return result("rejected", "candidate failed the cheap matched screen", screenEffect);
  if (confirmation.length < policy.min_confirmation_pairs) {
    return result("needs_confirmation", "screen passed; more matched confirmation pairs required", screenEffect);
  }
  if (confirmation.some((trial) => !trial.candidate_valid || !trial.incumbent_valid)) {
    return result("rejected", "confirmation validity failure");
  }
  const maxLooks = policy.max_confirmation_pairs - policy.min_confirmation_pairs + 1;
  const [meanEffect, low, high] = confidenceInterval(
    confirmation.map(delta),
    policy.confidence_z,
    maxLooks,
  );
  if (high <= policy.min_effect) {
    return result("rejected", "confirmation confidence interval is below the minimum effect", meanEffect, low, high);
  }
  if (low <= policy.min_effect) {
    return confirmation.length >= policy.max_confirmation_pairs
      ? result("inconclusive", "maximum confirmation budget reached without a decisive effect", meanEffect, low, high)
      : result("needs_confirmation", "confirmation uncertainty overlaps the minimum effect", meanEffect, low, high);
  }
  if (heldout.length < policy.min_heldout_pairs) {
    return result("needs_heldout", "confirmation passed; held-out matched pairs required", meanEffect, low, high);
  }
  if (heldout.some((trial) => !trial.candidate_valid || !trial.incumbent_valid)) {
    return result("rejected", "held-out validity failure", meanEffect, low, high);
  }
  const heldoutEffect = mean(heldout.map(delta));
  if (heldoutEffect <= policy.min_effect) {
    return result("rejected", "candidate regressed on the held-out lane", heldoutEffect, low, high);
  }
  return result("confirmed", "matched confirmation and held-out lanes passed", meanEffect, low, high);
}

export function campaignAlphaForCandidate(
  candidateIndex: number,
  policy: CampaignFalsePromotionPolicy = DEFAULT_CAMPAIGN_FALSE_PROMOTION_POLICY,
): number {
  validateCampaignFalsePromotionPolicy(policy);
  if (!Number.isInteger(candidateIndex) || candidateIndex < 0) {
    throw new Error("candidate_index must be a non-negative integer");
  }
  const allocated = policy.familywise_alpha
    * (1 - policy.allocation_decay)
    * policy.allocation_decay ** candidateIndex;
  if (allocated === 0) {
    throw new Error("campaign alpha allocation underflowed; no further promotion can be authorized");
  }
  return allocated;
}

export function requiredConfidenceZ(allocatedAlpha: number): number {
  if (!Number.isFinite(allocatedAlpha) || !(allocatedAlpha > 0 && allocatedAlpha < 1)) {
    throw new Error("allocated_alpha must be finite and between zero and one");
  }
  // Invert the same erfc implementation used by confidenceInterval. This
  // keeps the TypeScript policy internally consistent with Python's
  // two-sided normal-tail configuration surface.
  let lower = 0;
  let upper = 1;
  while (complementaryErrorFunction(upper / Math.SQRT2) > allocatedAlpha) {
    lower = upper;
    upper *= 2;
    if (!Number.isFinite(upper)) {
      throw new Error("campaign alpha allocation is too small to invert");
    }
  }
  for (let iteration = 0; iteration < 120; iteration += 1) {
    const midpoint = (lower + upper) / 2;
    if (complementaryErrorFunction(midpoint / Math.SQRT2) > allocatedAlpha) lower = midpoint;
    else upper = midpoint;
  }
  return Math.ceil(upper * 1e12) / 1e12;
}

export function campaignAdjustedConfirmationPolicy(
  basePolicy: ConfirmationPolicy,
  candidateIndex: number,
  campaignPolicy: CampaignFalsePromotionPolicy = DEFAULT_CAMPAIGN_FALSE_PROMOTION_POLICY,
): ConfirmationPolicy {
  validateConfirmationPolicy(basePolicy);
  const requiredZ = requiredConfidenceZ(campaignAlphaForCandidate(candidateIndex, campaignPolicy));
  return { ...basePolicy, confidence_z: Math.max(basePolicy.confidence_z, requiredZ) };
}

export function createCandidateRiskReservation(
  campaignId: string,
  candidateValue: unknown,
  candidateIndex: number,
  basePolicy: ConfirmationPolicy,
  campaignPolicy: CampaignFalsePromotionPolicy = DEFAULT_CAMPAIGN_FALSE_PROMOTION_POLICY,
): { confirmationPolicy: ConfirmationPolicy; reservation: CandidateRiskReservation } {
  if (!campaignId.trim()) throw new Error("campaign_id is required");
  const candidate = validateContextBundle(candidateValue);
  if (candidate.parent_digest === null) {
    throw new Error("false-promotion control requires a candidate incumbent");
  }
  const confirmationPolicy = campaignAdjustedConfirmationPolicy(basePolicy, candidateIndex, campaignPolicy);
  const allocatedAlpha = campaignAlphaForCandidate(candidateIndex, campaignPolicy);
  return {
    confirmationPolicy,
    reservation: {
      campaign_id: campaignId,
      candidate_digest: candidate.digest,
      incumbent_digest: candidate.parent_digest,
      evaluator_epoch: candidate.evaluator_epoch,
      candidate_index: candidateIndex,
      allocated_alpha: allocatedAlpha,
      required_confidence_z: requiredConfidenceZ(allocatedAlpha),
      confirmation_policy_digest: stableDigest(confirmationPolicy),
      status: "reserved",
      reason: null,
      evidence_digest: null,
      independent_confirmation_blocks: 0,
      independent_heldout_blocks: 0,
    },
  };
}

export function createCampaignFalsePromotionState(
  campaignId: string,
  policy: CampaignFalsePromotionPolicy,
  reservations: readonly CandidateRiskReservation[],
  fixtureReservations: readonly CandidateFixtureReservation[] = [],
  fixtureHistoryComplete = true,
): CampaignFalsePromotionState {
  validateCampaignFalsePromotionPolicy(policy);
  if (!campaignId.trim()) throw new Error("campaign_id is required");
  reservations.forEach((reservation, index) => {
    if (reservation.campaign_id !== campaignId || reservation.candidate_index !== index) {
      throw new Error("campaign false-promotion candidate indices or identities are invalid");
    }
    if (reservation.allocated_alpha !== campaignAlphaForCandidate(index, policy)) {
      throw new Error("campaign false-promotion alpha allocation mismatch");
    }
  });
  if (typeof fixtureHistoryComplete !== "boolean") {
    throw new Error("campaign fixture history completeness must be boolean");
  }
  const riskByCandidate = new Map(
    reservations.map((reservation) => [reservation.candidate_digest, reservation]),
  );
  if (riskByCandidate.size !== reservations.length) {
    throw new Error("campaign false-promotion reservations contain duplicate candidates");
  }
  const fixtureOwners = new Map<string, string>();
  const fixtureCandidates = new Set<string>();
  fixtureReservations.forEach((reservation) => {
    const riskReservation = riskByCandidate.get(reservation.candidate_digest);
    if (
      reservation.campaign_id !== campaignId
      || riskReservation === undefined
      || riskReservation.evaluator_epoch !== reservation.evaluator_epoch
      || !reservation.evaluator_epoch
      || !reservation.cohort
      || reservation.units.length === 0
    ) {
      throw new Error("campaign fixture reservation lineage is invalid");
    }
    if (fixtureCandidates.has(reservation.candidate_digest)) {
      throw new Error("campaign fixture reservations contain duplicate candidates");
    }
    fixtureCandidates.add(reservation.candidate_digest);
    validateFixtureUnits(reservation.units);
    const planPayload = {
      campaign_id: reservation.campaign_id,
      candidate_digest: reservation.candidate_digest,
      evaluator_epoch: reservation.evaluator_epoch,
      cohort: reservation.cohort,
      units: reservation.units.map((unit) => ({ ...unit })),
    };
    if (stableDigest(planPayload) !== reservation.plan_digest) {
      throw new Error("campaign fixture plan digest mismatch");
    }
    reservation.units.forEach((unit) => {
      if (!TRIAL_LANE_SET.has(unit.lane) || !unit.fixture_digest || !Number.isSafeInteger(unit.seed)) {
        throw new Error("campaign fixture unit is invalid");
      }
      const owner = fixtureOwners.get(unit.fixture_digest);
      if (owner !== undefined && owner !== reservation.candidate_digest) {
        throw new Error("actual fixture is reserved by multiple campaign candidates");
      }
      fixtureOwners.set(unit.fixture_digest, reservation.candidate_digest);
    });
  });
  const payload = {
    schema_version: 2 as const,
    campaign_id: campaignId,
    policy: { ...policy },
    reservations: reservations.map((reservation) => ({ ...reservation })),
    fixture_reservations: fixtureReservations.map((reservation) => ({
      ...reservation,
      units: reservation.units.map((unit) => ({ ...unit })),
    })),
    fixture_history_complete: fixtureHistoryComplete,
  };
  return { ...payload, state_digest: stableDigest(payload) };
}

export function createCandidateFixtureReservation(
  campaignId: string,
  candidateValue: unknown,
  cohort: string,
  units: readonly CampaignFixtureUnit[],
): CandidateFixtureReservation {
  const candidate = validateContextBundle(candidateValue);
  if (!campaignId.trim() || !cohort.trim()) {
    throw new Error("campaign fixture reservation identities must be non-empty");
  }
  if (units.length === 0) throw new Error("campaign fixture reservation requires a non-empty plan");
  const copiedUnits = units.map((unit) => ({ ...unit }));
  validateFixtureUnits(copiedUnits);
  const payload = {
    campaign_id: campaignId,
    candidate_digest: candidate.digest,
    evaluator_epoch: candidate.evaluator_epoch,
    cohort,
    units: copiedUnits,
  };
  return { ...payload, plan_digest: stableDigest(payload) };
}

export function evaluateCampaignFalsePromotionEvidence(
  candidateValue: unknown,
  trialsValue: readonly unknown[],
  confirmationPolicy: ConfirmationPolicy,
  reservation: CandidateRiskReservation,
  fixtureReservation: CandidateFixtureReservation,
  campaignPolicy: CampaignFalsePromotionPolicy = DEFAULT_CAMPAIGN_FALSE_PROMOTION_POLICY,
): CampaignFalsePromotionEvidenceResult {
  const candidate = validateContextBundle(candidateValue);
  validateConfirmationPolicy(confirmationPolicy);
  validateCampaignFalsePromotionPolicy(campaignPolicy);
  validateCandidateRiskReservation(reservation, candidate, confirmationPolicy, campaignPolicy);
  const trials: MatchedTrial[] = [];
  for (const value of trialsValue) {
    if (!isMatchedTrial(value)) throw new Error("matched trial has an invalid shape");
    trials.push(value);
  }
  validateTrials(candidate, trials);
  validateCandidateFixtureReservation(
    fixtureReservation,
    candidate,
    reservation,
    trials,
  );
  const evidenceDigest = stableDigest(
    [...trials]
      .sort((left, right) => compareUtf16(matchedTrialPairKey(left), matchedTrialPairKey(right)))
      .map((trial) => ({
        candidate_digest: trial.candidate_digest,
        incumbent_digest: trial.incumbent_digest,
        evaluator_epoch: trial.evaluator_epoch,
        cohort: trial.cohort,
        fixture: trial.fixture,
        fixture_digest: trial.fixture_digest,
        seed: trial.seed,
        lane: trial.lane,
        candidate_score: trial.candidate_score,
        incumbent_score: trial.incumbent_score,
        candidate_valid: trial.candidate_valid,
        incumbent_valid: trial.incumbent_valid,
        pair_key: matchedTrialPairKey(trial),
      })),
  );
  if (reservation.evidence_digest !== null && reservation.evidence_digest !== evidenceDigest) {
    throw new Error("false-promotion reservation evidence digest mismatch");
  }
  const cohorts = new Set(trials.map((trial) => trial.cohort));
  if (cohorts.size !== 1) throw new Error("false-promotion evidence must use exactly one trial cohort");
  const replayed = evaluateMatchedTrials(candidate, trials, confirmationPolicy);
  if (replayed.decision !== "confirmed") {
    return falsePromotionEvidenceResult(
      false,
      "matched evidence does not reproduce a confirmed comparison",
      0,
      0,
    );
  }
  const lanes = new Map<TrialLane, Map<string, number[]>>([
    ["screen", new Map()],
    ["confirmation", new Map()],
    ["heldout", new Map()],
  ]);
  for (const trial of trials) {
    const blocks = lanes.get(trial.lane)!;
    const effects = blocks.get(trial.fixture_digest) ?? [];
    effects.push(delta(trial));
    blocks.set(trial.fixture_digest, effects);
  }
  const screenBlocks = lanes.get("screen")!;
  const confirmationBlocks = lanes.get("confirmation")!;
  const heldoutBlocks = lanes.get("heldout")!;
  if (campaignPolicy.require_disjoint_lane_blocks && (
    mapsOverlap(screenBlocks, confirmationBlocks)
    || mapsOverlap(screenBlocks, heldoutBlocks)
    || mapsOverlap(confirmationBlocks, heldoutBlocks)
  )) {
    return falsePromotionEvidenceResult(false, "dependence blocks overlap across evaluation lanes", 0, 0);
  }

  const confirmationEffects = blockMeans(confirmationBlocks);
  const heldoutEffects = blockMeans(heldoutBlocks);
  const requiredConfirmationBlocks = Math.max(
    confirmationPolicy.min_confirmation_pairs,
    campaignPolicy.min_independent_confirmation_blocks,
  );
  if (confirmationEffects.length < requiredConfirmationBlocks) {
    return falsePromotionEvidenceResult(
      false,
      "insufficient independent confirmation blocks after non-IID clustering",
      confirmationEffects.length,
      heldoutEffects.length,
    );
  }
  if (heldoutEffects.length < confirmationPolicy.min_heldout_pairs) {
    return falsePromotionEvidenceResult(
      false,
      "insufficient independent held-out blocks after non-IID clustering",
      confirmationEffects.length,
      heldoutEffects.length,
    );
  }
  const maxLooks = confirmationPolicy.max_confirmation_pairs - confirmationPolicy.min_confirmation_pairs + 1;
  let confidenceLow: number;
  if (campaignPolicy.robust_method === "bounded_hoeffding") {
    const allEffects = trials.map(delta);
    if (allEffects.some((effect) => (
      effect < campaignPolicy.effect_lower_bound || effect > campaignPolicy.effect_upper_bound
    ))) {
      return falsePromotionEvidenceResult(
        false,
        "paired effect falls outside the predeclared robust bounds",
        confirmationEffects.length,
        heldoutEffects.length,
      );
    }
    const familyAlpha = complementaryErrorFunction(confirmationPolicy.confidence_z / Math.SQRT2);
    const lookAlpha = familyAlpha / maxLooks;
    const width = campaignPolicy.effect_upper_bound - campaignPolicy.effect_lower_bound;
    confidenceLow = mean(confirmationEffects)
      - width * Math.sqrt(Math.log(1 / lookAlpha) / (2 * confirmationEffects.length));
  } else {
    [, confidenceLow] = confidenceInterval(
      confirmationEffects,
      confirmationPolicy.confidence_z,
      maxLooks,
    );
  }
  if (confidenceLow <= confirmationPolicy.min_effect) {
    return falsePromotionEvidenceResult(
      false,
      "campaign-adjusted block confidence interval does not clear the minimum effect",
      confirmationEffects.length,
      heldoutEffects.length,
    );
  }
  if (mean(heldoutEffects) <= confirmationPolicy.min_effect) {
    return falsePromotionEvidenceResult(
      false,
      "independent held-out blocks do not clear the minimum effect",
      confirmationEffects.length,
      heldoutEffects.length,
    );
  }
  return falsePromotionEvidenceResult(
    true,
    "campaign alpha reservation and dependence-aware evidence authorized promotion",
    confirmationEffects.length,
    heldoutEffects.length,
  );
}

function validateCampaignFalsePromotionPolicy(policy: CampaignFalsePromotionPolicy): void {
  if (!Number.isFinite(policy.familywise_alpha) || !(policy.familywise_alpha > 0 && policy.familywise_alpha < 1)) {
    throw new Error("familywise_alpha must be finite and between zero and one");
  }
  if (!Number.isFinite(policy.allocation_decay) || !(policy.allocation_decay > 0 && policy.allocation_decay < 1)) {
    throw new Error("allocation_decay must be finite and between zero and one");
  }
  if (!Number.isInteger(policy.min_independent_confirmation_blocks)
    || policy.min_independent_confirmation_blocks < 2) {
    throw new Error("min_independent_confirmation_blocks must be an integer of at least two");
  }
  if (typeof policy.require_disjoint_lane_blocks !== "boolean") {
    throw new Error("require_disjoint_lane_blocks must be a boolean");
  }
  if (policy.robust_method !== "cluster_t" && policy.robust_method !== "bounded_hoeffding") {
    throw new Error("robust_method must be cluster_t or bounded_hoeffding");
  }
  if (!Number.isFinite(policy.effect_lower_bound)
    || !Number.isFinite(policy.effect_upper_bound)
    || policy.effect_lower_bound >= policy.effect_upper_bound) {
    throw new Error("effect bounds must be finite and increasing");
  }
}

function validateFixtureUnits(units: readonly CampaignFixtureUnit[]): void {
  const seen = new Set<string>();
  for (const unit of units) {
    if (
      !unit
      || typeof unit !== "object"
      || !TRIAL_LANE_SET.has(unit.lane)
      || typeof unit.fixture_digest !== "string"
      || !unit.fixture_digest.trim()
      || !Number.isSafeInteger(unit.seed)
    ) {
      throw new Error("campaign fixture unit is invalid");
    }
    const identity = stableDigest({ fixture_digest: unit.fixture_digest, seed: unit.seed });
    if (seen.has(identity)) {
      throw new Error("campaign fixture plan contains duplicate fixture/seed identities");
    }
    seen.add(identity);
  }
}

function validateCandidateFixtureReservation(
  fixtureReservation: CandidateFixtureReservation,
  candidate: ContextBundle,
  riskReservation: CandidateRiskReservation,
  trials: readonly MatchedTrial[],
): void {
  if (!fixtureReservation || typeof fixtureReservation !== "object") {
    throw new Error("campaign fixture reservation must be an object");
  }
  if (
    typeof fixtureReservation.campaign_id !== "string"
    || typeof fixtureReservation.candidate_digest !== "string"
    || typeof fixtureReservation.evaluator_epoch !== "string"
    || typeof fixtureReservation.cohort !== "string"
    || typeof fixtureReservation.plan_digest !== "string"
    || !Array.isArray(fixtureReservation.units)
    || fixtureReservation.units.length === 0
  ) {
    throw new Error("campaign fixture reservation identity is invalid");
  }
  validateFixtureUnits(fixtureReservation.units);
  const payload = {
    campaign_id: fixtureReservation.campaign_id,
    candidate_digest: fixtureReservation.candidate_digest,
    evaluator_epoch: fixtureReservation.evaluator_epoch,
    cohort: fixtureReservation.cohort,
    units: fixtureReservation.units.map((unit) => ({ ...unit })),
  };
  if (stableDigest(payload) !== fixtureReservation.plan_digest) {
    throw new Error("campaign fixture plan digest mismatch");
  }
  if (
    fixtureReservation.campaign_id !== riskReservation.campaign_id
    || fixtureReservation.candidate_digest !== candidate.digest
    || fixtureReservation.evaluator_epoch !== candidate.evaluator_epoch
  ) {
    throw new Error("campaign fixture reservation lineage does not match the candidate risk reservation");
  }
  const planned = new Map<string, TrialLane>();
  for (const unit of fixtureReservation.units) {
    planned.set(
      stableDigest({ fixture_digest: unit.fixture_digest, seed: unit.seed }),
      unit.lane,
    );
  }
  for (const trial of trials) {
    if (trial.cohort !== fixtureReservation.cohort) {
      throw new Error("matched evidence cohort differs from its fixture reservation");
    }
    const plannedLane = planned.get(
      stableDigest({ fixture_digest: trial.fixture_digest, seed: trial.seed }),
    );
    if (plannedLane === undefined) {
      throw new Error("matched evidence contains a fixture outside its reserved plan");
    }
    if (plannedLane !== trial.lane) {
      throw new Error("matched evidence relabels a reserved fixture lane");
    }
  }
}

function validateCandidateRiskReservation(
  reservation: CandidateRiskReservation,
  candidate: ContextBundle,
  confirmationPolicy: ConfirmationPolicy,
  campaignPolicy: CampaignFalsePromotionPolicy,
): void {
  const statuses = new Set<FalsePromotionStatus>([
    "reserved",
    "authorized",
    "rejected",
    "inconclusive",
    "blocked",
  ]);
  if (!reservation || typeof reservation !== "object") {
    throw new Error("false-promotion reservation must be an object");
  }
  if (
    typeof reservation.campaign_id !== "string"
    || !reservation.campaign_id.trim()
    || typeof reservation.candidate_digest !== "string"
    || typeof reservation.incumbent_digest !== "string"
    || typeof reservation.evaluator_epoch !== "string"
    || typeof reservation.confirmation_policy_digest !== "string"
    || !statuses.has(reservation.status)
  ) {
    throw new Error("false-promotion reservation identity or status is invalid");
  }
  if (
    reservation.candidate_digest !== candidate.digest
    || reservation.incumbent_digest !== candidate.parent_digest
    || reservation.evaluator_epoch !== candidate.evaluator_epoch
  ) {
    throw new Error("candidate lineage does not match its false-promotion reservation");
  }
  if (!Number.isSafeInteger(reservation.candidate_index) || reservation.candidate_index < 0) {
    throw new Error("false-promotion reservation candidate_index is invalid");
  }
  const allocatedAlpha = campaignAlphaForCandidate(reservation.candidate_index, campaignPolicy);
  const requiredZ = requiredConfidenceZ(allocatedAlpha);
  if (
    reservation.allocated_alpha !== allocatedAlpha
    || reservation.required_confidence_z !== requiredZ
  ) {
    throw new Error("false-promotion reservation alpha allocation mismatch");
  }
  if (reservation.confirmation_policy_digest !== stableDigest(confirmationPolicy)) {
    throw new Error("promotion evidence used a policy different from its risk reservation");
  }
  if (!Number.isFinite(reservation.required_confidence_z) || confirmationPolicy.confidence_z < requiredZ) {
    throw new Error("false-promotion reservation confidence threshold is invalid");
  }
  if (
    (reservation.reason !== null && typeof reservation.reason !== "string")
    || (reservation.evidence_digest !== null && typeof reservation.evidence_digest !== "string")
    || !Number.isSafeInteger(reservation.independent_confirmation_blocks)
    || reservation.independent_confirmation_blocks < 0
    || !Number.isSafeInteger(reservation.independent_heldout_blocks)
    || reservation.independent_heldout_blocks < 0
  ) {
    throw new Error("false-promotion reservation evidence metadata is invalid");
  }
  if (
    reservation.status !== "reserved"
    || reservation.reason !== null
    || reservation.evidence_digest !== null
    || reservation.independent_confirmation_blocks !== 0
    || reservation.independent_heldout_blocks !== 0
  ) {
    throw new Error("only a pristine reserved false-promotion allocation can evaluate new evidence");
  }
}

function falsePromotionEvidenceResult(
  authorized: boolean,
  reason: string,
  independentConfirmationBlocks: number,
  independentHeldoutBlocks: number,
): CampaignFalsePromotionEvidenceResult {
  return {
    authorized,
    reason,
    independent_confirmation_blocks: independentConfirmationBlocks,
    independent_heldout_blocks: independentHeldoutBlocks,
  };
}

function mapsOverlap(left: ReadonlyMap<string, unknown>, right: ReadonlyMap<string, unknown>): boolean {
  for (const key of left.keys()) if (right.has(key)) return true;
  return false;
}

function blockMeans(blocks: ReadonlyMap<string, readonly number[]>): number[] {
  return [...blocks.keys()]
    .sort(compareUtf16)
    .map((key) => mean(blocks.get(key)!));
}

function validateTrials(candidate: ContextBundle, trials: readonly MatchedTrial[]): void {
  const seen = new Set<string>();
  for (const trial of trials) {
    if (!trial || typeof trial !== "object") throw new Error("matched trial must be an object");
    if (
      typeof trial.candidate_digest !== "string"
      || (trial.incumbent_digest !== null && typeof trial.incumbent_digest !== "string")
      || typeof trial.evaluator_epoch !== "string"
      || typeof trial.cohort !== "string"
      || typeof trial.fixture !== "string"
      || typeof trial.fixture_digest !== "string"
      || !TRIAL_LANE_SET.has(trial.lane)
    ) {
      throw new Error("matched trial identity has an invalid shape");
    }
    if (trial.candidate_digest !== candidate.digest) throw new Error("trial candidate digest does not match the candidate bundle");
    if (trial.incumbent_digest !== candidate.parent_digest) throw new Error("trial incumbent digest does not match the candidate parent");
    if (trial.evaluator_epoch !== candidate.evaluator_epoch) throw new Error("trial evaluator epoch does not match the candidate bundle");
    if (!trial.cohort.trim() || !trial.fixture.trim() || !trial.fixture_digest.trim()) {
      throw new Error("matched trials require cohort, fixture, and fixture_digest");
    }
    if (!Number.isSafeInteger(trial.seed)) throw new Error("matched trial seed must be a safe integer");
    if (typeof trial.candidate_valid !== "boolean" || typeof trial.incumbent_valid !== "boolean") {
      throw new Error("matched trial validity flags must be booleans");
    }
    if (![trial.candidate_score, trial.incumbent_score, delta(trial)].every(Number.isFinite)) {
      throw new Error("matched trial scores and effect must be finite");
    }
    const key = matchedTrialPairKey(trial);
    if (trial.pair_key !== undefined && trial.pair_key !== key) throw new Error("matched trial pair_key mismatch");
    if (seen.has(key)) throw new Error("duplicate matched trial pair");
    seen.add(key);
  }
}

function isMatchedTrial(value: unknown): value is MatchedTrial {
  if (!value || typeof value !== "object") return false;
  return (
    "candidate_digest" in value
    && typeof value.candidate_digest === "string"
    && "incumbent_digest" in value
    && (value.incumbent_digest === null || typeof value.incumbent_digest === "string")
    && "evaluator_epoch" in value
    && typeof value.evaluator_epoch === "string"
    && "cohort" in value
    && typeof value.cohort === "string"
    && "fixture" in value
    && typeof value.fixture === "string"
    && "fixture_digest" in value
    && typeof value.fixture_digest === "string"
    && "seed" in value
    && typeof value.seed === "number"
    && Number.isSafeInteger(value.seed)
    && "lane" in value
    && typeof value.lane === "string"
    && TRIAL_LANE_SET.has(value.lane)
    && "candidate_score" in value
    && typeof value.candidate_score === "number"
    && Number.isFinite(value.candidate_score)
    && "incumbent_score" in value
    && typeof value.incumbent_score === "number"
    && Number.isFinite(value.incumbent_score)
    && "candidate_valid" in value
    && typeof value.candidate_valid === "boolean"
    && "incumbent_valid" in value
    && typeof value.incumbent_valid === "boolean"
    && (!("pair_key" in value) || value.pair_key === undefined || typeof value.pair_key === "string")
  );
}

function validateConfirmationPolicy(policy: ConfirmationPolicy): void {
  const counts: Array<[string, number]> = [
    ["min_screen_pairs", policy.min_screen_pairs],
    ["min_confirmation_pairs", policy.min_confirmation_pairs],
    ["max_confirmation_pairs", policy.max_confirmation_pairs],
    ["min_heldout_pairs", policy.min_heldout_pairs],
  ];
  for (const [name, value] of counts) {
    if (!Number.isInteger(value)) throw new Error(`${name} must be an integer`);
  }
  if (policy.min_screen_pairs < 1) throw new Error("min_screen_pairs must be positive");
  if (policy.min_confirmation_pairs < 2) throw new Error("min_confirmation_pairs must be at least 2");
  if (policy.max_confirmation_pairs < policy.min_confirmation_pairs) {
    throw new Error("max_confirmation_pairs must be >= min_confirmation_pairs");
  }
  if (policy.min_heldout_pairs < 1) throw new Error("min_heldout_pairs must be positive");
  if (!Number.isFinite(policy.min_effect)) throw new Error("min_effect must be finite");
  if (!Number.isFinite(policy.confidence_z) || policy.confidence_z <= 0) {
    throw new Error("confidence_z must be finite and positive");
  }
}

function delta(trial: MatchedTrial): number {
  return trial.candidate_score - trial.incumbent_score;
}

function mean(values: readonly number[]): number {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function confidenceInterval(values: readonly number[], z: number, maxLooks: number): [number, number, number] {
  const average = mean(values);
  const variance = values.reduce((sum, value) => sum + (value - average) ** 2, 0) / (values.length - 1);
  const standardError = Math.sqrt(variance) / Math.sqrt(values.length);
  if (standardError === 0) return [average, average, average];

  // ``confidence_z`` is the backwards-compatible configuration surface. As
  // in Python, convert it to a two-sided normal tail probability, reserve that
  // family-wise error rate across every possible adaptive look, and use the
  // corresponding small-sample Student-t critical value.
  const familyAlpha = complementaryErrorFunction(z / Math.SQRT2);
  const lookAlpha = familyAlpha / maxLooks;
  const critical = studentTUpperQuantile(lookAlpha / 2, values.length - 1);
  if (!Number.isFinite(critical)) return [average, -Number.MAX_VALUE, Number.MAX_VALUE];
  const halfWidth = critical * standardError;
  if (!Number.isFinite(halfWidth)) return [average, -Number.MAX_VALUE, Number.MAX_VALUE];
  return [average, average - halfWidth, average + halfWidth];
}

function studentTUpperQuantile(tailProbability: number, degreesOfFreedom: number): number {
  if (!(tailProbability >= 0 && tailProbability < 0.5)) {
    throw new Error("tail probability must be between zero and one half");
  }
  if (degreesOfFreedom < 1) throw new Error("degrees_of_freedom must be positive");
  if (tailProbability === 0) return Number.POSITIVE_INFINITY;

  let lower = 0;
  let upper = 1;
  while (studentTSurvival(upper, degreesOfFreedom) > tailProbability) {
    upper *= 2;
    if (!Number.isFinite(upper)) return upper;
  }
  for (let iteration = 0; iteration < 80; iteration += 1) {
    const midpoint = (lower + upper) / 2;
    if (studentTSurvival(midpoint, degreesOfFreedom) > tailProbability) lower = midpoint;
    else upper = midpoint;
  }
  return (lower + upper) / 2;
}

function studentTSurvival(value: number, degreesOfFreedom: number): number {
  const betaX = degreesOfFreedom / (degreesOfFreedom + value * value);
  return 0.5 * regularizedIncompleteBeta(betaX, degreesOfFreedom / 2, 0.5);
}

function regularizedIncompleteBeta(value: number, alpha: number, beta: number): number {
  if (value <= 0) return 0;
  if (value >= 1) return 1;
  const logFront = logGamma(alpha + beta)
    - logGamma(alpha)
    - logGamma(beta)
    + alpha * Math.log(value)
    + beta * Math.log1p(-value);
  const front = Math.exp(logFront);
  if (value < (alpha + 1) / (alpha + beta + 2)) {
    return front * betaContinuedFraction(alpha, beta, value) / alpha;
  }
  return 1 - front * betaContinuedFraction(beta, alpha, 1 - value) / beta;
}

function betaContinuedFraction(alpha: number, beta: number, value: number): number {
  const maxIterations = 200;
  const epsilon = 3e-14;
  const tiny = 1e-300;
  const qab = alpha + beta;
  const qap = alpha + 1;
  const qam = alpha - 1;
  let numerator = 1;
  let denominator = 1 - qab * value / qap;
  if (Math.abs(denominator) < tiny) denominator = tiny;
  denominator = 1 / denominator;
  let result = denominator;

  for (let iteration = 1; iteration <= maxIterations; iteration += 1) {
    const evenStep = 2 * iteration;
    let coefficient = iteration * (beta - iteration) * value
      / ((qam + evenStep) * (alpha + evenStep));
    denominator = 1 + coefficient * denominator;
    if (Math.abs(denominator) < tiny) denominator = tiny;
    numerator = 1 + coefficient / numerator;
    if (Math.abs(numerator) < tiny) numerator = tiny;
    denominator = 1 / denominator;
    result *= denominator * numerator;

    coefficient = -(alpha + iteration) * (qab + iteration) * value
      / ((alpha + evenStep) * (qap + evenStep));
    denominator = 1 + coefficient * denominator;
    if (Math.abs(denominator) < tiny) denominator = tiny;
    numerator = 1 + coefficient / numerator;
    if (Math.abs(numerator) < tiny) numerator = tiny;
    denominator = 1 / denominator;
    const change = denominator * numerator;
    result *= change;
    if (Math.abs(change - 1) < epsilon) return result;
  }
  throw new Error("incomplete beta continued fraction did not converge");
}

function complementaryErrorFunction(value: number): number {
  const complement = regularizedGammaQ(0.5, value * value);
  return value < 0 ? 2 - complement : complement;
}

function regularizedGammaQ(alpha: number, value: number): number {
  if (value < alpha + 1) return 1 - regularizedGammaSeries(alpha, value);

  const epsilon = 1e-15;
  const tiny = 1e-300;
  let denominatorBase = value + 1 - alpha;
  let coefficient = 1 / tiny;
  let denominator = 1 / denominatorBase;
  let result = denominator;
  for (let iteration = 1; iteration <= 200; iteration += 1) {
    const numerator = -iteration * (iteration - alpha);
    denominatorBase += 2;
    denominator = numerator * denominator + denominatorBase;
    if (Math.abs(denominator) < tiny) denominator = tiny;
    coefficient = denominatorBase + numerator / coefficient;
    if (Math.abs(coefficient) < tiny) coefficient = tiny;
    denominator = 1 / denominator;
    const change = denominator * coefficient;
    result *= change;
    if (Math.abs(change - 1) < epsilon) {
      return Math.exp(-value + alpha * Math.log(value) - logGamma(alpha)) * result;
    }
  }
  throw new Error("incomplete gamma continued fraction did not converge");
}

function regularizedGammaSeries(alpha: number, value: number): number {
  if (value === 0) return 0;
  const epsilon = 1e-15;
  let denominator = alpha;
  let term = 1 / alpha;
  let sum = term;
  for (let iteration = 1; iteration <= 200; iteration += 1) {
    denominator += 1;
    term *= value / denominator;
    sum += term;
    if (Math.abs(term) < Math.abs(sum) * epsilon) {
      return sum * Math.exp(-value + alpha * Math.log(value) - logGamma(alpha));
    }
  }
  throw new Error("incomplete gamma series did not converge");
}

function logGamma(value: number): number {
  const coefficients = [
    676.5203681218851,
    -1259.1392167224028,
    771.3234287776531,
    -176.6150291621406,
    12.507343278686905,
    -0.13857109526572012,
    9.984369578019572e-6,
    1.5056327351493116e-7,
  ];
  if (value < 0.5) {
    return Math.log(Math.PI) - Math.log(Math.sin(Math.PI * value)) - logGamma(1 - value);
  }
  const shifted = value - 1;
  let series = 0.9999999999998099;
  for (let index = 0; index < coefficients.length; index += 1) {
    series += coefficients[index]! / (shifted + index + 1);
  }
  const base = shifted + coefficients.length - 0.5;
  return 0.5 * Math.log(2 * Math.PI) + (shifted + 0.5) * Math.log(base) - base + Math.log(series);
}
