/* eslint-disable */
// AUTO-GENERATED from src/production-traces/contract/json-schemas/ — DO NOT EDIT.
// Regenerate with: node scripts/generate-production-traces-types.mjs
// CI gate: node scripts/generate-production-traces-types.mjs --check

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

// ---- session.schema.json ----

// ---- shared-defs.schema.json ----
export interface SharedDefinitionsForAutocontextProductionTraceDocuments {
  [k: string]: unknown;
}

// ---- timing-info.schema.json ----

// ---- trace-links.schema.json ----

// ---- trace-source.schema.json ----

// ---- usage-info.schema.json ----
