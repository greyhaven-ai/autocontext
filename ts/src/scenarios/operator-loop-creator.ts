import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { LLMProvider } from "../types/index.js";
import { validateForFamily } from "./family-pipeline.js";
import { getScenarioTypeMarker } from "./families.js";
import type { OperatorLoopSpec } from "./operator-loop-spec.js";
import { designOperatorLoop } from "./operator-loop-designer.js";

export interface OperatorLoopCreatorOpts {
  provider: LLMProvider;
  model?: string;
  knowledgeRoot: string;
}

export interface OperatorLoopScenarioHandle {
  family: "operator_loop";
  name: string;
  spec: OperatorLoopSpec;
}

function className(name: string): string {
  return name
    .split(/[^a-zA-Z0-9]+/)
    .filter(Boolean)
    .map((part) => part[0]!.toUpperCase() + part.slice(1))
    .join("") + "OperatorLoop";
}

function generateScenarioSource(spec: OperatorLoopSpec, name: string): string {
  const actions = spec.actions
    .map((action) => `            ActionSpec(name=${JSON.stringify(action.name)}, description=${JSON.stringify(action.description)}, parameters=${JSON.stringify(action.parameters)}, preconditions=${JSON.stringify(action.preconditions)}, effects=${JSON.stringify(action.effects)})`)
    .join(",\n");
  const requiredActions = JSON.stringify(spec.actions.map((action) => action.name));
  const escalationPolicy = JSON.stringify({
    escalation_threshold: spec.escalationPolicy.escalationThreshold,
    max_escalations: spec.escalationPolicy.maxEscalations,
  });
  return `from __future__ import annotations

from typing import Any

from autocontext.scenarios.operator_loop import ClarificationRequest, EscalationEvent, OperatorLoopInterface, OperatorLoopResult
from autocontext.scenarios.simulation import Action, ActionResult, ActionSpec, ActionTrace, EnvironmentSpec, SimulationResult


class ${className(name)}(OperatorLoopInterface):
    name = ${JSON.stringify(name)}
    _escalation_policy = ${escalationPolicy}

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
        return {"seed": seed or 0, "step": 0, "completed_actions": [], "failed_actions": [], "escalations": [], "clarifications": [], "necessary_escalation_steps": []}

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
        return required.issubset(completed) or state.get("step", 0) >= ${spec.maxSteps}

    def get_escalation_log(self, state: dict[str, Any]) -> list[EscalationEvent]:
        return [EscalationEvent.from_dict(event) for event in state.get("escalations", [])]

    def get_clarification_log(self, state: dict[str, Any]) -> list[ClarificationRequest]:
        return [ClarificationRequest.from_dict(request) for request in state.get("clarifications", [])]

    def escalate(self, state: dict[str, Any], event: EscalationEvent) -> dict[str, Any]:
        next_state = dict(state)
        next_state["escalations"] = [*state.get("escalations", []), event.to_dict()]
        return next_state

    def request_clarification(self, state: dict[str, Any], request: ClarificationRequest) -> dict[str, Any]:
        next_state = dict(state)
        next_state["clarifications"] = [*state.get("clarifications", []), request.to_dict()]
        return next_state

    def evaluate_judgment(self, state: dict[str, Any]) -> OperatorLoopResult:
        escalations = state.get("escalations", [])
        clarifications = state.get("clarifications", [])
        total_actions = len(state.get("completed_actions", []))
        necessary_steps = set(state.get("necessary_escalation_steps", []))
        necessary = sum(1 for event in escalations if event.get("was_necessary", False))
        unnecessary = len(escalations) - necessary
        missed = len(necessary_steps - {event.get("step", -1) for event in escalations})
        failed = len(state.get("failed_actions", []))
        action_quality = total_actions / max(total_actions + failed, 1)
        if escalations or necessary_steps:
            over_penalty = unnecessary * 0.15
            under_penalty = missed * 0.2
            judgment = max(0.0, 1.0 - over_penalty - under_penalty)
        else:
            judgment = 1.0
        score = round(action_quality * 0.4 + judgment * 0.6, 4)
        return OperatorLoopResult(
            score=score,
            reasoning=f"{total_actions} actions, {len(escalations)} escalations ({necessary} necessary, {unnecessary} unnecessary), {missed} missed escalations.",
            dimension_scores={"action_quality": round(action_quality, 4), "escalation_judgment": round(judgment, 4)},
            total_actions=total_actions,
            escalations=len(escalations),
            necessary_escalations=necessary,
            unnecessary_escalations=unnecessary,
            missed_escalations=missed,
            clarifications_requested=len(clarifications),
        )

    def evaluate_trace(self, trace: ActionTrace, final_state: dict[str, Any]) -> SimulationResult:
        judgment = self.evaluate_judgment(final_state)
        action_success = trace.success_rate
        score = round(judgment.score * 0.7 + action_success * 0.3, 4)
        return SimulationResult(
            score=score,
            reasoning=judgment.reasoning,
            dimension_scores={"action_quality": judgment.dimension_scores.get("action_quality", 0.0), "escalation_judgment": judgment.dimension_scores.get("escalation_judgment", 0.0), "action_success": round(action_success, 4)},
            workflow_complete=self.is_terminal(final_state),
            actions_taken=len(trace.records),
            actions_successful=sum(1 for record in trace.records if record.result.success),
            recovery_attempts=judgment.unnecessary_escalations,
            rollback_quality=judgment.dimension_scores.get("escalation_judgment", 0.0),
        )

    def get_rubric(self) -> str:
        return "Evaluate on action completion quality, escalation judgment (avoiding both over- and under-escalation), and appropriate use of clarification requests."

    def max_steps(self) -> int:
        return ${spec.maxSteps}
`;
}

export class OperatorLoopCreator {
  private provider: LLMProvider;
  private model: string;
  private knowledgeRoot: string;

  constructor(opts: OperatorLoopCreatorOpts) {
    this.provider = opts.provider;
    this.model = opts.model ?? opts.provider.defaultModel();
    this.knowledgeRoot = opts.knowledgeRoot;
  }

  async create(description: string, name: string): Promise<OperatorLoopScenarioHandle> {
    const llmFn = async (system: string, user: string): Promise<string> => {
      const result = await this.provider.complete({
        systemPrompt: system,
        userPrompt: user,
        model: this.model,
      });
      return result.text;
    };
    const spec = await designOperatorLoop(description, llmFn);
    const errors = validateForFamily("operator_loop", spec);
    if (errors.length > 0) {
      throw new Error(`operator_loop spec validation failed: ${errors.join("; ")}`);
    }

    const customDir = join(this.knowledgeRoot, "_custom_scenarios");
    const scenarioDir = join(customDir, name);
    if (!existsSync(scenarioDir)) mkdirSync(scenarioDir, { recursive: true });

    writeFileSync(join(scenarioDir, "scenario.py"), generateScenarioSource(spec, name), "utf-8");
    writeFileSync(join(scenarioDir, "scenario_type.txt"), getScenarioTypeMarker("operator_loop"), "utf-8");
    writeFileSync(
      join(scenarioDir, "spec.json"),
      JSON.stringify(
        {
          name,
          scenario_type: getScenarioTypeMarker("operator_loop"),
          description: spec.description,
          environment_description: spec.environmentDescription,
          initial_state_description: spec.initialStateDescription,
          escalation_policy: {
            escalation_threshold: spec.escalationPolicy.escalationThreshold,
            max_escalations: spec.escalationPolicy.maxEscalations,
          },
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

    return { family: "operator_loop", name, spec };
  }
}
