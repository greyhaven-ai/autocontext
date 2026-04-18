import Ajv2020 from "ajv/dist/2020.js";
import type { ErrorObject, ValidateFunction } from "ajv";
import addFormats from "ajv-formats";
import sharedDefsSchema from "./json-schemas/shared-defs.schema.json" with { type: "json" };
import metricBundleSchema from "./json-schemas/metric-bundle.schema.json" with { type: "json" };
import provenanceSchema from "./json-schemas/provenance.schema.json" with { type: "json" };
import evalRunSchema from "./json-schemas/eval-run.schema.json" with { type: "json" };
import promotionEventSchema from "./json-schemas/promotion-event.schema.json" with { type: "json" };
import artifactSchema from "./json-schemas/artifact.schema.json" with { type: "json" };
import promotionDecisionSchema from "./json-schemas/promotion-decision.schema.json" with { type: "json" };
import patchSchema from "./json-schemas/patch.schema.json" with { type: "json" };
import type {
  MetricBundle,
  Provenance,
  EvalRun,
  PromotionEvent,
  Artifact,
  PromotionDecision,
  Patch,
  ValidationResult,
} from "./types.js";

// AJV setup — register shared defs + all document schemas once; reuse compiled validators.
// ajv and ajv-formats are CJS; ESM default-interop exposes the class/function via .default.
// Accessing via a cast keeps strict typing while resolving the runtime shape.
const AjvCtor = (Ajv2020 as unknown as { default: typeof Ajv2020 }).default ?? Ajv2020;
const addFormatsFn = (addFormats as unknown as { default: typeof addFormats }).default ?? addFormats;

const ajv = new AjvCtor({ strict: true, allErrors: true });
addFormatsFn(ajv);

// addSchema for shared defs so $refs resolve; the $id determines the lookup key.
ajv.addSchema(sharedDefsSchema as object);
ajv.addSchema(metricBundleSchema as object);
ajv.addSchema(provenanceSchema as object);
ajv.addSchema(evalRunSchema as object);
ajv.addSchema(promotionEventSchema as object);
ajv.addSchema(artifactSchema as object);
ajv.addSchema(promotionDecisionSchema as object);
ajv.addSchema(patchSchema as object);

const metricBundleValidator      = ajv.getSchema("https://autocontext.dev/schema/metric-bundle.json")!;
const provenanceValidator        = ajv.getSchema("https://autocontext.dev/schema/provenance.json")!;
const evalRunValidator           = ajv.getSchema("https://autocontext.dev/schema/eval-run.json")!;
const promotionEventValidator    = ajv.getSchema("https://autocontext.dev/schema/promotion-event.json")!;
const artifactValidator          = ajv.getSchema("https://autocontext.dev/schema/artifact.json")!;
const promotionDecisionValidator = ajv.getSchema("https://autocontext.dev/schema/promotion-decision.json")!;
const patchValidator             = ajv.getSchema("https://autocontext.dev/schema/patch.json")!;

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

export function validateMetricBundle(input: unknown): ValidationResult {
  return toResult(metricBundleValidator, input);
}
export function validateProvenance(input: unknown): ValidationResult {
  return toResult(provenanceValidator, input);
}
export function validateEvalRun(input: unknown): ValidationResult {
  return toResult(evalRunValidator, input);
}
export function validatePromotionEvent(input: unknown): ValidationResult {
  return toResult(promotionEventValidator, input);
}
export function validateArtifact(input: unknown): ValidationResult {
  return toResult(artifactValidator, input);
}
export function validatePromotionDecision(input: unknown): ValidationResult {
  return toResult(promotionDecisionValidator, input);
}
export function validatePatch(input: unknown): ValidationResult {
  return toResult(patchValidator, input);
}

// Type-level assertions — if types drift from schemas, this won't compile.
export type _TypeCheck = MetricBundle | Provenance | EvalRun | PromotionEvent | Artifact | PromotionDecision | Patch;
