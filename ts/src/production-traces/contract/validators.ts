import Ajv2020 from "ajv/dist/2020.js";
import type { ErrorObject, ValidateFunction } from "ajv";
import addFormats from "ajv-formats";
import sharedDefsSchema from "./json-schemas/shared-defs.schema.json" with { type: "json" };
import traceSourceSchema from "./json-schemas/trace-source.schema.json" with { type: "json" };
import sessionSchema from "./json-schemas/session.schema.json" with { type: "json" };
import envContextSchema from "./json-schemas/env-context.schema.json" with { type: "json" };
import timingInfoSchema from "./json-schemas/timing-info.schema.json" with { type: "json" };
import usageInfoSchema from "./json-schemas/usage-info.schema.json" with { type: "json" };
import productionOutcomeSchema from "./json-schemas/production-outcome.schema.json" with { type: "json" };
import feedbackRefSchema from "./json-schemas/feedback-ref.schema.json" with { type: "json" };
import traceLinksSchema from "./json-schemas/trace-links.schema.json" with { type: "json" };
import redactionMarkerSchema from "./json-schemas/redaction-marker.schema.json" with { type: "json" };
import redactionPolicySchema from "./json-schemas/redaction-policy.schema.json" with { type: "json" };
import retentionPolicySchema from "./json-schemas/retention-policy.schema.json" with { type: "json" };
import productionTraceSchema from "./json-schemas/production-trace.schema.json" with { type: "json" };
import selectionRuleSchema from "./json-schemas/selection-rule.schema.json" with { type: "json" };
import clusterConfigSchema from "./json-schemas/cluster-config.schema.json" with { type: "json" };
import rubricConfigSchema from "./json-schemas/rubric-config.schema.json" with { type: "json" };
import datasetRowSchema from "./json-schemas/dataset-row.schema.json" with { type: "json" };
import datasetManifestSchema from "./json-schemas/dataset-manifest.schema.json" with { type: "json" };
import type {
  ProductionTrace,
  TraceSource,
  SessionIdentifier,
  EnvContext,
  TimingInfo,
  UsageInfo,
  ProductionOutcome,
  FeedbackRef,
  TraceLinks,
  RedactionMarker,
  ValidationResult,
} from "./types.js";

// Default-interop for CJS-shipped AJV from an ESM module.
const AjvCtor = (Ajv2020 as unknown as { default: typeof Ajv2020 }).default ?? Ajv2020;
const addFormatsFn = (addFormats as unknown as { default: typeof addFormats }).default ?? addFormats;

const ajv = new AjvCtor({ strict: true, allErrors: true });
addFormatsFn(ajv);

// Register all schemas once at module init so $refs resolve.
ajv.addSchema(sharedDefsSchema as object);
ajv.addSchema(traceSourceSchema as object);
ajv.addSchema(sessionSchema as object);
ajv.addSchema(envContextSchema as object);
ajv.addSchema(timingInfoSchema as object);
ajv.addSchema(usageInfoSchema as object);
ajv.addSchema(productionOutcomeSchema as object);
ajv.addSchema(feedbackRefSchema as object);
ajv.addSchema(traceLinksSchema as object);
ajv.addSchema(redactionMarkerSchema as object);
ajv.addSchema(redactionPolicySchema as object);
ajv.addSchema(retentionPolicySchema as object);
ajv.addSchema(productionTraceSchema as object);
ajv.addSchema(selectionRuleSchema as object);
ajv.addSchema(clusterConfigSchema as object);
ajv.addSchema(rubricConfigSchema as object);
ajv.addSchema(datasetRowSchema as object);
ajv.addSchema(datasetManifestSchema as object);

