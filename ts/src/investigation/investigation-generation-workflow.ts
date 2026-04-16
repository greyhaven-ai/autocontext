import { designInvestigation } from "../scenarios/investigation-designer.js";
import type { InvestigationSpec } from "../scenarios/investigation-spec.js";
import type { LLMProvider } from "../types/index.js";
import {
  buildFallbackInvestigationHypothesisSet,
  parseInvestigationHypothesisResponse,
  parseInvestigationSpecResponse,
} from "./investigation-generation-parsing.js";
import {
  buildInvestigationHypothesisPrompt,
  buildInvestigationSpecPrompt,
} from "./investigation-generation-prompts.js";

export interface InvestigationHypothesisDraft {
  statement: string;
  confidence: number;
}

export interface InvestigationHypothesisSet {
  hypotheses: InvestigationHypothesisDraft[];
  question: string;
}

function serializeDesignedInvestigationSpec(spec: InvestigationSpec): Record<string, unknown> {
  return {
    description: spec.description,
    environment_description: spec.environmentDescription,
    initial_state_description: spec.initialStateDescription,
    evidence_pool_description: spec.evidencePoolDescription,
    diagnosis_target: spec.diagnosisTarget,
    success_criteria: spec.successCriteria,
    failure_modes: spec.failureModes,
    actions: spec.actions,
    max_steps: spec.maxSteps,
  };
}

export async function buildInvestigationSpec(opts: {
  provider: LLMProvider;
  description: string;
}): Promise<Record<string, unknown>> {
  const result = await opts.provider.complete(buildInvestigationSpecPrompt(opts.description));

  const parsed = parseInvestigationSpecResponse(result.text);
  if (parsed) {
    return parsed;
  }

  const designed = await designInvestigation(opts.description, async (system, user) => {
    const fallback = await opts.provider.complete({
      systemPrompt: system,
      userPrompt: user,
    });
    return fallback.text;
  });
  return serializeDesignedInvestigationSpec(designed);
}

export async function generateInvestigationHypotheses(opts: {
  provider: LLMProvider;
  description: string;
  execution: { stepsExecuted: number; collectedEvidence: Array<{ content: string }> };
  maxHypotheses?: number;
}): Promise<InvestigationHypothesisSet> {
  try {
    const result = await opts.provider.complete(
      buildInvestigationHypothesisPrompt({
        description: opts.description,
        execution: opts.execution,
        maxHypotheses: opts.maxHypotheses,
      }),
    );

    const parsed = parseInvestigationHypothesisResponse({
      text: result.text,
      description: opts.description,
      maxHypotheses: opts.maxHypotheses,
    });
    if (parsed) {
      return parsed;
    }
  } catch {
    // fallback
  }

  return buildFallbackInvestigationHypothesisSet({
    description: opts.description,
    maxHypotheses: opts.maxHypotheses,
  });
}
