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
  EvalTrialStatus,
  EvalTrial,
  EvalReconciliationView,
  EvalReconciliationCounts,
  EvalRunReconciliation,
  RunTrack,
  StrategyComponentFingerprint,
  StrategyLineage,
  StrategyDuplicateAssessment,
  StrategyIdentity,
  StrategyQuarantineReason,
  StrategyQuarantine,
  WebPolicy,
  IntegrityMode,
  AdapterProvenance,
  EvalRunIntegrity,
  AblationTarget,
  AblationVerificationStatus,
  AblationVerification,
  AblationRequirement,
  AblationVerificationAssessment,
  MemoryPackRef,
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

export {
  RUN_TRACKS,
  isRunTrack,
  effectiveEvalRunTrack,
  assessEvalRunTrack,
  describeExperimentalEvalRunTrack,
} from "./run-track.js";
export type { EvalRunTrackAssessment } from "./run-track.js";

export {
  ABLATION_TARGETS,
  DEFAULT_ABLATION_REQUIREMENT,
  isAblationTarget,
  normalizeAblationRequirement,
  assessAblationVerification,
  describeAblationVerificationIssue,
} from "./ablation-verification.js";

export {
  buildStrategyIdentity,
  buildStrategyComponentsFromTree,
  detectStrategyDuplicate,
  strategyFingerprintForArtifact,
} from "./strategy-identity.js";
export type { BuildStrategyIdentityInputs } from "./strategy-identity.js";

export {
  assessStrategyQuarantine,
  describeStrategyQuarantine,
} from "./strategy-quarantine.js";

export {
  createArtifact,
  createPromotionEvent,
  createEvalRun,
} from "./factories.js";
export type {
  CreateArtifactInputs,
  CreatePromotionEventInputs,
  CreateEvalRunInputs,
} from "./factories.js";

export {
  validateLineageNoCycles,
  validateAppendOnly,
  computeTreeHash,
} from "./invariants.js";
export type { TreeFile } from "./invariants.js";
