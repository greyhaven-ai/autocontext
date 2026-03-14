import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { LLMProvider } from "../types/index.js";
import { getScenarioTypeMarker } from "./families.js";
import type { SimulationSpec } from "./simulation-spec.js";
import { designSimulation } from "./simulation-designer.js";

export interface SimulationCreatorOpts {
  provider: LLMProvider;
  model?: string;
  knowledgeRoot: string;
}

export interface SimulationScenarioHandle {
  family: "simulation";
  name: string;
  spec: SimulationSpec;
}

export function shouldUseSimulationFamily(description: string): boolean {
  const lowered = description.toLowerCase();
  return [
    "stateful",
    "simulation",
    "workflow",
    "orchestration",
    "api",
    "rollback",
    "retry",
    "cancellation",
    "transaction",
    "debug",
    "diagnos",
    "evidence",
    "side effect",
  ].some((keyword) => lowered.includes(keyword));
}

function validateSimulationSpec(spec: SimulationSpec): string[] {
  const errors: string[] = [];
  if (spec.actions.length < 2) errors.push("simulation must define at least two actions");
  const names = spec.actions.map((action) => action.name);
  if (new Set(names).size !== names.length) errors.push("action names must be unique");
  if (spec.maxSteps <= 0) errors.push("maxSteps must be positive");
  return errors;
}

function className(name: string): string {
  return name.split(/[^a-zA-Z0-9]+/).filter(Boolean).map((part) => part[0]!.toUpperCase() + part.slice(1)).join("") + "Simulation";
}

function generateScenarioSource(spec: SimulationSpec, name: string): string {
  const actions = spec.actions
    .map((action) => `            ActionSpec(name=${JSON.stringify(action.name)}, description=${JSON.stringify(action.description)}, parameters=${JSON.stringify(action.parameters)}, preconditions=${JSON.stringify(action.preconditions)}, effects=${JSON.stringify(action.effects)})`)
    .join(",\n");
  const requiredActions = JSON.stringify(spec.actions.map((action) => action.name));
  return `from __future__ import annotations

from typing import Any

from autocontext.scenarios.simulation import Action, ActionResult, ActionSpec, ActionTrace, EnvironmentSpec, SimulationInterface, SimulationResult


class ${className(name)}(SimulationInterface):
    name = ${JSON.stringify(name)}

    def describe_scenario(self) -> str:
        return ${JSON.stringify(spec.description)}

    def describe_environment(self) -> EnvironmentSpec:
        return EnvironmentSpec(
            name=${JSON.stringify(name)},
            description=${JSON.stringify(spec.environmentDescription)},
            available_actions=[
${actions}
            ],
            initial_state_description=${JSON.stringify(spec.initialStateDescription)},
            success_criteria=${JSON.stringify(spec.successCriteria)},
            failure_modes=${JSON.stringify(spec.failureModes)},
        )

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        return {"seed": seed or 0, "step": 0, "completed_actions": [], "failed_actions": [], "timeline": [], "terminal": False}

    def get_available_actions(self, state: dict[str, Any]) -> list[ActionSpec]:
        completed = set(state.get("completed_actions", []))
        return [spec for spec in self.describe_environment().available_actions if spec.name not in completed]

    def validate_action(self, state: dict[str, Any], action: Action) -> tuple[bool, str]:
        specs = {spec.name: spec for spec in self.describe_environment().available_actions}
        spec = specs.get(action.name)
        if spec is None:
            return False, f"unknown action: {action.name}"
        completed = set(state.get("completed_actions", []))
        for requirement in spec.preconditions:
            if requirement not in completed:
                return False, f"precondition not met for {action.name}: {requirement}"
        return True, ""

    def execute_action(self, state: dict[str, Any], action: Action) -> tuple[ActionResult, dict[str, Any]]:
        valid, reason = self.validate_action(state, action)
        next_state = dict(state)
        next_state["timeline"] = list(state.get("timeline", []))
        if not valid:
            next_state["failed_actions"] = [*state.get("failed_actions", []), action.name]
            return ActionResult(success=False, output="", state_changes={}, error=reason), next_state
        next_state["completed_actions"] = [*state.get("completed_actions", []), action.name]
        next_state["timeline"].append({"action": action.name, "parameters": action.parameters})
        return (
            ActionResult(success=True, output=f"executed {action.name}", state_changes={"completed_actions": list(next_state["completed_actions"])}, side_effects=[action.name]),
            next_state,
        )

    def is_terminal(self, state: dict[str, Any]) -> bool:
        required = set(${requiredActions})
        completed = set(state.get("completed_actions", []))
        return required.issubset(completed) or state.get("step", 0) >= ${spec.maxSteps}

    def evaluate_trace(self, trace: ActionTrace, final_state: dict[str, Any]) -> SimulationResult:
        required = set(${requiredActions})
        completed = set(final_state.get("completed_actions", []))
        completion = len(required & completed) / len(required) if required else 1.0
        ordering = trace.success_rate
        failures = sum(1 for record in trace.records if not record.result.success)
        recovery = 1.0 if failures == 0 else max(0.2, 1.0 - (failures / max(len(trace.records), 1)))
        score = round((completion * 0.5) + (ordering * 0.3) + (recovery * 0.2), 4)
        return SimulationResult(
            score=score,
            reasoning=f"Completed {len(completed)} of {len(required)} required actions.",
            dimension_scores={"completion": round(completion, 4), "ordering": round(ordering, 4), "recovery": round(recovery, 4)},
            workflow_complete=required.issubset(completed),
            actions_taken=len(trace.records),
            actions_successful=sum(1 for record in trace.records if record.result.success),
            recovery_attempts=failures,
            rollback_quality=1.0 if failures == 0 else recovery,
        )

    def get_rubric(self) -> str:
        return "Evaluate on completion, correct dependency ordering, and recovery quality."

    def max_steps(self) -> int:
        return ${spec.maxSteps}
`;
}

