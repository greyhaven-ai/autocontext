import { parseContentHash, type ContentHash } from "../contract/branded-ids.js";
import type { EvalRunIntegrity, ValidationResult } from "../contract/types.js";

export type OperationalMemoryPackStatus = "draft" | "sanitized" | "active" | "deprecated";
export type OperationalMemoryRisk = "low" | "medium" | "high";
export type OperationalMemoryContextSkipReason =
  | "pack-status-not-eligible"
  | "pack-integrity-not-clean"
  | "leakage-risk"
  | "duplicate-finding"
  | "strategy-quarantined"
  | "target-family-mismatch"
  | "risk-too-high"
  | "capacity-limit";

export interface OperationalMemoryFinding {
  readonly id: string;
  readonly summary: string;
  readonly evidenceRefs: readonly string[];
  readonly reusableBehavior: string;
  readonly targetFamilies: readonly string[];
  readonly risk: OperationalMemoryRisk;
  readonly containsTaskAnswer?: boolean;
  readonly containsSecret?: boolean;
  readonly strategyFingerprint?: ContentHash;
}

export interface OperationalMemoryPack {
  readonly packId: string;
  readonly version: string;
  readonly createdAt: string;
  readonly status: OperationalMemoryPackStatus;
  readonly integrity?: EvalRunIntegrity;
  readonly findings: readonly OperationalMemoryFinding[];
}

export interface CompileOperationalMemoryContextInputs {
  readonly contextId: string;
  readonly createdAt: string;
  readonly packs: readonly OperationalMemoryPack[];
  readonly targetFamilies: readonly string[];
  readonly taskId?: string;
  readonly quarantinedStrategyFingerprints?: readonly ContentHash[];
  readonly maxFindings?: number;
  readonly riskTolerance?: OperationalMemoryRisk;
}

export interface OperationalMemorySelectedFinding {
  readonly packId: string;
  readonly findingId: string;
  readonly summary: string;
  readonly evidenceRefs: readonly string[];
  readonly reusableBehavior: string;
  readonly targetFamilies: readonly string[];
  readonly matchedTargetFamilies: readonly string[];
  readonly risk: OperationalMemoryRisk;
}

export interface OperationalMemorySkippedFinding {
  readonly packId: string;
  readonly findingId: string;
  readonly reason: OperationalMemoryContextSkipReason;
  readonly detail?: string;
}

export interface OperationalMemoryContextApplication {
  readonly schemaVersion: "operational-memory-context/v1";
  readonly contextId: string;
  readonly createdAt: string;
  readonly taskId?: string;
  readonly targetFamilies: readonly string[];
  readonly maxFindings: number;
  readonly riskTolerance: OperationalMemoryRisk;
  readonly selectedFindings: readonly OperationalMemorySelectedFinding[];
  readonly skippedFindings: readonly OperationalMemorySkippedFinding[];
  readonly prompt: string;
}

interface CandidateFinding {
  readonly originalIndex: number;
  readonly score: number;
  readonly finding: OperationalMemorySelectedFinding;
}

export function compileOperationalMemoryContext(
  inputs: CompileOperationalMemoryContextInputs,
): OperationalMemoryContextApplication {
  const targetFamilies = uniqueNormalizedStrings(inputs.targetFamilies);
  const maxFindings = normalizedMaxFindings(inputs.maxFindings);
  const riskTolerance = inputs.riskTolerance ?? "medium";
  const skippedFindings: OperationalMemorySkippedFinding[] = [];
  const candidates: CandidateFinding[] = [];
  const candidateIds = new Set<string>();
  const quarantinedStrategyFingerprints = new Set(inputs.quarantinedStrategyFingerprints ?? []);
  let originalIndex = 0;

  for (const pack of inputs.packs) {
    if (!isEligiblePackStatus(pack.status)) {
      skipPackFindings(skippedFindings, pack, "pack-status-not-eligible", `status=${pack.status}`);
      continue;
    }
    if (pack.integrity !== undefined && pack.integrity.status !== "clean") {
      skipPackFindings(
        skippedFindings,
        pack,
        "pack-integrity-not-clean",
        `integrity=${pack.integrity.status}`,
      );
      continue;
    }

    for (const finding of pack.findings) {
      originalIndex += 1;
      if (candidateIds.has(finding.id)) {
        skippedFindings.push({
          packId: pack.packId,
          findingId: finding.id,
          reason: "duplicate-finding",
        });
        continue;
      }
      const leakageDetail = findingLeakageRiskDetail(finding);
      if (leakageDetail !== undefined) {
        skippedFindings.push({
          packId: pack.packId,
          findingId: finding.id,
          reason: "leakage-risk",
          detail: leakageDetail,
        });
        continue;
      }
      const strategyQuarantineDetail = findingStrategyQuarantineDetail(
        finding,
        quarantinedStrategyFingerprints,
      );
      if (strategyQuarantineDetail !== undefined) {
        skippedFindings.push({
          packId: pack.packId,
          findingId: finding.id,
          reason: "strategy-quarantined",
          detail: strategyQuarantineDetail,
        });
        continue;
      }
      if (riskRank(finding.risk) > riskRank(riskTolerance)) {
        skippedFindings.push({
          packId: pack.packId,
          findingId: finding.id,
          reason: "risk-too-high",
          detail: `risk=${finding.risk}; tolerance=${riskTolerance}`,
        });
        continue;
      }

      const matchedTargetFamilies = intersectNormalizedFamilies(targetFamilies, finding.targetFamilies);
      if (matchedTargetFamilies.length === 0) {
        skippedFindings.push({
          packId: pack.packId,
          findingId: finding.id,
          reason: "target-family-mismatch",
        });
        continue;
      }

      candidateIds.add(finding.id);
      candidates.push({
        originalIndex,
        score: matchedTargetFamilies.length,
        finding: {
          packId: pack.packId,
          findingId: finding.id,
          summary: finding.summary,
          evidenceRefs: finding.evidenceRefs,
          reusableBehavior: finding.reusableBehavior,
          targetFamilies: finding.targetFamilies,
          matchedTargetFamilies,
          risk: finding.risk,
        },
      });
    }
  }

  const rankedCandidates = [...candidates].sort(compareCandidates);
  const selectedFindings = rankedCandidates.slice(0, maxFindings).map((candidate) => candidate.finding);
  for (const candidate of rankedCandidates.slice(maxFindings)) {
    skippedFindings.push({
      packId: candidate.finding.packId,
      findingId: candidate.finding.findingId,
      reason: "capacity-limit",
      detail: `maxFindings=${maxFindings}`,
    });
  }

  return {
    schemaVersion: "operational-memory-context/v1",
    contextId: inputs.contextId,
    createdAt: inputs.createdAt,
    ...(inputs.taskId !== undefined ? { taskId: inputs.taskId } : {}),
    targetFamilies,
    maxFindings,
    riskTolerance,
    selectedFindings,
    skippedFindings,
    prompt: renderOperationalMemoryPrompt(selectedFindings),
  };
}

