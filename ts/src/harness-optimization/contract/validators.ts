import Ajv2020 from "ajv/dist/2020.js";
import type { ErrorObject, ValidateFunction } from "ajv";
import addFormats from "ajv-formats";
import candidateEvidenceSchema from "./json-schemas/candidate-evidence.schema.json" with { type: "json" };
import promotionScoreSchema from "./json-schemas/promotion-score.schema.json" with { type: "json" };
import repairResultSchema from "./json-schemas/repair-result.schema.json" with { type: "json" };
import integrityMetadataSchema from "./json-schemas/integrity-metadata.schema.json" with { type: "json" };
import frontierMechanismSchema from "./json-schemas/frontier-mechanism.schema.json" with { type: "json" };
import orphanMechanismSchema from "./json-schemas/orphan-mechanism.schema.json" with { type: "json" };
import type {
  CandidateEvidence,
  FrontierMechanism,
  IntegrityMetadata,
  OrphanMechanism,
  PromotionScore,
  RepairResult,
} from "./generated-types.js";

// Shared validation-result shape (matches the production-traces contract).
export type ValidationResult =
  { readonly valid: true } | { readonly valid: false; readonly errors: readonly string[] };

// Default-interop for CJS-shipped AJV from an ESM module.
const AjvCtor = (Ajv2020 as unknown as { default: typeof Ajv2020 }).default ?? Ajv2020;
const addFormatsFn =
  (addFormats as unknown as { default: typeof addFormats }).default ?? addFormats;

const ajv = new AjvCtor({ strict: true, allErrors: true });
addFormatsFn(ajv);

// Register the schemas once at module init so $refs resolve.
ajv.addSchema(candidateEvidenceSchema as object);
ajv.addSchema(promotionScoreSchema as object);
ajv.addSchema(repairResultSchema as object);
ajv.addSchema(integrityMetadataSchema as object);
ajv.addSchema(frontierMechanismSchema as object);
ajv.addSchema(orphanMechanismSchema as object);

const candidateEvidenceValidator = ajv.getSchema(
  "https://autocontext.dev/schema/harness-optimization/candidate-evidence.json",
)!;

const promotionScoreValidator = ajv.getSchema(
  "https://autocontext.dev/schema/harness-optimization/promotion-score.json",
)!;

const repairResultValidator = ajv.getSchema(
  "https://autocontext.dev/schema/harness-optimization/repair-result.json",
)!;

const integrityMetadataValidator = ajv.getSchema(
  "https://autocontext.dev/schema/harness-optimization/integrity-metadata.json",
)!;

const frontierMechanismValidator = ajv.getSchema(
  "https://autocontext.dev/schema/harness-optimization/frontier-mechanism.json",
)!;

const orphanMechanismValidator = ajv.getSchema(
  "https://autocontext.dev/schema/harness-optimization/orphan-mechanism.json",
)!;

function toResult(validate: ValidateFunction, input: unknown): ValidationResult {
  const ok = validate(input);
  if (ok) return { valid: true };
  const errors = (validate.errors ?? []).map(formatError);
  return { valid: false, errors };
}

function formatError(e: ErrorObject): string {
  const path = e.instancePath || "<root>";
  return `${path} ${e.message ?? "invalid"}`.trim();
}

export function validateCandidateEvidence(input: unknown): ValidationResult {
  return toResult(candidateEvidenceValidator, input);
}

export function validatePromotionScore(input: unknown): ValidationResult {
  return toResult(promotionScoreValidator, input);
}

export function validateRepairResult(input: unknown): ValidationResult {
  return toResult(repairResultValidator, input);
}

export function validateIntegrityMetadata(input: unknown): ValidationResult {
  return toResult(integrityMetadataValidator, input);
}

export function validateFrontierMechanism(input: unknown): ValidationResult {
  return toResult(frontierMechanismValidator, input);
}

export function validateOrphanMechanism(input: unknown): ValidationResult {
  return toResult(orphanMechanismValidator, input);
}

// Type-level assertion — if a TS type drifts from its schema this won't compile.
export type _TypeCheck =
  | CandidateEvidence
  | PromotionScore
  | RepairResult
  | IntegrityMetadata
  | FrontierMechanism
  | OrphanMechanism;
