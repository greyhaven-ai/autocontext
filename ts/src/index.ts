/**
 * autoctx — autocontext TypeScript toolkit.
 */

// Core types
export type {
  CompletionResult,
  LLMProvider,
  JudgeResult,
  AgentTaskResult,
  AgentTaskInterface,
  TaskStatus,
  TaskRow,
  RoundResult,
  ImprovementResult,
  EventType,
  NotificationEvent,
} from "./types/index.js";

export {
  CompletionResultSchema,
  JudgeResultSchema,
  AgentTaskResultSchema,
  TaskStatusSchema,
  TaskRowSchema,
  RoundResultSchema,
  ImprovementResultSchema,
  EventTypeSchema,
  NotificationEventSchema,
  ProviderError,
} from "./types/index.js";

// Providers
export {
  createAnthropicProvider,
  createOpenAICompatibleProvider,
  createProvider,
  resolveProviderConfig,
} from "./providers/index.js";
export type {
  AnthropicProviderOpts,
  OpenAICompatibleProviderOpts,
  CreateProviderOpts,
  ProviderConfig,
} from "./providers/index.js";

// Judge
export {
  LLMJudge,
  DelegatedJudge,
  CallbackJudge,
  SequentialDelegatedJudge,
  parseJudgeResponse,
} from "./judge/index.js";
export type {
  LLMJudgeOpts,
  ParsedJudge,
  DelegatedResult,
  CallbackEvaluateFn,
  DelegatedEvaluateOpts,
  JudgeInterface,
} from "./judge/index.js";

// Storage
export { SQLiteStore } from "./storage/index.js";
export type {
  TaskQueueRow,
  HumanFeedbackRow,
  RunRow,
  GenerationRow,
  MatchRow,
  AgentOutputRow,
  TrajectoryRow,
  UpsertGenerationOpts,
  RecordMatchOpts,
} from "./storage/index.js";

// Execution
export { ImprovementLoop, isParseFailure, isImproved } from "./execution/improvement-loop.js";
export type { ImprovementLoopOpts } from "./execution/improvement-loop.js";
export { cleanRevisionOutput } from "./execution/output-cleaner.js";
export { TaskRunner, SimpleAgentTask, enqueueTask } from "./execution/task-runner.js";
export type { TaskRunnerOpts, TaskConfig } from "./execution/task-runner.js";
export { JudgeExecutor } from "./execution/judge-executor.js";
export { ActionFilterHarness, ActionDictSchema } from "./execution/action-filter.js";
export type { ActionDict, ScenarioLike, HarnessLoaderLike } from "./execution/action-filter.js";
export { StrategyValidator, ValidationResultSchema } from "./execution/strategy-validator.js";
export type { ValidationResult, MatchResult as StrategyMatchResult, StrategyValidatorOpts, ExecuteMatchFn } from "./execution/strategy-validator.js";
export { expectedScore, updateElo } from "./execution/elo.js";
export { ExecutionSupervisor, LocalExecutor } from "./execution/supervisor.js";
export type { ExecutionInput, ExecutionOutput, ExecutionEngine } from "./execution/supervisor.js";
export { TournamentRunner } from "./execution/tournament.js";
export type { TournamentOpts, TournamentResult, MatchResult as TournamentMatchResult } from "./execution/tournament.js";

// Runtimes
export type { AgentOutput, AgentRuntime } from "./runtimes/index.js";
export { DirectAPIRuntime } from "./runtimes/index.js";
export { ClaudeCLIRuntime, createSessionRuntime } from "./runtimes/index.js";
export type { ClaudeCLIConfig } from "./runtimes/index.js";

// Scenarios
export type {
  AgentTaskSpec,
  AgentTaskFactoryOpts,
  AgentTaskCreatorOpts,
  CreatedScenario,
  SimulationCreatorOpts,
  SimulationScenarioHandle,
  SimulationSpec,
  SimulationActionSpec,
  ScenarioInterface,
  Observation,
  Result as ScenarioResult,
  ReplayEnvelope,
  ExecutionLimits,
  ScoringDimension,
  LegalAction,
} from "./scenarios/index.js";
export {
  AgentTaskSpecSchema,
  parseRawSpec,
  parseAgentTaskSpec,
  designAgentTask,
  SimulationSpecSchema,
  SimulationActionSpecSchema,
  parseRawSimulationSpec,
  parseSimulationSpec,
  designSimulation,
  validateSpec,
  createAgentTask,
  AgentTaskCreator,
  SimulationCreator,
  shouldUseSimulationFamily,
  SPEC_START,
  SPEC_END,
  SIM_SPEC_START,
  SIM_SPEC_END,
  ObservationSchema,
  ResultSchema,
  ReplayEnvelopeSchema,
  ExecutionLimitsSchema,
  GridCtfScenario,
  SCENARIO_REGISTRY,
  isGameScenario,
  isAgentTask,
} from "./scenarios/index.js";

// Knowledge / Skill Export
export {
  SkillPackage, exportAgentTaskSkill, cleanLessons, HarnessStore,
  VersionedFileStore, PlaybookManager, PlaybookGuard, ArtifactStore,
  ScoreTrajectoryBuilder, EMPTY_PLAYBOOK_SENTINEL, PLAYBOOK_MARKERS,
  exportStrategyPackage, importStrategyPackage,
} from "./knowledge/index.js";
export type {
  SkillPackageData, HarnessVersionEntry, HarnessVersionMap,
  VersionedFileStoreOpts, GuardResult, ArtifactStoreOpts,
  TrajectoryRow as KnowledgeTrajectoryRow,
  StrategyPackageData, ImportStrategyPackageResult, ConflictPolicy,
} from "./knowledge/index.js";

