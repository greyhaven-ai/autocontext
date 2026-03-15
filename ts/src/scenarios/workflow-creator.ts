import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { LLMProvider } from "../types/index.js";
import { validateForFamily } from "./family-pipeline.js";
import { getScenarioTypeMarker } from "./families.js";
import type { WorkflowSpec } from "./workflow-spec.js";
import { designWorkflow } from "./workflow-designer.js";

export interface WorkflowCreatorOpts {
  provider: LLMProvider;
  model?: string;
  knowledgeRoot: string;
}

export interface WorkflowScenarioHandle {
  family: "workflow";
  name: string;
  spec: WorkflowSpec;
}

function className(name: string): string {
  return name.split(/[^a-zA-Z0-9]+/).filter(Boolean).map((part) => part[0]!.toUpperCase() + part.slice(1)).join("") + "Workflow";
}

function generateScenarioSource(spec: WorkflowSpec, name: string): string {
  const actions = spec.actions
    .map((action) => `            ActionSpec(name=${JSON.stringify(action.name)}, description=${JSON.stringify(action.description)}, parameters=${JSON.stringify(action.parameters)}, preconditions=${JSON.stringify(action.preconditions)}, effects=${JSON.stringify(action.effects)})`)
    .join(",\n");
  const workflowSteps = JSON.stringify(spec.workflowSteps.map((step) => ({
    name: step.name,
    description: step.description,
    idempotent: step.idempotent,
    reversible: step.reversible,
    compensation: step.compensation ?? null,
  })));
  const requiredActions = JSON.stringify(spec.actions.map((action) => action.name));
  return `from __future__ import annotations

from typing import Any

from autocontext.scenarios.simulation import Action, ActionResult, ActionSpec, ActionTrace, EnvironmentSpec, SimulationResult
from autocontext.scenarios.workflow import CompensationAction, SideEffect, WorkflowInterface, WorkflowResult, WorkflowStep


class ${className(name)}(WorkflowInterface):
    name = ${JSON.stringify(name)}
    _workflow_step_defs = ${workflowSteps}

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
        return {"seed": seed or 0, "step": 0, "completed_actions": [], "failed_actions": [], "timeline": [], "completed_steps": [], "side_effects": [], "compensations": []}

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

    def get_workflow_steps(self) -> list[WorkflowStep]:
        return [WorkflowStep(name=raw["name"], description=raw["description"], idempotent=raw["idempotent"], reversible=raw["reversible"], compensation=raw.get("compensation")) for raw in self._workflow_step_defs]

    def execute_action(self, state: dict[str, Any], action: Action) -> tuple[ActionResult, dict[str, Any]]:
        valid, reason = self.validate_action(state, action)
        next_state = dict(state)
        next_state["timeline"] = list(state.get("timeline", []))
        next_state["side_effects"] = [dict(effect) for effect in state.get("side_effects", [])]
        next_state["compensations"] = [dict(comp) for comp in state.get("compensations", [])]
        if not valid:
            next_state["failed_actions"] = [*state.get("failed_actions", []), action.name]
            return ActionResult(success=False, output="", state_changes={}, error=reason), next_state
        next_state["completed_actions"] = [*state.get("completed_actions", []), action.name]
        next_state["completed_steps"] = [*state.get("completed_steps", []), action.name]
        next_state["timeline"].append({"action": action.name, "parameters": action.parameters})
        workflow_steps = {step.name: step for step in self.get_workflow_steps()}
        step = workflow_steps.get(action.name)
        if step is not None:
            next_state["side_effects"].append({"step_name": step.name, "effect_type": "workflow_step", "description": step.description, "reversible": step.reversible, "reversed": False})
        return (
            ActionResult(success=True, output=f"executed {action.name}", state_changes={"completed_actions": list(next_state["completed_actions"]), "completed_steps": list(next_state["completed_steps"])}, side_effects=[action.name]),
            next_state,
        )

    def is_terminal(self, state: dict[str, Any]) -> bool:
        required = set(${requiredActions})
        completed = set(state.get("completed_actions", []))
        return required.issubset(completed) or state.get("step", 0) >= ${spec.maxSteps}

    def execute_step(self, state: dict[str, Any], step: WorkflowStep) -> tuple[ActionResult, dict[str, Any]]:
        return self.execute_action(state, Action(name=step.name, parameters={}))

    def execute_compensation(self, state: dict[str, Any], step: WorkflowStep) -> CompensationAction:
        side_effects = [dict(effect) for effect in state.get("side_effects", [])]
        success = False
        for effect in side_effects:
            if effect["step_name"] == step.name and effect["reversible"] and not effect["reversed"]:
                effect["reversed"] = True
                success = True
        state["side_effects"] = side_effects
        state.setdefault("compensations", []).append({"step_name": step.name, "compensation_name": step.compensation or f"undo_{step.name}", "success": success, "output": "Compensation executed" if success else "No reversible side effect found"})
        return CompensationAction(step_name=step.name, compensation_name=step.compensation or f"undo_{step.name}", success=success, output="Compensation executed" if success else "No reversible side effect found")

    def get_side_effects(self, state: dict[str, Any]) -> list[SideEffect]:
        return [SideEffect(step_name=effect["step_name"], effect_type=effect["effect_type"], description=effect["description"], reversible=effect["reversible"], reversed=effect["reversed"]) for effect in state.get("side_effects", [])]

    def evaluate_workflow(self, state: dict[str, Any]) -> WorkflowResult:
        steps = self.get_workflow_steps()
        side_effects = self.get_side_effects(state)
        reversed_count = sum(1 for effect in side_effects if effect.reversed)
        leaked_count = sum(1 for effect in side_effects if effect.reversible and not effect.reversed)
        compensations = state.get("compensations", [])
        completion = len(state.get("completed_steps", [])) / len(steps) if steps else 1.0
        compensation_quality = (sum(1 for comp in compensations if comp.get("success")) / max(len(compensations), 1)) if compensations else (1.0 if leaked_count == 0 else 0.0)
        containment = 1.0 if leaked_count == 0 else max(0.0, 1.0 - (leaked_count / max(len(side_effects), 1)))
        score = round((completion * 0.5) + (compensation_quality * 0.3) + (containment * 0.2), 4)
        return WorkflowResult(score=score, reasoning=f"Completed {len(state.get('completed_steps', []))} of {len(steps)} workflow steps.", dimension_scores={"completeness": round(completion, 4), "compensation_quality": round(compensation_quality, 4), "side_effect_containment": round(containment, 4)}, steps_completed=len(state.get("completed_steps", [])), steps_total=len(steps), retries=sum(1 for action_name in state.get("failed_actions", []) if action_name in {step.name for step in steps}), compensations_triggered=len(compensations), compensations_successful=sum(1 for comp in compensations if comp.get("success")), side_effects=side_effects, side_effects_reversed=reversed_count, side_effects_leaked=leaked_count)

    def evaluate_trace(self, trace: ActionTrace, final_state: dict[str, Any]) -> SimulationResult:
        workflow = self.evaluate_workflow(final_state)
        return SimulationResult(score=workflow.score, reasoning=workflow.reasoning, dimension_scores={"completeness": workflow.dimension_scores["completeness"], "compensation_quality": workflow.dimension_scores["compensation_quality"], "side_effect_containment": workflow.dimension_scores["side_effect_containment"]}, workflow_complete=workflow.steps_completed == workflow.steps_total, actions_taken=len(trace.records), actions_successful=sum(1 for record in trace.records if record.result.success), recovery_attempts=workflow.retries, rollback_quality=workflow.dimension_scores["compensation_quality"])

    def get_rubric(self) -> str:
        return "Evaluate on workflow completeness, compensation quality, and side-effect containment."

    def max_steps(self) -> int:
        return ${spec.maxSteps}
`;
}

