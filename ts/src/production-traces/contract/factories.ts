import { newProductionTraceId, type ProductionTraceId } from "./branded-ids.js";
import {
  PRODUCTION_TRACE_SCHEMA_VERSION,
  type EnvContext,
  type FeedbackRef,
  type ProductionOutcome,
  type ProductionTrace,
  type ProviderInfo,
  type RedactionMarker,
  type SessionIdentifier,
  type TimingInfo,
  type ToolCall,
  type TraceLinks,
  type TraceMessage,
  type ProductionTraceRouting,
  type TraceSource,
  type UsageInfo,
} from "./types.js";

export interface CreateProductionTraceInputs {
  readonly id?: ProductionTraceId;
  readonly source: TraceSource;
  readonly provider: ProviderInfo;
  readonly model: string;
  readonly env: EnvContext;
  readonly messages: readonly TraceMessage[];
  readonly toolCalls?: readonly ToolCall[];
  readonly timing: TimingInfo;
  readonly usage: UsageInfo;
  readonly session?: SessionIdentifier;
  readonly outcome?: ProductionOutcome;
  readonly feedbackRefs?: readonly FeedbackRef[];
  readonly links?: TraceLinks;
  readonly redactions?: readonly RedactionMarker[];
  readonly routing?: ProductionTraceRouting;
  readonly metadata?: Record<string, unknown>;
}

/**
 * Create a new ProductionTrace with sensible defaults: fresh ULID traceId,
 * schemaVersion "1.0", empty arrays for toolCalls / feedbackRefs / redactions,
 * empty links object.
 *
 * Pure: no I/O, no side effects other than ULID entropy. Callers that want
 * to persist or emit the result do so themselves.
 */
export function createProductionTrace(inputs: CreateProductionTraceInputs): ProductionTrace {
  const trace: ProductionTrace = {
    schemaVersion: PRODUCTION_TRACE_SCHEMA_VERSION,
    traceId: inputs.id ?? newProductionTraceId(),
    source: inputs.source,
    provider: inputs.provider,
    model: inputs.model,
    ...(inputs.session !== undefined ? { session: inputs.session } : {}),
    env: inputs.env,
    messages: inputs.messages,
    toolCalls: inputs.toolCalls ?? [],
    ...(inputs.outcome !== undefined ? { outcome: inputs.outcome } : {}),
    timing: inputs.timing,
    usage: inputs.usage,
    feedbackRefs: inputs.feedbackRefs ?? [],
    links: inputs.links ?? {},
    redactions: inputs.redactions ?? [],
    ...(inputs.routing !== undefined ? { routing: inputs.routing } : {}),
    ...(inputs.metadata !== undefined ? { metadata: inputs.metadata } : {}),
  };
  return trace;
}
