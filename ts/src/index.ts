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
export { LLMJudge, parseJudgeResponse } from "./judge/index.js";
export type { LLMJudgeOpts, ParsedJudge } from "./judge/index.js";

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
export type { ValidationResult, MatchResult, StrategyValidatorOpts, ExecuteMatchFn } from "./execution/strategy-validator.js";

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
} from "./scenarios/index.js";

// Knowledge / Skill Export
export { SkillPackage, exportAgentTaskSkill, cleanLessons, HarnessStore } from "./knowledge/index.js";
export type { SkillPackageData, HarnessVersionEntry, HarnessVersionMap } from "./knowledge/index.js";

// Config
export { AppSettingsSchema, loadSettings, applyPreset, PRESETS } from "./config/index.js";
export type { AppSettings } from "./config/index.js";

// Loop (generation loop components)
export { HypothesisTree, HypothesisNodeSchema, EventStreamEmitter, LoopController } from "./loop/index.js";
export type { HypothesisNode, EventCallback } from "./loop/index.js";

// MCP
export { createMcpServer, startServer } from "./mcp/server.js";
export type { MtsServerOpts } from "./mcp/server.js";

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
} from "./rlm/index.js";
export {
  ReplCommandSchema,
  ReplResultSchema,
  ExecutionRecordSchema,
  RlmContextSchema,
} from "./rlm/index.js";
