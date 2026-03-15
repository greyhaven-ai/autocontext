import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { LLMProvider } from "../types/index.js";
import { validateForFamily } from "./family-pipeline.js";
import { getScenarioTypeMarker } from "./families.js";
import type { CoordinationSpec } from "./coordination-spec.js";
import { designCoordination } from "./coordination-designer.js";

export interface CoordinationCreatorOpts {
  provider: LLMProvider;
  model?: string;
  knowledgeRoot: string;
}

export interface CoordinationScenarioHandle {
  family: "coordination";
  name: string;
  spec: CoordinationSpec;
}

function className(name: string): string {
  return name
    .split(/[^a-zA-Z0-9]+/)
    .filter(Boolean)
    .map((part) => part[0]!.toUpperCase() + part.slice(1))
    .join("") + "Coordination";
}

function generateScenarioSource(spec: CoordinationSpec, name: string): string {
  const actions = spec.actions
    .map((action) => `            ActionSpec(name=${JSON.stringify(action.name)}, description=${JSON.stringify(action.description)}, parameters=${JSON.stringify(action.parameters)}, preconditions=${JSON.stringify(action.preconditions)}, effects=${JSON.stringify(action.effects)})`)
    .join(",\n");
  const requiredActions = JSON.stringify(spec.actions.map((action) => action.name));
  const workers = JSON.stringify(spec.workers.map((worker) => ({ worker_id: worker.workerId, role: worker.role })));
  return `from __future__ import annotations

from typing import Any

from autocontext.scenarios.coordination import CoordinationInterface, CoordinationResult, HandoffRecord, WorkerContext
from autocontext.scenarios.simulation import Action, ActionResult, ActionSpec, ActionTrace, EnvironmentSpec, SimulationResult


class ${className(name)}(CoordinationInterface):
    name = ${JSON.stringify(name)}
    _workers_spec = ${workers}

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
        return {"seed": seed or 0, "step": 0, "completed_actions": [], "failed_actions": [], "handoffs": [], "worker_outputs": {}, "merged": False, "merge_conflicts": 0}

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
        if not valid:
            next_state["failed_actions"] = [*state.get("failed_actions", []), action.name]
            return ActionResult(success=False, output="", state_changes={}, error=reason), next_state
        next_state["completed_actions"] = [*state.get("completed_actions", []), action.name]
        next_state["step"] = state.get("step", 0) + 1
        return (
            ActionResult(success=True, output=f"executed {action.name}", state_changes={"completed_actions": list(next_state["completed_actions"])}),
            next_state,
        )

    def is_terminal(self, state: dict[str, Any]) -> bool:
        required = set(${requiredActions})
        completed = set(state.get("completed_actions", []))
        return required.issubset(completed) or state.get("merged", False) or state.get("step", 0) >= ${spec.maxSteps}

    def get_worker_contexts(self, state: dict[str, Any]) -> list[WorkerContext]:
        del state
        return [WorkerContext(worker_id=worker["worker_id"], role=worker.get("role", "worker"), context_partition={}, visible_data=[]) for worker in self._workers_spec]

    def get_handoff_log(self, state: dict[str, Any]) -> list[HandoffRecord]:
        return [HandoffRecord.from_dict(handoff) for handoff in state.get("handoffs", [])]

    def record_handoff(self, state: dict[str, Any], handoff: HandoffRecord) -> dict[str, Any]:
        next_state = dict(state)
        next_state["handoffs"] = [*state.get("handoffs", []), handoff.to_dict()]
        return next_state

    def merge_outputs(self, state: dict[str, Any], worker_outputs: dict[str, str]) -> dict[str, Any]:
        next_state = dict(state)
        next_state["worker_outputs"] = worker_outputs
        next_state["merged"] = True
        values = list(worker_outputs.values())
        conflicts = 0
        for index, value in enumerate(values):
            for other in values[index + 1:]:
                if value == other and value:
                    conflicts += 1
        next_state["merge_conflicts"] = conflicts
        return next_state

    def evaluate_coordination(self, state: dict[str, Any]) -> CoordinationResult:
        handoffs = state.get("handoffs", [])
        worker_outputs = state.get("worker_outputs", {})
        workers_used = len(worker_outputs) or len(self._workers_spec)
        merge_conflicts = state.get("merge_conflicts", 0)
        values = list(worker_outputs.values())
        if len(values) > 1:
            unique = len(set(value for value in values if value))
            total = len([value for value in values if value])
            duplication_rate = 1.0 - (unique / max(total, 1)) if total > 0 else 0.0
        else:
            duplication_rate = 0.0
        avg_handoff = (sum(handoff.get("quality", 0.5) for handoff in handoffs) / len(handoffs)) if handoffs else 0.5
        merge_quality = max(0.0, 1.0 - merge_conflicts * 0.2)
        completed = len(state.get("completed_actions", []))
        failed = len(state.get("failed_actions", []))
        outcome_quality = completed / max(completed + failed, 1)
        duplication_avoidance = max(0.0, 1.0 - duplication_rate)
        score = round(duplication_avoidance * 0.25 + avg_handoff * 0.25 + merge_quality * 0.25 + outcome_quality * 0.25, 4)
        return CoordinationResult(
            score=score,
            reasoning=f"{workers_used} workers, {len(handoffs)} handoffs, duplication rate {duplication_rate:.2f}, {merge_conflicts} merge conflicts.",
            dimension_scores={"duplication_avoidance": round(duplication_avoidance, 4), "handoff_quality": round(avg_handoff, 4), "merge_quality": round(merge_quality, 4), "outcome_quality": round(outcome_quality, 4)},
            workers_used=workers_used,
            handoffs_completed=len(handoffs),
            duplication_rate=round(duplication_rate, 4),
            merge_conflicts=merge_conflicts,
        )

    def evaluate_trace(self, trace: ActionTrace, final_state: dict[str, Any]) -> SimulationResult:
        coordination = self.evaluate_coordination(final_state)
        action_success = trace.success_rate
        score = round(coordination.score * 0.7 + action_success * 0.3, 4)
        return SimulationResult(
            score=score,
            reasoning=coordination.reasoning,
            dimension_scores={"duplication_avoidance": coordination.dimension_scores.get("duplication_avoidance", 0.0), "handoff_quality": coordination.dimension_scores.get("handoff_quality", 0.0), "merge_quality": coordination.dimension_scores.get("merge_quality", 0.0), "outcome_quality": coordination.dimension_scores.get("outcome_quality", 0.0), "action_success": round(action_success, 4)},
            workflow_complete=final_state.get("merged", False),
            actions_taken=len(trace.records),
            actions_successful=sum(1 for record in trace.records if record.result.success),
            recovery_attempts=coordination.merge_conflicts,
            rollback_quality=coordination.dimension_scores.get("merge_quality", 0.0),
        )

    def get_rubric(self) -> str:
        return "Evaluate on duplication avoidance, handoff quality, merge quality, and overall outcome quality."

    def max_steps(self) -> int:
        return ${spec.maxSteps}
`;
}

