// Public surface of the autocontext control-plane contract.
// The on-disk format (JSON Schemas + filesystem layout) is the authoritative contract
// for ecosystem consumers; this module is the TypeScript projection of that contract.

export type {
  ArtifactId,
  ChangeSetId,
  Scenario,
  EnvironmentTag,
  SuiteId,
  ContentHash,
} from "./branded-ids.js";
export {
  newArtifactId,
  parseArtifactId,
  newChangeSetId,
  parseChangeSetId,
  parseScenario,
  parseEnvironmentTag,
  defaultEnvironmentTag,
  parseSuiteId,
  parseContentHash,
} from "./branded-ids.js";

export type { SchemaVersion } from "./schema-version.js";
export {
  CURRENT_SCHEMA_VERSION,
  parseSchemaVersion,
  compareSchemaVersions,
  isReadCompatible,
  canWriteVersion,
} from "./schema-version.js";

export { canonicalJsonStringify } from "./canonical-json.js";
export type { JsonValue } from "./canonical-json.js";

export type {
  ActuatorType,
  ActivationState,
  RollbackStrategy,
  CostMetric,
  LatencyMetric,
  SafetyRegression,
  MetricBundle,
  Provenance,
  EvalRunRef,
  EvalRun,
  PromotionEvent,
  Artifact,
  PromotionThresholds,
  PromotionDecision,
  Patch,
  ValidationResult,
} from "./types.js";

export {
  validateMetricBundle,
  validateProvenance,
  validateEvalRun,
  validatePromotionEvent,
  validateArtifact,
  validatePromotionDecision,
  validatePatch,
} from "./validators.js";
