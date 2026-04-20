import type {
  AppId,
  FeedbackRefId,
  ProductionTraceId,
  SessionIdHash,
  UserIdHash,
  EnvironmentTag,
  Scenario,
} from "./branded-ids.js";

// The contract starts at 1.0; any document on disk must carry this literal string.
export type ProductionTraceSchemaVersion = "1.0";
export const PRODUCTION_TRACE_SCHEMA_VERSION: ProductionTraceSchemaVersion = "1.0";

// ---- Shared primitives ----

export type MessageRole = "user" | "assistant" | "system" | "tool";

export type ToolCall = {
  readonly toolName: string;
  readonly args: Record<string, unknown>;
  readonly result?: unknown;
  readonly durationMs?: number;
  readonly error?: string;
};

export type TraceMessage = {
  readonly role: MessageRole;
  readonly content: string;
  readonly timestamp: string;
  readonly toolCalls?: readonly ToolCall[];
  readonly metadata?: Record<string, unknown>;
};

// ---- Sub-aggregates ----

export type TraceSource = {
  readonly emitter: string;
  readonly sdk: { readonly name: string; readonly version: string };
  readonly hostname?: string;
};

export type ProviderName =
  | "openai"
  | "anthropic"
  | "openai-compatible"
  | "langchain"
  | "vercel-ai-sdk"
  | "litellm"
  | "other";

export type ProviderInfo = {
  readonly name: ProviderName;
  readonly endpoint?: string;
  readonly providerVersion?: string;
};

export type SessionIdentifier = {
  readonly userIdHash?: UserIdHash;
  readonly sessionIdHash?: SessionIdHash;
  readonly requestId?: string;
};

export type EnvContext = {
  readonly environmentTag: EnvironmentTag;
  readonly appId: AppId;
  readonly taskType?: string;
  readonly deploymentMeta?: Record<string, unknown>;
};

export type TimingInfo = {
  readonly startedAt: string;
  readonly endedAt: string;
  readonly latencyMs: number;
  readonly timeToFirstTokenMs?: number;
};

export type UsageInfo = {
  readonly tokensIn: number;
  readonly tokensOut: number;
  readonly estimatedCostUsd?: number;
  readonly providerUsage?: Record<string, unknown>;
};

export type OutcomeLabel = "success" | "failure" | "partial" | "unknown";

export type ProductionOutcome = {
  readonly label?: OutcomeLabel;
  readonly score?: number;
  readonly reasoning?: string;
  readonly signals?: Record<string, number>;
  readonly error?: {
    readonly type: string;
    readonly message: string;
    readonly stack?: string;
  };
};

export type FeedbackKind = "thumbs" | "rating" | "correction" | "edit" | "custom";

export type FeedbackRef = {
  readonly kind: FeedbackKind;
  readonly submittedAt: string;
  readonly ref: FeedbackRefId;
  readonly score?: number;
  readonly comment?: string;
};

export type TraceLinks = {
  readonly scenarioId?: Scenario;
  readonly runId?: string;
  readonly evalExampleIds?: readonly string[];
  readonly trainingRecordIds?: readonly string[];
};

export type RedactionReason =
  | "pii-email"
  | "pii-name"
  | "pii-ssn"
  | "secret-token"
  | "pii-custom";

export type DetectedBy = "client" | "ingestion" | "operator";

export type RedactionMarker = {
  readonly path: string;
  readonly reason: RedactionReason;
  readonly category?: string;
  readonly detectedBy: DetectedBy;
  readonly detectedAt: string;
};


// ---- Routing decision (AC-545) ----

export type ModelRoutingDecisionReason = "default" | "matched-route" | "fallback";

export type ModelRoutingFallbackReason =
  | "budget-exceeded"
  | "latency-breached"
  | "provider-error"
  | "no-match";

export type ProductionTraceRouting = {
  readonly chosen: {
    readonly provider: string;
    readonly model: string;
    readonly endpoint?: string;
  };
  readonly matchedRouteId?: string;
  readonly reason: ModelRoutingDecisionReason;
  readonly fallbackReason?: ModelRoutingFallbackReason;
  readonly evaluatedAt: string;
};

// ---- Aggregate root ----

export type ProductionTrace = {
  readonly schemaVersion: ProductionTraceSchemaVersion;
  readonly traceId: ProductionTraceId;
  readonly source: TraceSource;
  readonly provider: ProviderInfo;
  readonly model: string;
  readonly session?: SessionIdentifier;
  readonly env: EnvContext;
  readonly messages: readonly TraceMessage[];
  readonly toolCalls: readonly ToolCall[];
  readonly outcome?: ProductionOutcome;
  readonly timing: TimingInfo;
  readonly usage: UsageInfo;
  readonly feedbackRefs: readonly FeedbackRef[];
  readonly links: TraceLinks;
  readonly redactions: readonly RedactionMarker[];
  readonly routing?: ProductionTraceRouting;
  readonly metadata?: Record<string, unknown>;
};

// Shared validation-result shape (matches Foundation B's control-plane contract).
export type ValidationResult =
  | { readonly valid: true }
  | { readonly valid: false; readonly errors: readonly string[] };
