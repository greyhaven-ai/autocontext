import Ajv2020 from "ajv/dist/2020.js";
import type { ErrorObject, ValidateFunction } from "ajv";
import addFormats from "ajv-formats";
import candidateEvidenceSchema from "./json-schemas/candidate-evidence.schema.json" with { type: "json" };
import promotionScoreSchema from "./json-schemas/promotion-score.schema.json" with { type: "json" };
import type { CandidateEvidence, PromotionScore } from "./generated-types.js";

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

const candidateEvidenceValidator = ajv.getSchema(
  "https://autocontext.dev/schema/harness-optimization/candidate-evidence.json",
)!;

const promotionScoreValidator = ajv.getSchema(
  "https://autocontext.dev/schema/harness-optimization/promotion-score.json",
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

// Type-level assertion — if a TS type drifts from its schema this won't compile.
export type _TypeCheck = CandidateEvidence | PromotionScore;