export class WorkflowCreator {
  private provider: LLMProvider;
  private model: string;
  private knowledgeRoot: string;

  constructor(opts: WorkflowCreatorOpts) {
    this.provider = opts.provider;
    this.model = opts.model ?? opts.provider.defaultModel();
    this.knowledgeRoot = opts.knowledgeRoot;
  }

  async create(description: string, name: string): Promise<WorkflowScenarioHandle> {
    const llmFn = async (system: string, user: string): Promise<string> => {
      const result = await this.provider.complete({
        systemPrompt: system,
        userPrompt: user,
        model: this.model,
      });
      return result.text;
    };
    const spec = await designWorkflow(description, llmFn);
    const errors = validateForFamily("workflow", spec);
    if (errors.length > 0) {
      throw new Error(`workflow spec validation failed: ${errors.join("; ")}`);
    }

    const customDir = join(this.knowledgeRoot, "_custom_scenarios");
    const scenarioDir = join(customDir, name);
    if (!existsSync(scenarioDir)) mkdirSync(scenarioDir, { recursive: true });

    writeFileSync(join(scenarioDir, "scenario.py"), generateScenarioSource(spec, name), "utf-8");
    writeFileSync(join(scenarioDir, "scenario_type.txt"), getScenarioTypeMarker("workflow"), "utf-8");
    writeFileSync(
      join(scenarioDir, "spec.json"),
      JSON.stringify(
        {
          name,
          scenario_type: getScenarioTypeMarker("workflow"),
          description: spec.description,
          environment_description: spec.environmentDescription,
          initial_state_description: spec.initialStateDescription,
          workflow_steps: spec.workflowSteps.map((step) => ({
            name: step.name,
            description: step.description,
            idempotent: step.idempotent,
            reversible: step.reversible,
            compensation: step.compensation ?? null,
          })),
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

    return { family: "workflow", name, spec };
  }
}
