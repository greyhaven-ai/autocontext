/**
 * AgentTaskFactory — creates AgentTaskInterface instances from specs.
 */

import type { AgentTaskInterface, AgentTaskResult, JudgeResult } from "../types/index.js";
import { LLMJudge } from "../judge/llm-judge.js";
import type { LLMProvider } from "../types/index.js";
import type { HookBus } from "../extensions/index.js";
import type { AgentTaskSpec } from "./agent-task-spec.js";
import { assertFamilyContract } from "./family-interfaces.js";
import { completeAgentTaskArtifact } from "./agent-task-artifact-completion.js";
import { buildAgentTaskRevisionPrompt } from "./agent-task-revision-prompt.js";
import {
  assertAgentTaskOutputFormat,
  buildAgentTaskOutputFormatBlock,
} from "./agent-task-output-format.js";
import {
  acquireProviderIsolation,
  closeProviderIsolation,
  NO_TOOLS_PROVIDER_ISOLATION,
} from "../providers/provider-isolation.js";

export interface AgentTaskFactoryOpts {
  spec: AgentTaskSpec;
  name: string;
  provider?: LLMProvider;
  /** Provider reserved for authoritative evaluation. Candidate-facing work uses provider. */
  evaluationProvider?: LLMProvider;
  hookBus?: HookBus | null;
}

/**
 * Create a concrete AgentTaskInterface from a spec.
 */
export function createAgentTask(opts: AgentTaskFactoryOpts): AgentTaskInterface & {
  readonly name: string;
  readonly spec: AgentTaskSpec;
} {
  const { spec, name, provider, evaluationProvider, hookBus } = opts;
  let lastReferenceContext = spec.referenceContext ?? undefined;
  let lastRequiredConcepts = spec.requiredConcepts ? [...spec.requiredConcepts] : undefined;

  const task = {
    name,
    spec,

    getTaskPrompt(_state: Record<string, unknown>): string {
      return [
        spec.taskPrompt,
        spec.sampleInput ? `## Input Data\n${spec.sampleInput}` : "",
        spec.improvementTaskContractVersion === 1 && spec.referenceContext
          ? `## Reference Context\n${spec.referenceContext}`
          : "",
        spec.improvementTaskContractVersion === 1
          ? buildAgentTaskOutputFormatBlock(spec.outputFormat)
          : "",
      ]
        .filter((block) => block.length > 0)
        .join("\n\n");
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
      assertAgentTaskOutputFormat({
        improvementTaskContractVersion: spec.improvementTaskContractVersion,
        outputFormat: spec.outputFormat,
        output,
        artifactLabel: "candidate output",
      });
      if (!provider) {
        throw new Error("LLM provider required for evaluation — pass provider in factory opts");
      }
      lastReferenceContext = evalOpts?.referenceContext ?? spec.referenceContext ?? undefined;
      const judgeReferenceContext = combineJudgeReferenceContext(
        spec.sampleInput,
        lastReferenceContext,
        spec.evaluationContext,
      );
      const effectiveRequiredConcepts =
        evalOpts?.requiredConcepts ?? spec.requiredConcepts ?? undefined;
      lastRequiredConcepts = effectiveRequiredConcepts ? [...effectiveRequiredConcepts] : undefined;
      const hasEvaluatorOnlyContext = Boolean(spec.evaluationContext?.trim());
      const authoritativeProvider = evaluationProvider ?? provider;
      const acquiredEvaluationProvider = hasEvaluatorOnlyContext
        ? acquireProviderIsolation(authoritativeProvider, NO_TOOLS_PROVIDER_ISOLATION)
        : { provider: authoritativeProvider, owned: false };
      let privateResult: JudgeResult;
      try {
        const judge = new LLMJudge({
          provider: acquiredEvaluationProvider.provider,
          model: spec.judgeModel || acquiredEvaluationProvider.provider.defaultModel(),
          rubric: spec.judgeRubric,
          hookBus: hookBus ?? null,
          ...(hasEvaluatorOnlyContext ? { evaluationContext: spec.evaluationContext } : {}),
          ...(hasEvaluatorOnlyContext ? { promptVisibility: "evaluator_only" as const } : {}),
        });
        privateResult = await judge.evaluate({
          taskPrompt: spec.taskPrompt,
          agentOutput: output,
          referenceContext: judgeReferenceContext,
          requiredConcepts: lastRequiredConcepts,
          calibrationExamples: evalOpts?.calibrationExamples,
        });
      } finally {
        closeProviderIsolation(acquiredEvaluationProvider);
      }
      return candidateSafeJudgeResult({
        judgeResult: {
          score: privateResult.score,
          reasoning: privateResult.reasoning,
          dimensionScores: privateResult.dimensionScores ?? {},
          internalRetries: privateResult.internalRetries ?? 0,
          ...(privateResult.parseMethod === "none" ? { authoritativeParseFailed: true } : {}),
          // AC-885: carry the judge's evaluator epoch so the improve loop can detect epoch changes.
          evaluatorEpoch: privateResult.evaluatorEpoch ?? null,
        },
        evaluationContext: spec.evaluationContext,
        provider,
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
        outputFormat: spec.outputFormat,
        improvementTaskContractVersion: spec.improvementTaskContractVersion,
        referenceContext: lastReferenceContext,
        referenceSources: spec.referenceSources,
        requiredConcepts: lastRequiredConcepts,
        sampleInput: spec.sampleInput,
      });
      const acquiredRevisionProvider = spec.evaluationContext?.trim()
        ? acquireProviderIsolation(provider, NO_TOOLS_PROVIDER_ISOLATION)
        : { provider, owned: false };
      try {
        const result = await completeAgentTaskArtifact({
          hookBus: hookBus ?? null,
          provider: acquiredRevisionProvider.provider,
          role: "agent_task_revise",
          artifactLabel: "revision",
          systemPrompt: "You are a helpful assistant revising your previous output.",
          userPrompt: prompt,
          model: undefined,
        });
        assertAgentTaskOutputFormat({
          improvementTaskContractVersion: spec.improvementTaskContractVersion,
          outputFormat: spec.outputFormat,
          output: result.text,
          artifactLabel: "revision",
        });
        return result.text;
      } finally {
        closeProviderIsolation(acquiredRevisionProvider);
      }
    },
  };

  assertFamilyContract(task, "agent_task", `custom agent task '${name}'`);
  return task;
}

