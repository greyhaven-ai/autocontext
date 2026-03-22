/**
 * RLM (REPL-Loop Mode) module — multi-turn LLM REPL session for agent roles.
 * TypeScript port of Python autocontext.rlm / mts.harness.repl.
 */

export { RlmSession, extractCode } from "./session.js";
export type { RlmSessionOpts, RlmResult } from "./session.js";

export {
  ReplCommandSchema,
  ReplResultSchema,
  ExecutionRecordSchema,
  RlmContextSchema,
  RlmTaskConfigSchema,
  RlmPhaseSchema,
  RlmSessionRecordSchema,
} from "./types.js";

export type {
  ReplCommand,
  ReplResult,
  ExecutionRecord,
  RlmContext,
  ReplWorker,
  LlmComplete,
  RlmTaskConfig,
  RlmPhase,
  RlmSessionRecord,
} from "./types.js";

export { SecureExecReplWorker } from "./secure-exec-worker.js";
export type { SecureExecReplWorkerOpts } from "./secure-exec-worker.js";
export { runAgentTaskRlmSession } from "./agent-task.js";
export type { AgentTaskRlmOpts } from "./agent-task.js";