export function validateOperationalMemoryPack(input: unknown): ValidationResult {
  const errors: string[] = [];

  if (!isRecord(input)) {
    return { valid: false, errors: ["memory pack must be an object"] };
  }

  requireString(input, "packId", errors);
  requireString(input, "version", errors);
  requireString(input, "createdAt", errors);
  requireEnum(input, "status", ["draft", "sanitized", "active", "deprecated"], errors);
  validateOptionalIntegrity(input.integrity, errors);

  if (!Array.isArray(input.findings)) {
    errors.push("findings must be an array");
  } else {
    for (const finding of input.findings) {
      validateFinding(finding, errors);
    }
  }

  return errors.length === 0 ? { valid: true } : { valid: false, errors };
}

function validateOptionalIntegrity(input: unknown, errors: string[]): void {
  if (input === undefined) return;
  if (!isRecord(input)) {
    errors.push("integrity must be an object when present");
    return;
  }

  requireEnum(input, "status", ["clean", "discarded", "contaminated"], errors);
  requireOptionalString(input, "discardedReason", "integrity", errors);
  if (
    Object.prototype.hasOwnProperty.call(input, "notes") &&
    (!Array.isArray(input.notes) || !input.notes.every((item) => typeof item === "string" && item.length > 0))
  ) {
    errors.push("integrity notes must be an array of non-empty strings when present");
  }
}

function validateFinding(input: unknown, errors: string[]): void {
  if (!isRecord(input)) {
    errors.push("finding must be an object");
    return;
  }

  const id = typeof input.id === "string" && input.id.length > 0 ? input.id : "<unknown>";
  requireString(input, "id", errors);
  requireString(input, "summary", errors);
  requireString(input, "reusableBehavior", errors);
  requireStringArray(input, "evidenceRefs", errors);
  requireStringArray(input, "targetFamilies", errors);
  requireEnum(input, "risk", ["low", "medium", "high"], errors);
  requireOptionalBoolean(input, "containsTaskAnswer", id, errors);
  requireOptionalBoolean(input, "containsSecret", id, errors);
  requireOptionalContentHash(input, "strategyFingerprint", id, errors);

  if (input.containsTaskAnswer === true) {
    errors.push(`finding ${id} contains task-specific answer material`);
  }
  if (input.containsSecret === true) {
    errors.push(`finding ${id} contains secret material`);
  }
}

function findingLeakageRiskDetail(finding: OperationalMemoryFinding): string | undefined {
  const record = finding as unknown as Readonly<Record<string, unknown>>;
  const details = [
    leakageFlagRiskDetail(record, "containsTaskAnswer"),
    leakageFlagRiskDetail(record, "containsSecret"),
  ].filter((detail): detail is string => detail !== undefined);
  return details.length === 0 ? undefined : details.join("; ");
}

function leakageFlagRiskDetail(
  input: Readonly<Record<string, unknown>>,
  field: "containsTaskAnswer" | "containsSecret",
): string | undefined {
  if (!Object.prototype.hasOwnProperty.call(input, field)) return undefined;
  if (typeof input[field] !== "boolean") return `${field} must be boolean when present`;
  return input[field] === true ? `${field}=true` : undefined;
}

