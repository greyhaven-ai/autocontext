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

export const DEFAULT_CONFIRMATION_POLICY: Readonly<ConfirmationPolicy> = Object.freeze({
  min_screen_pairs: 2,
  min_confirmation_pairs: 6,
  max_confirmation_pairs: 20,
  min_heldout_pairs: 2,
  min_effect: 0,
  confidence_z: 1.96,
});

export function canonicalJson(value: unknown): string {
  if (value === null || typeof value === "boolean" || typeof value === "string") {
    return JSON.stringify(value);
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new TypeError("canonical JSON does not permit non-finite numbers");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (typeof value === "object") {
    return `{${Object.entries(value)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`)
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
  const components = [...input.components].sort((left, right) =>
    `${left.kind}\0${left.key}`.localeCompare(`${right.kind}\0${right.key}`),
  );
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

export function matchedTrialPairKey(trial: MatchedTrial): string {
  return stableDigest({
    evaluator_epoch: trial.evaluator_epoch,
    cohort: trial.cohort,
    fixture: trial.fixture,
    fixture_digest: trial.fixture_digest,
    seed: trial.seed,
    lane: trial.lane,
  });
}

export function evaluateMatchedTrials(
  candidate: ContextBundle,
  trials: readonly MatchedTrial[],
  policy: ConfirmationPolicy = DEFAULT_CONFIRMATION_POLICY,
): ComparisonResult {
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
  const [meanEffect, low, high] = confidenceInterval(confirmation.map(delta), policy.confidence_z);
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

function validateTrials(candidate: ContextBundle, trials: readonly MatchedTrial[]): void {
  const seen = new Set<string>();
  for (const trial of trials) {
    if (trial.candidate_digest !== candidate.digest) throw new Error("trial candidate digest does not match the candidate bundle");
    if (trial.incumbent_digest !== candidate.parent_digest) throw new Error("trial incumbent digest does not match the candidate parent");
    if (trial.evaluator_epoch !== candidate.evaluator_epoch) throw new Error("trial evaluator epoch does not match the candidate bundle");
    if (!trial.cohort.trim() || !trial.fixture.trim() || !trial.fixture_digest.trim()) {
      throw new Error("matched trials require cohort, fixture, and fixture_digest");
    }
    const key = matchedTrialPairKey(trial);
    if (trial.pair_key !== undefined && trial.pair_key !== key) throw new Error("matched trial pair_key mismatch");
    if (seen.has(key)) throw new Error("duplicate matched trial pair");
    seen.add(key);
  }
}

function delta(trial: MatchedTrial): number {
  return trial.candidate_score - trial.incumbent_score;
}

function mean(values: readonly number[]): number {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function confidenceInterval(values: readonly number[], z: number): [number, number, number] {
  const average = mean(values);
  const variance = values.reduce((sum, value) => sum + (value - average) ** 2, 0) / (values.length - 1);
  const halfWidth = z * Math.sqrt(variance) / Math.sqrt(values.length);
  return [average, average - halfWidth, average + halfWidth];
}
