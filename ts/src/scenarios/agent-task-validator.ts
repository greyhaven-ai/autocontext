/**
 * AgentTaskValidator — validates AgentTaskSpec for completeness.
 * Port of autocontext/src/autocontext/scenarios/custom/agent_task_validator.py
 *
 * Note: In TS we don't do code generation/execution validation.
 * Instead we use Zod for spec validation and a factory for instantiation.
 */

import { AgentTaskSpecSchema } from "./agent-task-spec.js";
import type { AgentTaskSpec } from "./agent-task-spec.js";

/**
 * Validate an AgentTaskSpec for completeness and correctness.
 * Returns an array of error strings (empty = valid).
 */
export function validateSpec(spec: AgentTaskSpec): string[] {
  const result = AgentTaskSpecSchema.safeParse(spec);
  if (!result.success) {
    return result.error.issues.map(
      (issue) => `${issue.path.join(".")}: ${issue.message}`,
    );
  }
  return [];
}
