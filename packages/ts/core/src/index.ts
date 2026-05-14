export const packageRole = "core";
export const packageTopologyVersion = 1;

export {
	createInMemoryWorkspaceEnv,
	createLocalWorkspaceEnv,
	defineRuntimeCommand,
} from "../../../../ts/src/runtimes/workspace-env.js";
export type {
	InMemoryWorkspaceEnvOptions,
	LocalWorkspaceEnvOptions,
	RuntimeCommandContext,
	RuntimeCommandGrant,
	RuntimeCommandGrantOptions,
	RuntimeCommandHandler,
	RuntimeExecOptions,
	RuntimeExecResult,
	RuntimeFileStat,
	RuntimeScopeOptions,
	RuntimeWorkspaceEnv,
} from "../../../../ts/src/runtimes/workspace-env.js";
export { expectedScore, updateElo } from "../../../../ts/src/execution/elo.js";
export type {
	ParsedJudge,
	ParseMethod,
} from "../../../../ts/src/judge/parse.js";
export { parseJudgeResponse } from "../../../../ts/src/judge/parse.js";
export type { RubricCoherenceResult } from "../../../../ts/src/judge/rubric-coherence.js";
export { checkRubricCoherence } from "../../../../ts/src/judge/rubric-coherence.js";
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
export {
	defaultEnvironmentTag,
	newProductionTraceId,
	parseAppId,
	parseContentHash,
	parseEnvironmentTag,
	parseFeedbackRefId,
	parseProductionTraceId,
	parseScenario,
	parseSessionIdHash,
	parseUserIdHash,
} from "../../../../ts/src/production-traces/contract/branded-ids.js";
export { deriveDatasetId } from "../../../../ts/src/production-traces/contract/content-address.js";
export type { CreateProductionTraceInputs } from "../../../../ts/src/production-traces/contract/factories.js";
export { createProductionTrace } from "../../../../ts/src/production-traces/contract/factories.js";
export {
	validateJsonPointer,
	validateRedactionPaths,
	validateTimingSanity,
} from "../../../../ts/src/production-traces/contract/invariants.js";
export {
	validateEnvContext,
	validateFeedbackRef,
	validateProductionOutcome,
	validateProductionTrace,
	validateRedactionMarker,
	validateRedactionPolicy,
	validateRetentionPolicy,
	validateSession,
	validateTimingInfo,
	validateTraceLinks,
	validateTraceSource,
	validateUsageInfo,
} from "../../../../ts/src/production-traces/contract/validators.js";
export type {
	DetectedBy,
	FeedbackKind,
	MessageRole,
	ModelRoutingDecisionReason,
	ModelRoutingFallbackReason,
	OutcomeLabel,
	ProductionOutcome,
	ProductionTrace,
	ProductionTraceSchemaVersion,
	ProviderName,
	RedactionReason,
	TraceLinks,
	TraceSource,
	ValidationResult,
} from "../../../../ts/src/production-traces/contract/types.js";
export { PRODUCTION_TRACE_SCHEMA_VERSION } from "../../../../ts/src/production-traces/contract/types.js";
export type {
	AnthropicErrorReasonKey,
	OpenAiErrorReasonKey,
	OutcomeReasonKey,
} from "../../../../ts/src/production-traces/taxonomy/index.js";
export {
	ANTHROPIC_ERROR_REASON_KEYS,
	ANTHROPIC_ERROR_REASONS,
	OPENAI_ERROR_REASON_KEYS,
	OPENAI_ERROR_REASONS,
	OUTCOME_REASON_KEYS,
} from "../../../../ts/src/production-traces/taxonomy/index.js";
export {
	ContextBudget,
	ContextBudgetPolicy,
	estimateTokens,
} from "../../../../ts/src/prompts/context-budget.js";
export type {
	ComponentBudgetHit,
	ComponentCapHit,
	ContextBudgetPolicyOptions,
	ContextBudgetResult,
	ContextBudgetTelemetry,
	GlobalTrimHit,
} from "../../../../ts/src/prompts/context-budget.js";
export type {
	PromptBundle,
	PromptContext,
} from "../../../../ts/src/prompts/templates.js";
export { buildPromptBundle } from "../../../../ts/src/prompts/templates.js";
export {
	buildContextSelectionReport,
	ContextSelectionReport,
} from "../../../../ts/src/knowledge/context-selection-report.js";
export type {
	ContextSelectionCandidateInput,
	ContextSelectionDecisionInput,
	ContextSelectionDiagnostic,
	ContextSelectionDiagnosticPolicy,
	ContextSelectionReportPayload,
	ContextSelectionReportSummary,
	ContextSelectionStageSummary,
	ContextSelectionTelemetryCard,
} from "../../../../ts/src/knowledge/context-selection-report.js";
export type {
	ExecutionLimits,
	LegalAction,
	Observation,
	ReplayEnvelope,
	Result,
	ScenarioInterface,
	ScoringDimension,
} from "../../../../ts/src/scenarios/game-interface.js";
export {
	ExecutionLimitsSchema,
	ObservationSchema,
	ReplayEnvelopeSchema,
	ResultSchema,
} from "../../../../ts/src/scenarios/game-interface.js";
export type { ArtifactEditingInterface } from "../../../../ts/src/scenarios/primary-family-interface-types.js";
export type {
	CoordinationInterface,
	InvestigationInterface,
	NegotiationInterface,
	OperatorLoopInterface,
	SchemaEvolutionInterface,
	SimulationInterface,
	ToolFragilityInterface,
	WorkflowInterface,
} from "../../../../ts/src/scenarios/simulation-family-interface-types.js";
export type {
	AgentOutputRow,
	GenerationRow,
	HumanFeedbackRow,
	MatchRow,
	RecordMatchOpts,
	RunRow,
	TaskQueueRow,
	TrajectoryRow,
	UpsertGenerationOpts,
} from "../../../../ts/src/storage/storage-contracts.js";
export type {
	AgentTaskInterface,
	AgentTaskResult,
	CompletionResult,
	LLMProvider,
} from "../../../../ts/src/types/index.js";
export {
	AgentTaskResultSchema,
	CompletionResultSchema,
	ProviderError,
} from "../../../../ts/src/types/index.js";
