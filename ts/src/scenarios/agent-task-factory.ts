/**
 * AgentTaskFactory — creates AgentTaskInterface instances from specs.
 */

import type { AgentTaskInterface, AgentTaskResult } from "../types/index.js";
import { LLMJudge } from "../judge/llm-judge.js";
import type { LLMProvider } from "../types/index.js";
import type { HookBus } from "../extensions/index.js";
import type { AgentTaskSpec } from "./agent-task-spec.js";
import { assertFamilyContract } from "./family-interfaces.js";
import { completeAgentTaskArtifact } from "./agent-task-artifact-completion.js";
import { buildAgentTaskRevisionPrompt } from "./agent-task-revision-prompt.js";

export interface AgentTaskFactoryOpts {
  spec: AgentTaskSpec;
  name: string;
  provider?: LLMProvider;
  hookBus?: HookBus | null;
}

/**
 * Create a concrete AgentTaskInterface from a spec.
 */
export function createAgentTask(opts: AgentTaskFactoryOpts): AgentTaskInterface & {
  readonly name: string;
  readonly spec: AgentTaskSpec;
} {
  const { spec, name, provider, hookBus } = opts;
  let lastReferenceContext = spec.referenceContext ?? undefined;
  let lastRequiredConcepts = spec.requiredConcepts ? [...spec.requiredConcepts] : undefined;

  const task = {
    name,
    spec,

    getTaskPrompt(_state: Record<string, unknown>): string {
      let prompt = spec.taskPrompt;
      if (spec.sampleInput) {
        prompt += "\n\n## Input Data\n" + spec.sampleInput;
      }
      if (spec.referenceContext) {
        prompt += "\n\n## Reference Context\n" + spec.referenceContext;
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
      const state: Record<string, unknown> = {
        taskName: name,
        outputFormat: spec.outputFormat,
        seed: seed ?? null,
      };
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
        hookBus: hookBus ?? null,
      });
      lastReferenceContext = evalOpts?.referenceContext ?? spec.referenceContext ?? undefined;
      const judgeReferenceContext = combineJudgeReferenceContext(
        spec.sampleInput,
        lastReferenceContext,
        spec.evaluationContext,
      );
      const effectiveRequiredConcepts =
        evalOpts?.requiredConcepts ?? spec.requiredConcepts ?? undefined;
      lastRequiredConcepts = effectiveRequiredConcepts ? [...effectiveRequiredConcepts] : undefined;
      const privateResult = await judge.evaluate({
        taskPrompt: spec.taskPrompt,
        agentOutput: output,
        referenceContext: judgeReferenceContext,
        requiredConcepts: lastRequiredConcepts,
        calibrationExamples: evalOpts?.calibrationExamples,
      });
      return candidateSafeJudgeResult({
        judgeResult: {
          score: privateResult.score,
          reasoning: privateResult.reasoning,
          dimensionScores: privateResult.dimensionScores ?? {},
          internalRetries: privateResult.internalRetries ?? 0,
          // AC-885: carry the judge's evaluator epoch so the improve loop can detect epoch changes.
          evaluatorEpoch: privateResult.evaluatorEpoch ?? null,
        },
        evaluationContext: spec.evaluationContext,
        provider,
        model: spec.judgeModel || provider.defaultModel(),
        rubric: spec.judgeRubric,
        hookBus: hookBus ?? null,
        taskPrompt: spec.taskPrompt,
        output,
        sampleInput: spec.sampleInput,
        referenceContext: lastReferenceContext,
        requiredConcepts: lastRequiredConcepts,
      });
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
      const prompt = buildAgentTaskRevisionPrompt({
        revisionPrompt: spec.revisionPrompt,
        output,
        judgeResult,
        taskPrompt: spec.taskPrompt,
        judgeRubric: spec.judgeRubric,
        referenceContext: lastReferenceContext,
        referenceSources: spec.referenceSources,
        requiredConcepts: lastRequiredConcepts,
        sampleInput: spec.sampleInput,
      });
      const result = await completeAgentTaskArtifact({
        hookBus: hookBus ?? null,
        provider,
        role: "agent_task_revise",
        artifactLabel: "revision",
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

async function candidateSafeJudgeResult(opts: {
  judgeResult: AgentTaskResult;
  evaluationContext?: string | null;
  provider: LLMProvider;
  model: string;
  rubric: string;
  hookBus: HookBus | null;
  taskPrompt: string;
  output: string;
  sampleInput?: string | null;
  referenceContext?: string;
  requiredConcepts?: string[];
}): Promise<AgentTaskResult> {
  if (!opts.evaluationContext?.trim()) return opts.judgeResult;

  const publicJudge = new LLMJudge({
    provider: opts.provider,
    model: opts.model,
    rubric: opts.rubric,
    hookBus: opts.hookBus,
  });
  const publicFeedback = await publicJudge.evaluate({
    taskPrompt: opts.taskPrompt,
    agentOutput: opts.output,
    referenceContext: combineJudgeReferenceContext(opts.sampleInput, opts.referenceContext),
    requiredConcepts: opts.requiredConcepts,
  });
  return {
    score: opts.judgeResult.score,
    reasoning: publicFeedback.reasoning,
    dimensionScores: publicFeedback.dimensionScores ?? {},
    internalRetries: opts.judgeResult.internalRetries + (publicFeedback.internalRetries ?? 0),
    evaluatorEpoch: opts.judgeResult.evaluatorEpoch ?? null,
  };
}

function combineJudgeReferenceContext(
  sampleInput?: string | null,
  visibleReferenceContext?: string,
  evaluationContext?: string | null,
): string | undefined {
  const sections = [
    sampleInput?.trim()
      ? [
          "## Candidate Input and Improvement Target",
          "Use this candidate-visible material to verify that the output transforms, analyzes, or improves the supplied input as requested.",
          sampleInput.trim(),
        ].join("\n")
      : "",
    visibleReferenceContext?.trim()
      ? `## Task Reference Context\n${visibleReferenceContext.trim()}`
      : "",
    evaluationContext?.trim()
      ? [
          "## Evaluator-only Context",
          "Use this material only to evaluate the candidate. Do not treat it as part of the candidate output. Its raw content and identifying details must not appear in judge reasoning because revision may use only candidate-safe feedback.",
          evaluationContext.trim(),
        ].join("\n")
      : "",
  ].filter((section) => section.length > 0);
  return sections.length > 0 ? sections.join("\n\n") : undefined;
}
