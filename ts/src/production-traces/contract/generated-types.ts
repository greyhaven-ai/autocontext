/* eslint-disable */
// AUTO-GENERATED from src/production-traces/contract/json-schemas/ — DO NOT EDIT.
// Regenerate with: node scripts/generate-production-traces-types.mjs
// CI gate: node scripts/generate-production-traces-types.mjs --check

// ---- cluster-config.schema.json ----
/**
 * Rule-based clustering config (Tier 2 per spec §8.1). First-matching rule wins; a catch-all with `default: true` is required.
 */
export interface ClusterConfig {
  strategy: "rules";
  /**
   * @minItems 1
   */
  rules: [
    {
      id: string;
      match: {
        [k: string]: {
          equals?: unknown;
          contains?: string | string[];
          default?: true;
        };
      };
    },
    ...{
      id: string;
      match: {
        [k: string]: {
          equals?: unknown;
          contains?: string | string[];
          default?: true;
        };
      };
    }[]
  ];
}

// ---- dataset-manifest.schema.json ----
/**
 * A single selection rule in the dataset-generation pipeline (per spec §8.2). Rules are applied in order; each rule transforms the trace set forward.
 */
export type SelectionRule = GateRule | TopQuartileRule | ContrastiveRule | SplitRule;

/**
 * Top-level manifest for a generated dataset (per spec §8.4). Lives at .autocontext/datasets/<datasetId>/manifest.json.
 */
export interface DatasetManifest {
  schemaVersion: "1.0";
  datasetId: string;
  name: string;
  description: string;
  createdAt: string;
  autoctxVersion: string;
  source: {
    traceCount: number;
    timeRange: {
      from: string;
      to: string;
    };
    clusterStrategy: "taskType" | "rules";
    filterRules: SelectionRule[];
    redactionPolicy: {
      mode: "on-export" | "on-ingest";
      snapshotHash: string;
    };
  };
  splits: {
    train: SplitStats;
    eval: SplitStats;
    holdout: SplitStats;
  };
  clusters: {
    clusterId: string;
    size: number;
    rubricId?: string;
    rubricSource?: "explicit" | "registry" | "synthetic";
    skippedReason?: string;
  }[];
  provenance: {
    configHash: string;
    inputTracesHash: string;
  };
}
export interface GateRule {
  type: "gate";
  include?: MatchExpression[];
  exclude?: MatchExpression[];
}
export interface MatchExpression {
  [k: string]: {
    equals?: unknown;
    contains?: string | string[];
    default?: true;
  };
}
export interface TopQuartileRule {
  type: "top-quartile";
  by: string;
  percentile: number;
  perCluster?: boolean;
}
export interface ContrastiveRule {
  type: "contrastive";
  failureCriterion: MatchExpression;
  successCriterion: MatchExpression;
  pairStrategy?: "same-cluster";
  maxPairsPerCluster?: number;
}
export interface SplitRule {
  type: "split";
  train: number;
  eval: number;
  holdout: number;
  shuffle?: boolean;
  seed?: number;
}
export interface SplitStats {
  rowCount: number;
  fileHash: string;
}

// ---- dataset-row.schema.json ----
/**
 * A single row in a generated dataset (per spec §8.4). Emitted one-per-JSONL-line under .autocontext/datasets/<id>/<split>.jsonl.
 */
export interface DatasetRow {
  schemaVersion: "1.0";
  rowId: string;
  split: "train" | "eval" | "holdout";
  clusterId: string;
  source: {
    /**
     * @minItems 1
     */
    traceIds: [string, ...string[]];
    timeRange: {
      from: string;
      to: string;
    };
    redactionApplied: boolean;
  };
  inputs: {
    messages: TraceMessage[];
    toolsAvailable: string[];
  };
  expectedOutcome?: {
    label: "success" | "failure" | "partial";
    score?: number;
    reasoning?: string;
  };
  rubric?: {
    rubricId: string;
    dimensions: string[];
    source: "explicit" | "registry" | "synthetic";
  };
  metadata: {};
}
export interface TraceMessage {
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  timestamp: string;
  toolCalls?: ToolCall[];
  metadata?: {};
}
export interface ToolCall {
  toolName: string;
  args: {};
  result?: unknown;
  durationMs?: number;
  error?: string;
}

// ---- env-context.schema.json ----
export interface EnvContext {
  environmentTag: string;
  appId: string;
  taskType?: string;
  deploymentMeta?: {};
}

// ---- feedback-ref.schema.json ----
export interface FeedbackRef {
  kind: "thumbs" | "rating" | "correction" | "edit" | "custom";
  submittedAt: string;
  ref: string;
  score?: number;
  comment?: string;
}

// ---- production-outcome.schema.json ----
export interface ProductionOutcome {
  label?: "success" | "failure" | "partial" | "unknown";
  score?: number;
  reasoning?: string;
  signals?: {
    [k: string]: number;
  };
  error?: {
    type: string;
    message: string;
    stack?: string;
  };
}

