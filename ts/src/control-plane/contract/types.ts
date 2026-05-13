import type {
  ArtifactId,
  ChangeSetId,
  Scenario,
  EnvironmentTag,
  SuiteId,
  ContentHash,
} from "./branded-ids.js";
import type { SchemaVersion } from "./schema-version.js";

export type ActuatorType =
  | "prompt-patch"
  | "tool-policy"
  | "routing-rule"
  | "fine-tuned-model"
  | "model-routing";

export type ActivationState =
  | "candidate"
  | "shadow"
  | "canary"
  | "active"
  | "disabled"
  | "deprecated";

export type RollbackStrategy =
  | { readonly kind: "content-revert" }
  | { readonly kind: "pointer-flip" }
  | { readonly kind: "cascade-set"; readonly dependsOn: readonly ActuatorType[] };

// ---- MetricBundle and sub-shapes ----

export type CostMetric = {
  readonly tokensIn: number;
  readonly tokensOut: number;
  readonly usd?: number;
};

export type LatencyMetric = {
  readonly p50Ms: number;
  readonly p95Ms: number;
  readonly p99Ms: number;
};

export type SafetyRegression = {
  readonly id: string;
  readonly severity: "info" | "minor" | "major" | "critical";
  readonly description: string;
  readonly exampleRef?: string;
};

export type MetricBundle = {
  readonly quality: { readonly score: number; readonly sampleSize: number };
  readonly cost: CostMetric;
  readonly latency: LatencyMetric;
  readonly safety: { readonly regressions: readonly SafetyRegression[] };
  readonly humanFeedback?: {
    readonly positive: number;
    readonly negative: number;
    readonly neutral: number;
  };
  readonly evalRunnerIdentity: {
    readonly name: string;
    readonly version: string;
    readonly configHash: ContentHash;
  };
};

// ---- Provenance ----

export type Provenance = {
  readonly authorType: "autocontext-run" | "human" | "external-agent";
  readonly authorId: string;
  readonly agentRole?: string;
  readonly parentArtifactIds: readonly ArtifactId[];
  readonly createdAt: string;
};

// ---- Strategy identity ----

export type StrategyComponentFingerprint = {
  readonly name: string;
  readonly fingerprint: ContentHash;
};

export type StrategyLineage = {
  readonly parentFingerprints: readonly ContentHash[];
};

export type StrategyDuplicateAssessment = {
  readonly kind: "exact" | "near";
  readonly artifactId: ArtifactId;
  readonly fingerprint: ContentHash;
  readonly similarity: number;
};

export type StrategyIdentity = {
  readonly fingerprint: ContentHash;
  readonly payloadHash?: ContentHash;
  readonly components: readonly StrategyComponentFingerprint[];
  readonly lineage: StrategyLineage;
  readonly duplicateOf?: StrategyDuplicateAssessment;
};

export type StrategyQuarantineReason =
  | "repeated-invalid-strategy"
  | "contaminated-finding";

export type StrategyQuarantine = {
  readonly status: "quarantined";
  readonly reason: StrategyQuarantineReason;
  readonly sourceArtifactIds: readonly ArtifactId[];
  readonly sourceFingerprints: readonly ContentHash[];
  readonly detail?: string;
};

// ---- EvalRun ----

export type EvalRunRef = {
  readonly evalRunId: string;
  readonly suiteId: SuiteId;
  readonly ingestedAt: string;
};

export type EvalTrialStatus =
  | "passed"
  | "failed"
  | "infrastructure-error"
  | "cancelled"
  | "discarded";

export type EvalTrial = {
  readonly taskId: string;
  readonly trialId: string;
  readonly attempt: number;
  readonly status: EvalTrialStatus;
  readonly reward?: number;
  readonly errorKind?: string;
  readonly replacementForTrialId?: string;
  readonly startedAt?: string;
  readonly completedAt?: string;
  readonly rawResultPath?: string;
  readonly notes?: readonly string[];
};

export type EvalReconciliationView =
  | "first-completed-per-task"
  | "best-of-k";

export type EvalReconciliationCounts = {
  readonly taskCount: number;
  readonly selectedTaskCount: number;
  readonly passed: number;
  readonly failed: number;
  readonly infrastructureErrors: number;
  readonly cancelled: number;
  readonly discarded: number;
  readonly duplicatesIgnored: number;
};

export type EvalRunReconciliation = {
  readonly view: EvalReconciliationView;
  readonly score: number;
  readonly selectedTrialIdsByTask: Readonly<Record<string, string>>;
  readonly ignoredTrialIds: readonly string[];
  readonly unresolvedTaskIds: readonly string[];
  readonly counts: EvalReconciliationCounts;
};

export type RunTrack =
  | "verified"
  | "experimental";

export type WebPolicy =
  | "disabled"
  | "docs-and-downloads-only"
  | "unrestricted";

export type IntegrityMode =
  | "standard"
  | "external-eval"
  | "customer-run";

export type AdapterProvenance = {
  readonly provider: string;
  readonly model: string;
  readonly reasoningEffort?: string;
  readonly promptTemplatePath?: string;
  readonly promptTemplateHash?: ContentHash;
  readonly webPolicy: WebPolicy;
  readonly integrityMode: IntegrityMode;
  readonly authMode?: string;
};

