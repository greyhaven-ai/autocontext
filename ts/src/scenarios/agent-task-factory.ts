/**
 * AgentTaskFactory — creates AgentTaskInterface instances from specs.
 */

import type { AgentTaskInterface, AgentTaskResult } from "../types/index.js";
import { LLMJudge } from "../judge/llm-judge.js";
import type { LLMProvider } from "../types/index.js";
import type { AgentTaskSpec } from "./agent-task-spec.js";
import { assertFamilyContract } from "./family-interfaces.js";

export interface AgentTaskFactoryOpts {
  spec: AgentTaskSpec;
  name: string;
  provider?: LLMProvider;
}

/**
 * Create a concrete AgentTaskInterface from a spec.
 */
export function createAgentTask(opts: AgentTaskFactoryOpts): AgentTaskInterface & {
  readonly name: string;
  readonly spec: AgentTaskSpec;
} {
  const { spec, name, provider } = opts;

  const task = {
    name,
    spec,

    getTaskPrompt(_state: Record<string, unknown>): string {
      let prompt = spec.taskPrompt;
      if (spec.sampleInput) {
        prompt += "\n\n## Input Data\n" + spec.sampleInput;
      }
      return prompt;
    },

    getRubric(): string {
      return spec.judgeRubric;
    },

    describeTask(): string {
      return spec.taskPrompt;
    },

    initialState(seed?: number): Record<string, unknown> {
      const state: Record<string, unknown> = { taskName: name, outputFormat: spec.outputFormat, seed: seed ?? null };
      if (spec.sampleInput) {
        state.sampleInput = spec.sampleInput;
      }
      return state;
    },

    async evaluateOutput(
      output: string,
      _state: Record<string, unknown>,
      evalOpts?: {
        referenceContext?: string;
        requiredConcepts?: string[];
        calibrationExamples?: Array<Record<string, unknown>>;
      },
    ): Promise<AgentTaskResult> {
      if (!provider) {
        throw new Error("LLM provider required for evaluation — pass provider in factory opts");
      }
      const judge = new LLMJudge({
        provider,
        model: spec.judgeModel || provider.defaultModel(),
        rubric: spec.judgeRubric,
      });
      const result = await judge.evaluate({
        taskPrompt: spec.taskPrompt,
        agentOutput: output,
        referenceContext: evalOpts?.referenceContext ?? spec.referenceContext ?? undefined,
        requiredConcepts: evalOpts?.requiredConcepts ?? spec.requiredConcepts ?? undefined,
        calibrationExamples: evalOpts?.calibrationExamples,
      });
      return {
        score: result.score,
        reasoning: result.reasoning,
        dimensionScores: result.dimensionScores ?? {},
        internalRetries: result.internalRetries ?? 0,
      };
    },

    async prepareContext(state: Record<string, unknown>): Promise<Record<string, unknown>> {
      const s = { ...state };
      if (spec.contextPreparation) s.contextPreparation = spec.contextPreparation;
      if (spec.referenceContext) s.referenceContext = spec.referenceContext;
      if (spec.referenceSources) s.referenceSources = spec.referenceSources;
      return s;
    },

    validateContext(state: Record<string, unknown>): string[] {
      const errors: string[] = [];
      if (spec.requiredContextKeys) {
        for (const key of spec.requiredContextKeys) {
          if (!(key in state) || state[key] === undefined || state[key] === null) {
            errors.push(`missing required context key: '${key}'`);
          }
        }
      }
      return errors;
    },

    async reviseOutput(
      output: string,
      judgeResult: AgentTaskResult,
      _state: Record<string, unknown>,
    ): Promise<string> {
      if (!provider || (!spec.revisionPrompt && spec.maxRounds <= 1)) {
        return output;
      }
      const revisionInstructions = spec.revisionPrompt ?? "Revise the output based on the feedback.";
      const prompt = [
        `Original output:\n${output}`,
        `\nJudge score: ${judgeResult.score}`,
        `Judge reasoning: ${judgeResult.reasoning}`,
        `\nRevision instructions: ${revisionInstructions}`,
        `\nProvide the revised output:`,
      ].join("\n");
      const result = await provider.complete({
        systemPrompt: "You are a helpful assistant revising your previous output.",
        userPrompt: prompt,
        model: spec.judgeModel || undefined,
      });
      return result.text;
    },
  };

  assertFamilyContract(task, "agent_task", `custom agent task '${name}'`);
  return task;
}