const traceSourceValidator       = ajv.getSchema("https://autocontext.dev/schema/production-traces/trace-source.json")!;
const sessionValidator           = ajv.getSchema("https://autocontext.dev/schema/production-traces/session.json")!;
const envContextValidator        = ajv.getSchema("https://autocontext.dev/schema/production-traces/env-context.json")!;
const timingInfoValidator        = ajv.getSchema("https://autocontext.dev/schema/production-traces/timing-info.json")!;
const usageInfoValidator         = ajv.getSchema("https://autocontext.dev/schema/production-traces/usage-info.json")!;
const productionOutcomeValidator = ajv.getSchema("https://autocontext.dev/schema/production-traces/production-outcome.json")!;
const feedbackRefValidator       = ajv.getSchema("https://autocontext.dev/schema/production-traces/feedback-ref.json")!;
const traceLinksValidator        = ajv.getSchema("https://autocontext.dev/schema/production-traces/trace-links.json")!;
const redactionMarkerValidator   = ajv.getSchema("https://autocontext.dev/schema/production-traces/redaction-marker.json")!;
const redactionPolicyValidator   = ajv.getSchema("https://autocontext.dev/schema/production-traces/redaction-policy.json")!;
const retentionPolicyValidator   = ajv.getSchema("https://autocontext.dev/schema/production-traces/retention-policy.json")!;
const productionTraceValidator   = ajv.getSchema("https://autocontext.dev/schema/production-traces/production-trace.json")!;
const selectionRuleValidator     = ajv.getSchema("https://autocontext.dev/schema/production-traces/selection-rule.json")!;
const clusterConfigValidator     = ajv.getSchema("https://autocontext.dev/schema/production-traces/cluster-config.json")!;
const rubricConfigValidator      = ajv.getSchema("https://autocontext.dev/schema/production-traces/rubric-config.json")!;
const datasetRowValidator        = ajv.getSchema("https://autocontext.dev/schema/production-traces/dataset-row.json")!;
const datasetManifestValidator   = ajv.getSchema("https://autocontext.dev/schema/production-traces/dataset-manifest.json")!;

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

export function validateTraceSource(input: unknown): ValidationResult {
  return toResult(traceSourceValidator, input);
}
export function validateSession(input: unknown): ValidationResult {
  return toResult(sessionValidator, input);
}
export function validateEnvContext(input: unknown): ValidationResult {
  return toResult(envContextValidator, input);
}
export function validateTimingInfo(input: unknown): ValidationResult {
  return toResult(timingInfoValidator, input);
}
export function validateUsageInfo(input: unknown): ValidationResult {
  return toResult(usageInfoValidator, input);
}
export function validateProductionOutcome(input: unknown): ValidationResult {
  return toResult(productionOutcomeValidator, input);
}
export function validateFeedbackRef(input: unknown): ValidationResult {
  return toResult(feedbackRefValidator, input);
}
export function validateTraceLinks(input: unknown): ValidationResult {
  return toResult(traceLinksValidator, input);
}
export function validateRedactionMarker(input: unknown): ValidationResult {
  return toResult(redactionMarkerValidator, input);
}
export function validateRedactionPolicy(input: unknown): ValidationResult {
  return toResult(redactionPolicyValidator, input);
}
export function validateRetentionPolicy(input: unknown): ValidationResult {
  return toResult(retentionPolicyValidator, input);
}
export function validateProductionTrace(input: unknown): ValidationResult {
  return toResult(productionTraceValidator, input);
}
export function validateSelectionRule(input: unknown): ValidationResult {
  return toResult(selectionRuleValidator, input);
}
export function validateClusterConfig(input: unknown): ValidationResult {
  return toResult(clusterConfigValidator, input);
}
export function validateRubricConfig(input: unknown): ValidationResult {
  return toResult(rubricConfigValidator, input);
}
export function validateDatasetRow(input: unknown): ValidationResult {
  return toResult(datasetRowValidator, input);
}
export function validateDatasetManifest(input: unknown): ValidationResult {
  return toResult(datasetManifestValidator, input);
}

// Type-level assertions — if TS types drift from schemas these won't compile.
export type _TypeCheck =
  | ProductionTrace
  | TraceSource
  | SessionIdentifier
  | EnvContext
  | TimingInfo
  | UsageInfo
  | ProductionOutcome
  | FeedbackRef
  | TraceLinks
  | RedactionMarker;
