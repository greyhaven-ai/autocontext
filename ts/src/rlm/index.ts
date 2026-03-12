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
} from "./types.js";

export type {
  ReplCommand,
  ReplResult,
  ExecutionRecord,
  RlmContext,
  ReplWorker,
  LlmComplete,
} from "./types.js";
