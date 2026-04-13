/**
 * Spec auto-heal — graceful recovery from malformed specs (AC-440).
 *
 * Ports Python's spec_auto_heal.py and adds broader healing:
 * - Missing sampleInput when prompt references external data
 * - Type coercion (string "10" → number 10)
 * - Missing field inference (empty description → derived from taskPrompt)
 * - Per-family healing applied before codegen
 *
 * The goal: NL descriptions are messy. Auto-heal turns "your description
 * had a minor issue" into "we fixed it and created the scenario."
 */

import {
  applyHealedAgentTaskSpec,
  generateSyntheticSampleInput,
  healAgentTaskSpec,
  needsSampleInput,
  normalizeAgentTaskHealSpec,
} from "./spec-auto-heal-agent-task.js";
import {
  coerceSpecTypes,
  inferMissingFields,
} from "./spec-auto-heal-core.js";
import {
  healSimulationPreconditions,
  needsPreconditionHealing,
} from "./spec-auto-heal-preconditions.js";

export {
  needsSampleInput,
  generateSyntheticSampleInput,
  healAgentTaskSpec,
  coerceSpecTypes,
  inferMissingFields,
};

/**
 * Apply all healing passes to a spec before codegen.
 *
 * 1. Type coercion (string → number/boolean)
 * 2. Missing field inference
 * 3. Family-specific healing (e.g., agent_task sampleInput)
 *
 * Returns a new spec object (does not mutate the original).
 */
export function healSpec(
  spec: Record<string, unknown>,
  family: string,
  description?: string,
): Record<string, unknown> {
  let healed = { ...spec };

  healed = coerceSpecTypes(healed);
  healed = inferMissingFields(healed);

  if (family === "agent_task") {
    const healedTask = healAgentTaskSpec(
      normalizeAgentTaskHealSpec(healed),
      description,
    );
    healed = applyHealedAgentTaskSpec(healed, healedTask);
  }

  if (needsPreconditionHealing(family)) {
    healed = healSimulationPreconditions(healed);
  }

  return healed;
}
