import type { HookBus } from "../extensions/index.js";
import { createAgentTask } from "../scenarios/agent-task-factory.js";
import { completeAgentTaskArtifact } from "../scenarios/agent-task-artifact-completion.js";
import type { AgentTaskSpec } from "../scenarios/agent-task-spec.js";
import type { AgentTaskInterface, LLMProvider } from "../types/index.js";
import type { RlmSessionRecord } from "../rlm/types.js";
import {
  acquireProviderIsolation,
  closeProviderIsolation,
  NO_TOOLS_PROVIDER_ISOLATION,
} from "../providers/provider-isolation.js";
import { assertAgentTaskOutputFormat } from "../scenarios/agent-task-output-format.js";

export interface StructuredWorkflowAgentTask extends AgentTaskInterface {
  generateOutput(context?: {
    referenceContext?: string;
    requiredConcepts?: string[];
    state?: Record<string, unknown>;
  }): Promise<string>;
  getRlmSessions(): RlmSessionRecord[];
}

/**
 * Adapt the native agent-task implementation to command/queue workflows that
 * also need to generate their initial artifact. Evaluation and revision stay
 * on the native path so candidate-visible reference material and
 * evaluator-only evidence retain their separate handling.
 */
export function createStructuredAgentTaskWorkflow(opts: {
  name: string;
  spec: AgentTaskSpec;
  provider: LLMProvider;
  model?: string | null;
  hookBus?: HookBus | null;
  evaluationProvider?: LLMProvider;
}): StructuredWorkflowAgentTask {
  const spec = opts.spec;
  const task = createAgentTask({
    spec,
    name: opts.name,
    provider: opts.provider,
    ...(opts.evaluationProvider ? { evaluationProvider: opts.evaluationProvider } : {}),
    hookBus: opts.hookBus ?? null,
  });

  return {
    ...task,
    async generateOutput(context): Promise<string> {
      const state = context?.state ?? task.initialState();
      const acquiredDraftProvider = spec.evaluationContext?.trim()
        ? acquireProviderIsolation(opts.provider, NO_TOOLS_PROVIDER_ISOLATION)
        : { provider: opts.provider, owned: false };
      try {
        const result = await completeAgentTaskArtifact({
          hookBus: opts.hookBus ?? null,
          provider: acquiredDraftProvider.provider,
          role: "agent_task_initial",
          artifactLabel: "initial response",
          systemPrompt: "You are a helpful assistant.",
          userPrompt: task.getTaskPrompt(state),
          model: opts.model ?? undefined,
        });
        assertAgentTaskOutputFormat({
          improvementTaskContractVersion: spec.improvementTaskContractVersion,
          outputFormat: spec.outputFormat,
          output: result.text,
          artifactLabel: "initial response",
        });
        return result.text;
      } finally {
        closeProviderIsolation(acquiredDraftProvider);
      }
    },
    getRlmSessions(): RlmSessionRecord[] {
      return [];
    },
  };
}