export class SimulationCreator {
  private provider: LLMProvider;
  private model: string;
  private knowledgeRoot: string;

  constructor(opts: SimulationCreatorOpts) {
    this.provider = opts.provider;
    this.model = opts.model ?? opts.provider.defaultModel();
    this.knowledgeRoot = opts.knowledgeRoot;
  }

  async create(description: string, name: string): Promise<SimulationScenarioHandle> {
    const llmFn = async (system: string, user: string): Promise<string> => {
      const result = await this.provider.complete({
        systemPrompt: system,
        userPrompt: user,
        model: this.model,
      });
      return result.text;
    };
    const spec = await designSimulation(description, llmFn);
    const errors = validateSimulationSpec(spec);
    if (errors.length > 0) {
      throw new Error(`simulation spec validation failed: ${errors.join("; ")}`);
    }

    const customDir = join(this.knowledgeRoot, "_custom_scenarios");
    const scenarioDir = join(customDir, name);
    if (!existsSync(scenarioDir)) mkdirSync(scenarioDir, { recursive: true });

    writeFileSync(join(scenarioDir, "scenario.py"), generateScenarioSource(spec, name), "utf-8");
    writeFileSync(join(scenarioDir, "scenario_type.txt"), getScenarioTypeMarker("simulation"), "utf-8");
    writeFileSync(
      join(scenarioDir, "spec.json"),
      JSON.stringify(
        {
          name,
          scenario_type: getScenarioTypeMarker("simulation"),
          description: spec.description,
          environment_description: spec.environmentDescription,
          initial_state_description: spec.initialStateDescription,
          success_criteria: spec.successCriteria,
          failure_modes: spec.failureModes,
          max_steps: spec.maxSteps,
          actions: spec.actions,
        },
        null,
        2,
      ),
      "utf-8",
    );

    return { family: "simulation", name, spec };
  }
}