// Prompts
export { ContextBudget, estimateTokens } from "./prompts/context-budget.js";
export { buildPromptBundle } from "./prompts/templates.js";
export type { PromptBundle, PromptContext } from "./prompts/templates.js";

// Agents (AC-345)
export {
  ROLES, ROLE_CONFIGS, parseCompetitorOutput, parseAnalystOutput, parseCoachOutput,
  parseArchitectOutput, extractDelimitedSection, RuntimeBridgeProvider, RetryProvider,
  ModelRouter, TierConfig, AgentOrchestrator,
} from "./agents/index.js";
export type {
  Role, RoleConfig, CompetitorOutput, AnalystOutput, CoachOutput, ArchitectOutput,
  RetryOpts, TierConfigOpts, SelectOpts, GenerationPrompts, GenerationResult,
} from "./agents/index.js";

// Config
export { AppSettingsSchema, loadSettings, applyPreset, PRESETS } from "./config/index.js";
export type { AppSettings } from "./config/index.js";
export {
  resolveApiKeyValue, saveProviderCredentials, loadProviderCredentials,
  removeProviderCredentials, listConfiguredProviders, discoverAllProviders,
  validateApiKey, getKnownProvider, getModelsForProvider,
  resolveModel, listAuthenticatedModels,
  KNOWN_PROVIDERS, PROVIDER_MODELS,
} from "./config/credentials.js";
export type {
  ProviderCredentials, ProviderAuthStatus, DiscoveredProvider,
  KnownProvider, KnownModel, AuthenticatedModel, ResolveModelOpts,
  ValidationResult as ApiKeyValidationResult,
} from "./config/credentials.js";

// Loop (generation loop components)
export {
  HypothesisTree, HypothesisNodeSchema, EventStreamEmitter, LoopController,
  BackpressureGate, TrendAwareGate, GenerationRunner,
} from "./loop/index.js";
export type { HypothesisNode, EventCallback, GateDecision, GenerationRunnerOpts, RunResult } from "./loop/index.js";

// Analytics / Traces
export { ActorRef, TraceEvent, RunTrace } from "./analytics/index.js";
export type { TraceEventInit } from "./analytics/index.js";
export {
  SCHEMA_VERSION,
  ToolCallSchema,
  TraceMessageSchema,
  TraceOutcomeSchema,
  PublicTraceSchema,
  RedactionPolicySchema,
  ProvenanceManifestSchema,
  SubmissionAttestationSchema,
  validatePublicTrace,
  createProvenanceManifest,
  createSubmissionAttestation,
  exportToPublicTrace,
} from "./traces/public-schema.js";
export type {
  ToolCall,
  TraceMessage,
  TraceOutcome,
  PublicTrace,
  RedactionPolicy as TraceRedactionPolicy,
  ProvenanceManifest,
  SubmissionAttestation,
  ValidationResult as PublicTraceValidationResult,
} from "./traces/public-schema.js";

// Trace redaction / export workflow
export { SensitiveDataDetector, RedactionPolicy, applyRedactionPolicy } from "./traces/redaction.js";
export type {
  DetectionCategory,
  PolicyAction,
  Detection,
  Redaction,
  RedactionResult,
  CustomPattern,
} from "./traces/redaction.js";
export { TraceExportWorkflow } from "./traces/export-workflow.js";
export type {
  ExportRequest,
  RedactionSummary as TraceExportRedactionSummary,
  ExportResult as TraceExportResult,
  TraceExportWorkflowOpts,
} from "./traces/export-workflow.js";

// MCP
export { createMcpServer, startServer } from "./mcp/server.js";
export type { MtsServerOpts } from "./mcp/server.js";

// Interactive Server
export {
  PROTOCOL_VERSION,
  parseClientMessage,
  parseServerMessage,
  RunManager,
  InteractiveServer,
} from "./server/index.js";
export type {
  ServerMessage,
  ClientMessage,
  RunManagerOpts,
  RunManagerState,
  EnvironmentInfo,
  InteractiveServerOpts,
} from "./server/index.js";

// RLM (REPL-Loop Mode)
export { RlmSession, extractCode } from "./rlm/index.js";
export type {
  RlmSessionOpts,
  RlmResult,
  ReplWorker,
  LlmComplete,
  ReplCommand,
  ReplResult,
  ExecutionRecord,
  RlmContext,
  RlmTaskConfig,
  RlmPhase,
  RlmSessionRecord,
} from "./rlm/index.js";
export {
  ReplCommandSchema,
  ReplResultSchema,
  ExecutionRecordSchema,
  RlmContextSchema,
  RlmTaskConfigSchema,
  RlmPhaseSchema,
  RlmSessionRecordSchema,
} from "./rlm/index.js";

// Mission (AC-410)
export {
  MissionSchema, MissionStatusSchema, MissionBudgetSchema,
  MissionStepSchema, StepStatusSchema, VerifierResultSchema,
  MissionStore, MissionManager,
} from "./mission/index.js";
export type {
  Mission, MissionStatus, MissionBudget,
  MissionStep, StepStatus, VerifierResult, MissionVerifier,
} from "./mission/index.js";
export { SecureExecReplWorker, runAgentTaskRlmSession } from "./rlm/index.js";
export type { SecureExecReplWorkerOpts, AgentTaskRlmOpts } from "./rlm/index.js";
