/**
 * Scenario revision flow — iterative spec refinement with feedback (AC-441).
 *
 * Ports Python's agent_task_revision.py revision prompt building and adds
 * a generic reviseSpec() that works for all families. Users can create a
 * scenario, see the result, provide feedback, and get an improved version
 * without starting over.
 *
 * Two levels of revision:
 * 1. Spec revision (reviseSpec) — refine the scenario definition itself
 * 2. Output revision (reviseAgentTaskOutput) — refine agent output based on judge feedback
 */

export type {
  JudgeResult,
  OutputRevisionOpts,
  RevisionPromptOpts,
  RevisionResult,
  ReviseSpecOpts,
} from "./scenario-revision-contracts.js";
export { reviseSpec } from "./scenario-revision-request-workflow.js";
export {
  buildRevisionPrompt,
  reviseAgentTaskOutput,
} from "./scenario-revision-prompt-workflow.js";
