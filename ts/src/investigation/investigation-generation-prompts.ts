export interface InvestigationPrompt {
  systemPrompt: string;
  userPrompt: string;
}

export function buildInvestigationSpecPrompt(description: string): InvestigationPrompt {
  return {
    systemPrompt: `You are an investigation designer. Given a problem description, produce an investigation spec as JSON.

Required fields:
- description: investigation summary
- environment_description: system/context being investigated
- initial_state_description: what is known at the start
- evidence_pool_description: what evidence sources are available
- diagnosis_target: what we're trying to determine
- success_criteria: array of strings (what constitutes a successful investigation)
- failure_modes: array of strings
- max_steps: positive integer
- actions: array of {name, description, parameters, preconditions, effects}
- evidence_pool: array of {id, content, isRedHerring, relevance}
- correct_diagnosis: the ground truth answer

Output ONLY the JSON object, no markdown fences.`,
    userPrompt: `Investigation: ${description}`,
  };
}

export function buildInvestigationHypothesisPrompt(opts: {
  description: string;
  execution: { stepsExecuted: number; collectedEvidence: Array<{ content: string }> };
  maxHypotheses?: number;
}): InvestigationPrompt {
  return {
    systemPrompt: `You are a diagnostic analyst. Given an investigation description and collected evidence, generate hypotheses. Output JSON:
{
  "question": "The specific question being investigated",
  "hypotheses": [
    { "statement": "Hypothesis text", "confidence": 0.0-1.0 }
  ]
}
Output ONLY the JSON object.`,
    userPrompt: `Investigation: ${opts.description}\nEvidence collected: ${
      opts.execution.collectedEvidence.map((item) => item.content).join(", ") || "none yet"
    }\nSteps taken: ${opts.execution.stepsExecuted}\nMaximum hypotheses: ${opts.maxHypotheses ?? 5}`,
  };
}
