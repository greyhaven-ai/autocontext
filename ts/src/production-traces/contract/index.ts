// Public surface of the autocontext production-traces contract.
// The on-disk format (JSON Schemas + filesystem layout) is the authoritative
// contract for ecosystem consumers — this module is its TypeScript projection.

export type {
  ProductionTraceId,
  AppId,
  UserIdHash,
  SessionIdHash,
  FeedbackRefId,
  EnvironmentTag,
  ContentHash,
  Scenario,
} from "./branded-ids.js";
export {
  newProductionTraceId,
  parseProductionTraceId,
  parseAppId,
  parseUserIdHash,
  parseSessionIdHash,
  parseFeedbackRefId,
  parseEnvironmentTag,
  defaultEnvironmentTag,
  parseContentHash,
  parseScenario,
} from "./branded-ids.js";

export type {
  ProductionTraceSchemaVersion,
  MessageRole,
  TraceMessage,
  ToolCall,
  TraceSource,
  ProviderName,
  ProviderInfo,
  SessionIdentifier,
  EnvContext,
  TimingInfo,
  UsageInfo,
  OutcomeLabel,
  ProductionOutcome,
  FeedbackKind,
  FeedbackRef,
  TraceLinks,
  RedactionReason,
  DetectedBy,
  RedactionMarker,
  ProductionTrace,
  ValidationResult,
} from "./types.js";
export { PRODUCTION_TRACE_SCHEMA_VERSION } from "./types.js";

export {
  validateProductionTrace,
  validateTraceSource,
  validateSession,
  validateEnvContext,
  validateTimingInfo,
  validateUsageInfo,
  validateProductionOutcome,
  validateFeedbackRef,
  validateTraceLinks,
  validateRedactionMarker,
  validateRedactionPolicy,
  validateRetentionPolicy,
} from "./validators.js";

export { createProductionTrace } from "./factories.js";
export type { CreateProductionTraceInputs } from "./factories.js";

export {
  validateTimingSanity,
  validateJsonPointer,
  validateRedactionPaths,
} from "./invariants.js";

export { deriveDatasetId } from "./content-address.js";
