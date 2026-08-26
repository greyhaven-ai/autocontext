import { createHash } from "node:crypto";

import type { AgentTaskSpec } from "./agent-task-spec.js";

export const NATIVE_AGENT_TASK_QUEUE_MARKER = "native_agent_task_v1";

/**
 * Contract-v1 tasks and any task carrying evaluator-only evidence must use the
 * native task implementation. Generic judge/improve paths have only one
 * context channel and would otherwise silently expose or discard that data.
 */
export function requiresNativeAgentTaskExecution(
  spec: Pick<AgentTaskSpec, "improvementTaskContractVersion" | "evaluationContext">,
): boolean {
  return spec.improvementTaskContractVersion === 1 || Boolean(spec.evaluationContext?.trim());
}

export function overrideSavedAgentTaskSpec(
  spec: AgentTaskSpec,
  overrides: {
    taskPrompt?: string;
    judgeRubric?: string;
  },
): AgentTaskSpec {
  return {
    ...spec,
    ...(overrides.taskPrompt === undefined ? {} : { taskPrompt: overrides.taskPrompt }),
    ...(overrides.judgeRubric === undefined ? {} : { judgeRubric: overrides.judgeRubric }),
  };
}

/** Bind queued native execution to the exact normalized saved specification. */
export function savedAgentTaskSpecDigest(spec: AgentTaskSpec): string {
  return `sha256:${createHash("sha256").update(canonicalSpecJson(spec), "utf8").digest("hex")}`;
}

function canonicalSpecJson(value: unknown): string {
  if (value === null) return "null";
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalSpecJson(item)).join(",")}]`;
  }
  if (typeof value === "object") {
    const fields = Object.entries(value)
      .filter(([, item]) => item !== undefined)
      .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalSpecJson(item)}`);
    return `{${fields.join(",")}}`;
  }
  const encoded = JSON.stringify(value);
  if (encoded === undefined) {
    throw new Error(`Saved agent-task spec contains unsupported ${typeof value} content`);
  }
  return encoded;
}