export class CoordinationCreator {
  private provider: LLMProvider;
  private model: string;
  private knowledgeRoot: string;

  constructor(opts: CoordinationCreatorOpts) {
    this.provider = opts.provider;
    this.model = opts.model ?? opts.provider.defaultModel();
    this.knowledgeRoot = opts.knowledgeRoot;
  }

  async create(description: string, name: string): Promise<CoordinationScenarioHandle> {
    const llmFn = async (system: string, user: string): Promise<string> => {
      const result = await this.provider.complete({
        systemPrompt: system,
        userPrompt: user,
        model: this.model,
      });
      return result.text;
    };
    const spec = await designCoordination(description, llmFn);
    const errors = validateForFamily("coordination", spec);
    if (errors.length > 0) {
      throw new Error(`coordination spec validation failed: ${errors.join("; ")}`);
    }

    const customDir = join(this.knowledgeRoot, "_custom_scenarios");
    const scenarioDir = join(customDir, name);
    if (!existsSync(scenarioDir)) mkdirSync(scenarioDir, { recursive: true });

    writeFileSync(join(scenarioDir, "scenario.py"), generateScenarioSource(spec, name), "utf-8");
    writeFileSync(join(scenarioDir, "scenario_type.txt"), getScenarioTypeMarker("coordination"), "utf-8");
    writeFileSync(
      join(scenarioDir, "spec.json"),
      JSON.stringify(
        {
          name,
          scenario_type: getScenarioTypeMarker("coordination"),
          description: spec.description,
          environment_description: spec.environmentDescription,
          initial_state_description: spec.initialStateDescription,
          workers: spec.workers.map((worker) => ({ worker_id: worker.workerId, role: worker.role })),
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

    return { family: "coordination", name, spec };
  }
}
