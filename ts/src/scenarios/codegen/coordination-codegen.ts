/**
 * Coordination family codegen (AC-436).
 * Mirrors Python's autocontext/scenarios/custom/coordination_codegen.py.
 */

import { renderCodegenTemplate } from "./template-renderer.js";
import { COORDINATION_SCENARIO_TEMPLATE } from "./templates/coordination-template.js";

export function generateCoordinationSource(
  spec: Record<string, unknown>,
  name: string,
): string {
  const description = String(spec.description ?? "");
  const envDescription = String(
    spec.environment_description ?? spec.environmentDescription ?? "",
  );
  const initialStateDescription = String(
    spec.initial_state_description ?? spec.initialStateDescription ?? "",
  );
  const successCriteria = (spec.success_criteria ?? spec.successCriteria ?? []) as string[];
  const failureModes = (spec.failure_modes ?? spec.failureModes ?? []) as string[];
  const maxSteps = Number(spec.max_steps ?? spec.maxSteps ?? 30);
  const actions = (spec.actions ?? []) as Array<{
    name: string;
    description: string;
    parameters: Record<string, unknown>;
    preconditions: string[];
    effects: string[];
  }>;
  const workers = (spec.workers ?? []) as Array<{
    id: string;
    role: string;
    partialContext: Record<string, unknown>;
  }>;

  return renderCodegenTemplate(COORDINATION_SCENARIO_TEMPLATE, {
    __SCENARIO_NAME_COMMENT__: name,
    __SCENARIO_NAME__: JSON.stringify(name),
    __DESCRIPTION__: JSON.stringify(description),
    __ENV_DESCRIPTION__: JSON.stringify(envDescription),
    __INITIAL_STATE_DESCRIPTION__: JSON.stringify(initialStateDescription),
    __SUCCESS_CRITERIA__: JSON.stringify(successCriteria),
    __FAILURE_MODES__: JSON.stringify(failureModes),
    __MAX_STEPS__: String(maxSteps),
    __ACTIONS__: JSON.stringify(actions, null, 2),
    __WORKERS__: JSON.stringify(workers, null, 2),
  });
}
