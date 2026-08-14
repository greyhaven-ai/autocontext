/** Control-plane adapter for production-traces dataset rubric discovery. */

import type { Rubric, RubricLookup } from "../../production-traces/dataset/index.js";
import { openRegistry } from "./index.js";

const ACTIVE_ARTIFACT_TYPES = [
  "prompt-patch",
  "tool-policy",
  "routing-rule",
  "fine-tuned-model",
  "model-routing",
] as const;

/**
 * Compose the control-plane registry into the production-traces RubricLookup
 * port. The dependency points from the adapter to the port it implements;
 * production-traces never imports the registry implementation.
 */
export function createRegistryRubricLookup(cwd: string): RubricLookup {
  const registry = openRegistry(cwd);
  return async (scenarioId) => {
    try {
      for (const actuatorType of ACTIVE_ARTIFACT_TYPES) {
        const matches = registry.listCandidates({
          scenario: scenarioId,
          actuatorType,
          activationState: "active",
        });
        if (matches.length === 0) continue;
        const first = matches[0]!;
        const rubric: Rubric = {
          rubricId: first.id,
          dimensions: ["registry-active-artifact"],
          description: `Auto-imported from control-plane registry: active ${first.actuatorType} for scenario=${first.scenario}, env=${first.environmentTag}.`,
        };
        return rubric;
      }
    } catch {
      // Missing or unreadable registry state is equivalent to no rubric match.
    }
    return null;
  };
}
