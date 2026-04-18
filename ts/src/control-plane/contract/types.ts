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
  | "fine-tuned-model";

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

// ---- EvalRun ----

export type EvalRunRef = {
  readonly evalRunId: string;
  readonly suiteId: SuiteId;
  readonly ingestedAt: string;
};

export type EvalRun = {
  readonly schemaVersion: SchemaVersion;
  readonly runId: string;
  readonly artifactId: ArtifactId;
  readonly suiteId: SuiteId;
  readonly metrics: MetricBundle;
  readonly datasetProvenance: {
    readonly datasetId: string;
    readonly sliceHash: ContentHash;
    readonly sampleCount: number;
  };
  readonly ingestedAt: string;
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
