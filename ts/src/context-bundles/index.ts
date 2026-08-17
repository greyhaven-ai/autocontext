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

function validateTrials(candidate: ContextBundle, trials: readonly MatchedTrial[]): void {
  const seen = new Set<string>();
  for (const trial of trials) {
    if (trial.candidate_digest !== candidate.digest) throw new Error("trial candidate digest does not match the candidate bundle");
    if (trial.incumbent_digest !== candidate.parent_digest) throw new Error("trial incumbent digest does not match the candidate parent");
    if (trial.evaluator_epoch !== candidate.evaluator_epoch) throw new Error("trial evaluator epoch does not match the candidate bundle");
    if (!trial.cohort.trim() || !trial.fixture.trim() || !trial.fixture_digest.trim()) {
      throw new Error("matched trials require cohort, fixture, and fixture_digest");
    }
    if (!Number.isInteger(trial.seed)) throw new Error("matched trial seed must be an integer");
    if (![trial.candidate_score, trial.incumbent_score, delta(trial)].every(Number.isFinite)) {
      throw new Error("matched trial scores and effect must be finite");
    }
    const key = matchedTrialPairKey(trial);
    if (trial.pair_key !== undefined && trial.pair_key !== key) throw new Error("matched trial pair_key mismatch");
    if (seen.has(key)) throw new Error("duplicate matched trial pair");
    seen.add(key);
  }
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
