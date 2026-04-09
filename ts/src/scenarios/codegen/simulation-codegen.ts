/**
 * Simulation family codegen — generates JS source from a SimulationSpec (AC-436).
 * Mirrors Python's autocontext/scenarios/custom/simulation_codegen.py.
 */

import { renderCodegenTemplate } from "./template-renderer.js";
import { SIMULATION_SCENARIO_TEMPLATE } from "./templates/simulation-template.js";

export function generateSimulationSource(
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
  const maxSteps = Number(spec.max_steps ?? spec.maxSteps ?? 20);
  const actions = (spec.actions ?? []) as Array<{
    name: string;
    description: string;
    parameters: Record<string, unknown>;
    preconditions: string[];
    effects: string[];
  }>;

  const actionSpecs = JSON.stringify(
    actions.map((action) => ({
      name: action.name,
      description: action.description,
      parameters: action.parameters ?? {},
      preconditions: action.preconditions ?? [],
      effects: action.effects ?? [],
    })),
    null,
    2,
  );

  return renderCodegenTemplate(SIMULATION_SCENARIO_TEMPLATE, {
    __SCENARIO_NAME_COMMENT__: name,
    __SCENARIO_NAME__: JSON.stringify(name),
    __DESCRIPTION__: JSON.stringify(description),
    __ENV_DESCRIPTION__: JSON.stringify(envDescription),
    __INITIAL_STATE_DESCRIPTION__: JSON.stringify(initialStateDescription),
    __SUCCESS_CRITERIA__: JSON.stringify(successCriteria),
    __FAILURE_MODES__: JSON.stringify(failureModes),
    __MAX_STEPS__: String(maxSteps),
    __ACTION_SPECS__: actionSpecs,
    __REQUIRED_ACTIONS__: JSON.stringify(actions.map((action) => action.name)),
  });
}