export type EvalRunIntegrity = {
  readonly status: "clean" | "discarded" | "contaminated";
  readonly discardedReason?: string;
  readonly notes?: readonly string[];
};

export type AblationTarget =
  | "strategy"
  | "harness";

export type AblationVerificationStatus =
  | "passed"
  | "failed"
  | "incomplete";

export type AblationVerification = {
  readonly status: AblationVerificationStatus;
  readonly targets: readonly AblationTarget[];
  readonly verifiedAt: string;
  readonly evidenceRefs: readonly string[];
  readonly notes?: readonly string[];
};

export type AblationRequirement = {
  readonly required: boolean;
  readonly targets: readonly AblationTarget[];
};

export type AblationVerificationAssessment = {
  readonly required: boolean;
  readonly status: "not-required" | "missing" | "incomplete" | "failed" | "passed";
  readonly requiredTargets: readonly AblationTarget[];
  readonly coveredTargets: readonly AblationTarget[];
  readonly missingTargets: readonly AblationTarget[];
  readonly reason?: string;
};

export type MemoryPackRef = {
  readonly packId: string;
  readonly version: string;
  readonly contentHash: ContentHash;
};

export type EvalRun = {
  readonly schemaVersion: SchemaVersion;
  readonly runId: string;
  readonly artifactId: ArtifactId;
  readonly suiteId: SuiteId;
  readonly track?: RunTrack;
  readonly metrics: MetricBundle;
  readonly datasetProvenance: {
    readonly datasetId: string;
    readonly sliceHash: ContentHash;
    readonly sampleCount: number;
  };
  readonly ingestedAt: string;
  readonly adapterProvenance?: AdapterProvenance;
  readonly integrity?: EvalRunIntegrity;
  readonly ablationVerification?: AblationVerification;
  readonly trials?: readonly EvalTrial[];
  readonly reconciliation?: EvalRunReconciliation;
  readonly memoryPacks?: readonly MemoryPackRef[];
};

// ---- PromotionEvent ----

export type PromotionEvent = {
  readonly from: ActivationState;
  readonly to: ActivationState;
  readonly reason: string;
  readonly evidence?: {
    readonly baselineArtifactId?: ArtifactId;
    readonly suiteId?: SuiteId;
    readonly decision?: PromotionDecision;
    readonly resolvedTargetPath?: string;
    readonly layoutConfigHash?: ContentHash;
  };
  readonly timestamp: string;
  readonly signature?: string;
};

// ---- Artifact (aggregate root) ----

export type Artifact = {
  readonly schemaVersion: SchemaVersion;
  readonly id: ArtifactId;
  readonly actuatorType: ActuatorType;
  readonly scenario: Scenario;
  readonly environmentTag: EnvironmentTag;
  readonly changeSetId?: ChangeSetId;          // reserved v1.5; optional in v1
  readonly activationState: ActivationState;
  readonly payloadHash: ContentHash;
  readonly provenance: Provenance;
  readonly strategyIdentity?: StrategyIdentity;
  readonly strategyQuarantine?: StrategyQuarantine;
  readonly promotionHistory: readonly PromotionEvent[];
  readonly evalRuns: readonly EvalRunRef[];
};

// ---- PromotionDecision ----

export type PromotionThresholds = {
  readonly qualityMinDelta: number;
  readonly costMaxRelativeIncrease: number;
  readonly latencyMaxRelativeIncrease: number;
  readonly humanFeedbackMinDelta?: number;
  readonly strongConfidenceMin: number;
  readonly moderateConfidenceMin: number;
  readonly strongQualityMultiplier: number;
};

export type PromotionDecision = {
  readonly schemaVersion: SchemaVersion;
  readonly pass: boolean;
  readonly recommendedTargetState: "shadow" | "canary" | "active" | "disabled";
  readonly deltas: {
    readonly quality: {
      readonly baseline: number;
      readonly candidate: number;
      readonly delta: number;
      readonly passed: boolean;
    };
    readonly cost: {
      readonly baseline: CostMetric;
      readonly candidate: CostMetric;
      readonly delta: CostMetric;
      readonly passed: boolean;
    };
    readonly latency: {
      readonly baseline: LatencyMetric;
      readonly candidate: LatencyMetric;
      readonly delta: LatencyMetric;
      readonly passed: boolean;
    };
    readonly safety: {
      readonly regressions: readonly SafetyRegression[];
      readonly passed: boolean;
    };
    readonly humanFeedback?: {
      readonly delta: number;
      readonly passed: boolean;
    };
  };
  readonly confidence: number;
  readonly thresholds: PromotionThresholds;
  readonly ablationVerification?: AblationVerificationAssessment;
  readonly reasoning: string;
  readonly evaluatedAt: string;
};

// ---- Patch (used by emit/) ----

export type Patch = {
  readonly filePath: string;
  readonly operation: "create" | "modify" | "delete";
  readonly unifiedDiff: string;
  readonly afterContent?: string;
};

// Validation result returned by every validator.
export type ValidationResult =
  | { readonly valid: true }
  | { readonly valid: false; readonly errors: readonly string[] };
