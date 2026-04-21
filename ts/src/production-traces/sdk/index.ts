/**
 * ``autoctx/production-traces`` — customer-facing emit SDK.
 *
 * This is the lean, tree-shakable, enterprise-disciplined subpath entry.
 * See ``STABILITY.md`` and ``BUDGET.md`` in this directory for stability
 * commitments and bundle-size budget, respectively.
 *
 * DDD anchor: every exported name mirrors Foundation A Layer 6's Python SDK
 * vocabulary (``build_trace`` → ``buildTrace``, etc.). camelCase only
 * translates the naming convention; semantics match Python exactly.
 *
 * Zero telemetry. Traces go where you put them.
 */

// ---- Core emit surface ----
export { buildTrace } from "./build-trace.js";
export type { BuildTraceInputs } from "./build-trace.js";

export { writeJsonl } from "./write-jsonl.js";
export type { WriteJsonlOpts } from "./write-jsonl.js";

export { TraceBatch } from "./trace-batch.js";

// ---- Hashing ----
export {
  hashUserId,
  hashSessionId,
  loadInstallSalt,
  initializeInstallSalt,
  rotateInstallSalt,
  installSaltPath,
} from "./hashing.js";

// ---- Validation ----
export {
  validateProductionTrace,
  validateProductionTraceDict,
  ValidationError,
} from "./validate.js";
export type { ValidateResult } from "./validate.js";

// ---- Re-exported contract types (zero duplication) ----
export type {
  ProductionTrace,
  ProductionTraceSchemaVersion,
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
  ProductionTraceRouting,
  ModelRoutingDecisionReason,
  ModelRoutingFallbackReason,
  MessageRole,
  TraceMessage,
  ToolCall,
} from "../contract/types.js";
export { PRODUCTION_TRACE_SCHEMA_VERSION } from "../contract/types.js";

export type {
  ProductionTraceId,
  AppId,
  UserIdHash,
  SessionIdHash,
  FeedbackRefId,
  EnvironmentTag,
  Scenario,
} from "../contract/branded-ids.js";
export {
  newProductionTraceId,
  parseProductionTraceId,
  parseAppId,
  parseUserIdHash,
  parseSessionIdHash,
  parseFeedbackRefId,
  parseEnvironmentTag,
  defaultEnvironmentTag,
  parseScenario,
} from "../contract/branded-ids.js";
