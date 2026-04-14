import {
  extractPromptSections,
  REQUIRED_SYSTEM_PROMPT_SECTIONS,
} from "./prompt-alignment-helpers.js";
import type {
  PromptPair,
  PromptShape,
  ValidationResult,
} from "./prompt-alignment-types.js";

export function buildPromptContractShape(): PromptShape {
  return {
    systemFields: [
      "scenarioRules",
      "strategyInterface",
      "evaluationCriteria",
      "playbook",
      "trajectory",
    ],
    userFields: ["task"],
    responseFormat: "JSON strategy or structured text matching scenario interface",
  };
}

export function validatePromptContract(prompt: PromptPair): ValidationResult {
  const errors: string[] = [];
  const systemSections = extractPromptSections(prompt.system);

  for (const required of REQUIRED_SYSTEM_PROMPT_SECTIONS) {
    if (!systemSections.includes(required)) {
      errors.push(`Missing required system section: ${required}`);
    }
  }

  if (!prompt.user || prompt.user.trim().length < 3) {
    errors.push("User prompt is empty or too short");
  }

  return { valid: errors.length === 0, errors };
}
