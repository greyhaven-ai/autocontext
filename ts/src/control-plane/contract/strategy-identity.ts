import { createHash } from "node:crypto";
import { canonicalJsonStringify } from "./canonical-json.js";
import { parseContentHash, type ContentHash, type Scenario } from "./branded-ids.js";
import type {
  ActuatorType,
  Artifact,
  StrategyComponentFingerprint,
  StrategyDuplicateAssessment,
  StrategyIdentity,
} from "./types.js";
import type { TreeFile } from "./invariants.js";

const STRATEGY_IDENTITY_VERSION = 1;
const DEFAULT_NEAR_DUPLICATE_THRESHOLD = 0.5;

export interface BuildStrategyIdentityInputs {
  readonly actuatorType: ActuatorType;
  readonly scenario: Scenario;
  readonly payloadHash: ContentHash;
  readonly components: readonly StrategyComponentFingerprint[];
  readonly parentFingerprints: readonly ContentHash[];
}

export function buildStrategyIdentity(inputs: BuildStrategyIdentityInputs): StrategyIdentity {
  const components = normalizeComponents(inputs.components);
  const parentFingerprints = normalizeFingerprints(inputs.parentFingerprints);
  const fingerprint = hashCanonical({
    version: STRATEGY_IDENTITY_VERSION,
    actuatorType: inputs.actuatorType,
    scenario: inputs.scenario,
    payloadHash: inputs.payloadHash,
    components,
  });

  return {
    fingerprint,
    payloadHash: inputs.payloadHash,
    components,
    lineage: { parentFingerprints },
  };
}

export function buildStrategyComponentsFromTree(
  files: readonly TreeFile[],
): readonly StrategyComponentFingerprint[] {
  return normalizeComponents(
    files.map((file) => ({
      name: file.path,
      fingerprint: hashBytes(file.content),
    })),
  );
}

export function strategyFingerprintForArtifact(
  artifact: Pick<Artifact, "actuatorType" | "scenario" | "payloadHash" | "strategyIdentity">,
): ContentHash {
  if (artifact.strategyIdentity !== undefined) return artifact.strategyIdentity.fingerprint;
  return artifact.payloadHash;
}

export function detectStrategyDuplicate(
  candidate: StrategyIdentity,
  actuatorType: ActuatorType,
  scenario: Scenario,
  existingArtifacts: readonly Pick<Artifact, "id" | "actuatorType" | "scenario" | "payloadHash" | "strategyIdentity">[],
  threshold = DEFAULT_NEAR_DUPLICATE_THRESHOLD,
): StrategyDuplicateAssessment | null {
  let bestNear: StrategyDuplicateAssessment | null = null;

  for (const existing of existingArtifacts) {
    if (existing.actuatorType !== actuatorType || existing.scenario !== scenario) continue;
    const prior = existing.strategyIdentity;

    if (prior?.fingerprint === candidate.fingerprint) {
      return {
        kind: "exact",
        artifactId: existing.id,
        fingerprint: prior.fingerprint,
        similarity: 1,
      };
    }
    if (prior === undefined) {
      if (candidate.payloadHash !== undefined && existing.payloadHash === candidate.payloadHash) {
        return {
          kind: "exact",
          artifactId: existing.id,
          fingerprint: existing.payloadHash,
          similarity: 1,
        };
      }
      continue;
    }

    const similarity = strategyComponentSimilarity(candidate.components, prior.components);
    if (similarity < threshold) continue;
    const near: StrategyDuplicateAssessment = {
      kind: "near",
      artifactId: existing.id,
      fingerprint: prior.fingerprint,
      similarity,
    };
    if (isBetterDuplicate(near, bestNear)) {
      bestNear = near;
    }
  }

  return bestNear;
}

function strategyComponentSimilarity(
  left: readonly StrategyComponentFingerprint[],
  right: readonly StrategyComponentFingerprint[],
): number {
  if (left.length === 0 || right.length === 0) return 0;
  const leftNames = new Set(left.map((c) => c.name));
  const rightNames = new Set(right.map((c) => c.name));
  const nameUnion = unionSize(leftNames, rightNames);
  if (nameUnion === 0) return 0;
  const nameIntersection = intersectionSize(leftNames, rightNames);

  const leftPairs = new Set(left.map((c) => `${c.name}\0${c.fingerprint}`));
  const rightPairs = new Set(right.map((c) => `${c.name}\0${c.fingerprint}`));
  const pairUnion = unionSize(leftPairs, rightPairs);
  const pairIntersection = intersectionSize(leftPairs, rightPairs);

  const nameScore = nameIntersection / nameUnion;
  const valueScore = pairUnion === 0 ? 0 : pairIntersection / pairUnion;
  return roundSimilarity((nameScore + valueScore) / 2);
}

function normalizeComponents(
  components: readonly StrategyComponentFingerprint[],
): readonly StrategyComponentFingerprint[] {
  const byName = new Map<string, ContentHash>();
  for (const component of components) {
    const existing = byName.get(component.name);
    if (existing !== undefined && existing !== component.fingerprint) {
      throw new Error(`duplicate strategy component name with different fingerprints: ${component.name}`);
    }
    byName.set(component.name, component.fingerprint);
  }

  return Array.from(byName.entries())
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([name, fingerprint]) => ({ name, fingerprint }));
}

function normalizeFingerprints(fingerprints: readonly ContentHash[]): readonly ContentHash[] {
  return Array.from(new Set(fingerprints)).sort();
}

function isBetterDuplicate(
  candidate: StrategyDuplicateAssessment,
  incumbent: StrategyDuplicateAssessment | null,
): boolean {
  if (incumbent === null) return true;
  if (candidate.similarity !== incumbent.similarity) return candidate.similarity > incumbent.similarity;
  return candidate.artifactId < incumbent.artifactId;
}

function unionSize<T>(left: ReadonlySet<T>, right: ReadonlySet<T>): number {
  const union = new Set<T>(left);
  for (const item of right) union.add(item);
  return union.size;
}

function intersectionSize<T>(left: ReadonlySet<T>, right: ReadonlySet<T>): number {
  let count = 0;
  for (const item of left) {
    if (right.has(item)) count += 1;
  }
  return count;
}

function roundSimilarity(value: number): number {
  return Math.round(value * 1_000_000) / 1_000_000;
}

function hashCanonical(value: unknown): ContentHash {
  return hashBytes(Buffer.from(canonicalJsonStringify(value), "utf8"));
}

function hashBytes(value: Uint8Array): ContentHash {
  const parsed = parseContentHash(`sha256:${createHash("sha256").update(value).digest("hex")}`);
  if (parsed === null) {
    throw new Error("strategy identity hash failed content-hash validation");
  }
  return parsed;
}