// ---- production-trace.schema.json ----
export interface ProductionTrace {
  schemaVersion: "1.0";
  traceId: string;
  source: TraceSource;
  provider: {
    name: "openai" | "anthropic" | "openai-compatible" | "langchain" | "vercel-ai-sdk" | "litellm" | "other";
    endpoint?: string;
    providerVersion?: string;
  };
  model: string;
  session?: SessionIdentifier;
  env: EnvContext;
  /**
   * @minItems 1
   */
  messages: [TraceMessage, ...TraceMessage[]];
  toolCalls: ToolCall[];
  outcome?: ProductionOutcome;
  timing: TimingInfo;
  usage: UsageInfo;
  feedbackRefs: FeedbackRef[];
  links: TraceLinks;
  redactions: RedactionMarker[];
  routing?: {
    chosen: {
      provider: string;
      model: string;
      endpoint?: string;
    };
    matchedRouteId?: string;
    reason: "default" | "matched-route" | "fallback";
    fallbackReason?: "budget-exceeded" | "latency-breached" | "provider-error" | "no-match";
    evaluatedAt: string;
  };
  metadata?: {};
}
export interface TraceSource {
  emitter: string;
  sdk: {
    name: string;
    version: string;
  };
  hostname?: string;
}
export interface SessionIdentifier {
  userIdHash?: string;
  sessionIdHash?: string;
  requestId?: string;
}
export interface TimingInfo {
  startedAt: string;
  endedAt: string;
  latencyMs: number;
  timeToFirstTokenMs?: number;
}
export interface UsageInfo {
  tokensIn: number;
  tokensOut: number;
  estimatedCostUsd?: number;
  providerUsage?: {};
}
export interface TraceLinks {
  scenarioId?: string;
  runId?: string;
  evalExampleIds?: string[];
  trainingRecordIds?: string[];
}
export interface RedactionMarker {
  path: string;
  reason: "pii-email" | "pii-name" | "pii-ssn" | "secret-token" | "pii-custom";
  category?: string;
  detectedBy: "client" | "ingestion" | "operator";
  detectedAt: string;
}

// ---- redaction-marker.schema.json ----

// ---- redaction-policy.schema.json ----
/**
 * Per-installation redaction policy config. Lives at .autocontext/production-traces/redaction-policy.json.
 */
export interface RedactionPolicy {
  schemaVersion: "1.0";
  mode: "on-export" | "on-ingest";
  autoDetect: {
    enabled: boolean;
    categories: string[];
  };
  customPatterns: {
    name: string;
    regex: string;
    category: string;
    reason: "pii-email" | "pii-name" | "pii-ssn" | "secret-token" | "pii-custom";
  }[];
  rawProviderPayload: {
    behavior: "blanket-mark";
  };
  exportPolicy: {
    placeholder: string;
    preserveLength: boolean;
    includeRawProviderPayload: boolean;
    includeMetadata: boolean;
    categoryOverrides: {
      [k: string]: {
        action: "redact" | "hash" | "preserve" | "drop";
        placeholder?: string;
        hashSalt?: string;
      };
    };
  };
}

// ---- retention-policy.schema.json ----
/**
 * Per-installation retention policy config. Lives at .autocontext/production-traces/retention-policy.json. See spec §6.6.
 */
export interface RetentionPolicy {
  schemaVersion: "1.0";
  /**
   * Traces whose endedAt is older than this many days are eligible for deletion.
   */
  retentionDays: number;
  /**
   * Compliance-bound escape hatch: when true, no traces are deleted regardless of other settings.
   */
  preserveAll: boolean;
  /**
   * Traces whose outcome.label matches any value in this list are retained regardless of age.
   */
  preserveCategories: string[];
  /**
   * Maximum number of traces to evaluate-and-delete per enforcement run; bounds latency for large backlogs.
   */
  gcBatchSize: number;
}

// ---- rubric-config.schema.json ----
/**
 * Explicit per-cluster rubric mapping (spec §8.3 source #1). Consumed by build-dataset as the highest-precedence rubric source.
 */
export interface RubricConfig {
  rubricsByCluster: {
    [k: string]:
      | {
          source: "file";
          path: string;
        }
      | {
          source: "inline";
          rubric: Rubric;
        };
  };
}
export interface Rubric {
  rubricId: string;
  /**
   * @minItems 1
   */
  dimensions: [string, ...string[]];
  description?: string;
}

// ---- selection-rule.schema.json ----
/**
 * A single selection rule in the dataset-generation pipeline (per spec §8.2). Rules are applied in order; each rule transforms the trace set forward.
 */


// ---- session.schema.json ----

// ---- shared-defs.schema.json ----
export interface SharedDefinitionsForAutocontextProductionTraceDocuments {
  [k: string]: unknown;
}

// ---- timing-info.schema.json ----

// ---- trace-links.schema.json ----

// ---- trace-source.schema.json ----

// ---- usage-info.schema.json ----
