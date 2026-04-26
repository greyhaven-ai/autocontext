export const packageRole = "control";
export const packageTopologyVersion = 1;

export type {
	AppId,
	ContentHash,
	EnvironmentTag,
	FeedbackRefId,
	ProductionTraceId,
	Scenario,
	SessionIdHash,
	UserIdHash,
} from "../../../../ts/src/production-traces/contract/branded-ids.js";
export type {
	DetectedBy,
	EnvContext,
	FeedbackKind,
	FeedbackRef,
	MessageRole,
	ModelRoutingDecisionReason,
	ModelRoutingFallbackReason,
	OutcomeLabel,
	ProductionOutcome,
	ProductionTrace,
	ProductionTraceRouting,
	ProductionTraceSchemaVersion,
	ProviderInfo,
	ProviderName,
	RedactionMarker,
	RedactionReason,
	SessionIdentifier,
	TimingInfo,
	ToolCall,
	TraceLinks,
	TraceMessage,
	TraceSource,
	UsageInfo,
	ValidationResult,
} from "../../../../ts/src/production-traces/contract/types.js";
export { PRODUCTION_TRACE_SCHEMA_VERSION } from "../../../../ts/src/production-traces/contract/types.js";
export type { ResearchAdapter } from "../../../../ts/src/research/types.js";
export {
	Citation,
	ResearchConfig,
	ResearchQuery,
	ResearchResult,
	Urgency,
} from "../../../../ts/src/research/types.js";
export {
	ExecutorInfoSchema,
	ExecutorResourcesSchema,
	PROTOCOL_VERSION,
	ScenarioInfoSchema,
	ScoringComponentSchema,
	StrategyParamSchema,
} from "../../../../ts/src/server/protocol.js";