async function candidateSafeJudgeResult(opts: {
  judgeResult: AgentTaskResult;
  evaluationContext?: string | null;
  provider: LLMProvider;
  rubric: string;
  hookBus: HookBus | null;
  taskPrompt: string;
  output: string;
  sampleInput?: string | null;
  referenceContext?: string;
  requiredConcepts?: string[];
}): Promise<AgentTaskResult> {
  if (!opts.evaluationContext?.trim()) return opts.judgeResult;

  const acquiredPublicProvider = acquireProviderIsolation(
    opts.provider,
    NO_TOOLS_PROVIDER_ISOLATION,
  );
  let publicFeedback: JudgeResult;
  try {
    const publicJudge = new LLMJudge({
      provider: acquiredPublicProvider.provider,
      model: acquiredPublicProvider.provider.defaultModel(),
      rubric: opts.rubric,
      hookBus: opts.hookBus,
    });
    publicFeedback = await publicJudge.evaluate({
      taskPrompt: opts.taskPrompt,
      agentOutput: opts.output,
      referenceContext: combineJudgeReferenceContext(opts.sampleInput, opts.referenceContext),
      requiredConcepts: opts.requiredConcepts,
    });
  } finally {
    closeProviderIsolation(acquiredPublicProvider);
  }
  const publicFeedbackParsed = publicFeedback.parseMethod !== "none";
  return {
    score: opts.judgeResult.score,
    reasoning: publicFeedbackParsed
      ? publicFeedback.reasoning
      : "Candidate-safe feedback could not be generated; authoritative evaluation score retained.",
    dimensionScores: publicFeedbackParsed ? (publicFeedback.dimensionScores ?? {}) : {},
    internalRetries: opts.judgeResult.internalRetries + (publicFeedback.internalRetries ?? 0),
    ...(opts.judgeResult.authoritativeParseFailed === true
      ? { authoritativeParseFailed: true }
      : {}),
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