function findingStrategyQuarantineDetail(
  finding: OperationalMemoryFinding,
  quarantinedStrategyFingerprints: ReadonlySet<ContentHash>,
): string | undefined {
  const record = finding as unknown as Readonly<Record<string, unknown>>;
  const raw = record.strategyFingerprint;
  if (raw === undefined) return undefined;
  if (typeof raw !== "string") return "strategyFingerprint must be ContentHash when present";
  const fingerprint = parseContentHash(raw);
  if (fingerprint === null) return "strategyFingerprint must be ContentHash when present";
  if (!quarantinedStrategyFingerprints.has(fingerprint)) return undefined;
  return `strategyFingerprint=${fingerprint}`;
}

function requireOptionalBoolean(
  input: Readonly<Record<string, unknown>>,
  field: "containsTaskAnswer" | "containsSecret",
  findingId: string,
  errors: string[],
): void {
  if (Object.prototype.hasOwnProperty.call(input, field) && typeof input[field] !== "boolean") {
    errors.push(`finding ${findingId} ${field} must be a boolean when present`);
  }
}

function requireOptionalContentHash(
  input: Readonly<Record<string, unknown>>,
  field: string,
  findingId: string,
  errors: string[],
): void {
  const value = input[field];
  if (value === undefined) return;
  if (typeof value !== "string" || parseContentHash(value) === null) {
    errors.push(`finding ${findingId} ${field} must be a ContentHash when present`);
  }
}

function requireOptionalString(
  input: Readonly<Record<string, unknown>>,
  field: string,
  parent: string,
  errors: string[],
): void {
  if (Object.prototype.hasOwnProperty.call(input, field) && typeof input[field] !== "string") {
    errors.push(`${parent} ${field} must be a string when present`);
  }
}

function requireString(
  input: Readonly<Record<string, unknown>>,
  field: string,
  errors: string[],
): void {
  if (typeof input[field] !== "string" || input[field].length === 0) {
    errors.push(`${field} must be a non-empty string`);
  }
}

function requireStringArray(
  input: Readonly<Record<string, unknown>>,
  field: string,
  errors: string[],
): void {
  const value = input[field];
  if (!Array.isArray(value) || !value.every((item) => typeof item === "string" && item.length > 0)) {
    errors.push(`${field} must be an array of non-empty strings`);
  }
}

function requireEnum(
  input: Readonly<Record<string, unknown>>,
  field: string,
  values: readonly string[],
  errors: string[],
): void {
  if (typeof input[field] !== "string" || !values.includes(input[field])) {
    errors.push(`${field} must be one of ${values.join(", ")}`);
  }
}

function isRecord(input: unknown): input is Readonly<Record<string, unknown>> {
  return typeof input === "object" && input !== null && !Array.isArray(input);
}

function normalizedMaxFindings(maxFindings: number | undefined): number {
  if (maxFindings === undefined) return 4;
  if (!Number.isFinite(maxFindings)) return 0;
  return Math.max(0, Math.floor(maxFindings));
}

function isEligiblePackStatus(status: OperationalMemoryPackStatus): boolean {
  return status === "sanitized" || status === "active";
}

function skipPackFindings(
  skippedFindings: OperationalMemorySkippedFinding[],
  pack: OperationalMemoryPack,
  reason: OperationalMemoryContextSkipReason,
  detail: string,
): void {
  for (const finding of pack.findings) {
    skippedFindings.push({
      packId: pack.packId,
      findingId: finding.id,
      reason,
      detail,
    });
  }
}

function compareCandidates(a: CandidateFinding, b: CandidateFinding): number {
  if (a.score !== b.score) return b.score - a.score;
  const riskDelta = riskRank(a.finding.risk) - riskRank(b.finding.risk);
  if (riskDelta !== 0) return riskDelta;
  return a.originalIndex - b.originalIndex;
}

function riskRank(risk: OperationalMemoryRisk): number {
  switch (risk) {
    case "low":
      return 0;
    case "medium":
      return 1;
    case "high":
      return 2;
  }
}

function intersectNormalizedFamilies(
  targetFamilies: readonly string[],
  findingFamilies: readonly string[],
): readonly string[] {
  const targetSet = new Set(targetFamilies);
  return uniqueNormalizedStrings(findingFamilies).filter((family) => targetSet.has(family));
}

function uniqueNormalizedStrings(values: readonly string[]): readonly string[] {
  const seen = new Set<string>();
  const normalized: string[] = [];
  for (const value of values) {
    const item = value.trim().toLowerCase();
    if (item.length === 0 || seen.has(item)) continue;
    seen.add(item);
    normalized.push(item);
  }
  return normalized;
}

function renderOperationalMemoryPrompt(findings: readonly OperationalMemorySelectedFinding[]): string {
  if (findings.length === 0) return "";
  return [
    "Operational memory to apply:",
    ...findings.map(
      (finding, index) => `${index + 1}. ${finding.summary}\n   ${finding.reusableBehavior}`,
    ),
  ].join("\n");
}
