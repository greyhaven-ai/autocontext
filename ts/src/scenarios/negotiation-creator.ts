import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { LLMProvider } from "../types/index.js";
import { validateForFamily } from "./family-pipeline.js";
import { getScenarioTypeMarker } from "./families.js";
import type { NegotiationSpec } from "./negotiation-spec.js";
import { designNegotiation } from "./negotiation-designer.js";

export interface NegotiationCreatorOpts {
  provider: LLMProvider;
  model?: string;
  knowledgeRoot: string;
}

export interface NegotiationScenarioHandle {
  family: "negotiation";
  name: string;
  spec: NegotiationSpec;
}

function className(name: string): string {
  return name
    .split(/[^a-zA-Z0-9]+/)
    .filter(Boolean)
    .map((part) => part[0]!.toUpperCase() + part.slice(1))
    .join("") + "Negotiation";
}

function generateScenarioSource(spec: NegotiationSpec, name: string): string {
  const actions = spec.actions
    .map((action) => `            ActionSpec(name=${JSON.stringify(action.name)}, description=${JSON.stringify(action.description)}, parameters=${JSON.stringify(action.parameters)}, preconditions=${JSON.stringify(action.preconditions)}, effects=${JSON.stringify(action.effects)})`)
    .join(",\n");
  const requiredActions = JSON.stringify(spec.actions.map((action) => action.name));
  const hiddenPrefs = JSON.stringify({
    priorities: spec.hiddenPreferences.priorities,
    reservation_value: spec.hiddenPreferences.reservationValue,
    aspiration_value: spec.hiddenPreferences.aspirationValue,
    batna_description: spec.hiddenPreferences.batnaDescription,
  });
  return `from __future__ import annotations

from typing import Any

from autocontext.scenarios.negotiation import HiddenPreferences, NegotiationInterface, NegotiationResult, NegotiationRound, OpponentModel
from autocontext.scenarios.simulation import Action, ActionResult, ActionSpec, ActionTrace, EnvironmentSpec, SimulationResult


class ${className(name)}(NegotiationInterface):
    name = ${JSON.stringify(name)}
    _hidden_prefs_spec = ${hiddenPrefs}

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
        return {
            "seed": seed or 0,
            "step": 0,
            "round": 0,
            "max_rounds": ${spec.maxRounds},
            "rounds": [],
            "completed_actions": [],
            "failed_actions": [],
            "opponent_model": None,
            "deal_value": None,
            "deal_closed": False,
        }

    def get_available_actions(self, state: dict[str, Any]) -> list[ActionSpec]:
        completed = set(state.get("completed_actions", []))
        return [s for s in self.describe_environment().available_actions if s.name not in completed]

    def validate_action(self, state: dict[str, Any], action: Action) -> tuple[bool, str]:
        specs = {s.name: s for s in self.describe_environment().available_actions}
        spec = specs.get(action.name)
        if spec is None:
            return False, f"unknown action: {action.name}"
        completed = set(state.get("completed_actions", []))
        for req in spec.preconditions:
            if req not in completed:
                return False, f"precondition not met for {action.name}: {req}"
        return True, ""

    def execute_action(self, state: dict[str, Any], action: Action) -> tuple[ActionResult, dict[str, Any]]:
        valid, reason = self.validate_action(state, action)
        next_state = dict(state)
        if not valid:
            next_state["failed_actions"] = [*state.get("failed_actions", []), action.name]
            return ActionResult(success=False, output="", state_changes={}, error=reason), next_state

        next_state["completed_actions"] = [*state.get("completed_actions", []), action.name]
        next_state["round"] = state.get("round", 0) + 1
        offer = action.parameters if action.parameters else {}
        rnd = {
            "round_number": next_state["round"],
            "offer": offer,
            "counter_offer": None,
            "accepted": action.name == "accept",
            "agent_reasoning": action.parameters.get("reasoning", "") if action.parameters else "",
        }
        next_state["rounds"] = [*state.get("rounds", []), rnd]

        if action.name == "accept":
            next_state["deal_closed"] = True
            rounds_used = next_state["round"]
            reservation = self._hidden_prefs_spec.get("reservation_value", 0.0)
            aspiration = self._hidden_prefs_spec.get("aspiration_value", 100.0)
            ratio = 1.0 - (rounds_used / max(state.get("max_rounds", ${spec.maxRounds}), 1))
            next_state["deal_value"] = round(reservation + ratio * (aspiration - reservation), 2)

        return (
            ActionResult(
                success=True,
                output=f"executed {action.name} (round {next_state['round']})",
                state_changes={"round": next_state["round"]},
            ),
            next_state,
        )

    def is_terminal(self, state: dict[str, Any]) -> bool:
        required = set(${requiredActions})
        completed = set(state.get("completed_actions", []))
        return state.get("deal_closed", False) or required.issubset(completed) or state.get("round", 0) >= state.get("max_rounds", ${spec.maxRounds}) or state.get("step", 0) >= ${spec.maxSteps}

    def get_hidden_preferences(self, state: dict[str, Any]) -> HiddenPreferences:
        del state
        return HiddenPreferences(
            priorities=self._hidden_prefs_spec.get("priorities", {}),
            reservation_value=self._hidden_prefs_spec.get("reservation_value", 0.0),
            aspiration_value=self._hidden_prefs_spec.get("aspiration_value", 100.0),
            batna_description=self._hidden_prefs_spec.get("batna_description", ""),
        )

    def get_rounds(self, state: dict[str, Any]) -> list[NegotiationRound]:
        return [NegotiationRound.from_dict(rnd) for rnd in state.get("rounds", [])]

    def get_opponent_model(self, state: dict[str, Any]) -> OpponentModel | None:
        raw = state.get("opponent_model")
        if raw is None:
            return None
        return OpponentModel.from_dict(raw)

    def update_opponent_model(self, state: dict[str, Any], model: OpponentModel) -> dict[str, Any]:
        next_state = dict(state)
        next_state["opponent_model"] = model.to_dict()
        return next_state

    def evaluate_negotiation(self, state: dict[str, Any]) -> NegotiationResult:
        prefs = self.get_hidden_preferences(state)
        deal_value = state.get("deal_value") or 0.0
        rounds_used = state.get("round", 0)
        max_rounds = state.get("max_rounds", ${spec.maxRounds})
        surplus = prefs.aspiration_value - prefs.reservation_value
        value_ratio = max(0.0, (deal_value - prefs.reservation_value) / surplus) if surplus > 0 else (1.0 if deal_value >= prefs.reservation_value else 0.0)
        efficiency = 1.0 - (rounds_used / max(max_rounds, 1))
        opponent_model = self.get_opponent_model(state)
        if opponent_model is not None:
            diffs = [abs(actual - opponent_model.inferred_priorities.get(dim, 0.0)) for dim, actual in prefs.priorities.items()]
            model_accuracy = max(0.0, 1.0 - (sum(diffs) / max(len(diffs), 1)))
        else:
            model_accuracy = 0.0
        adaptation = min(1.0, len(state.get("rounds", [])) * 0.2)
        score = round(value_ratio * 0.35 + model_accuracy * 0.25 + efficiency * 0.2 + adaptation * 0.2, 4)
        return NegotiationResult(
            score=score,
            reasoning=f"Deal value {deal_value} ({rounds_used}/{max_rounds} rounds). Model accuracy: {model_accuracy:.2f}.",
            dimension_scores={"deal_quality": round(value_ratio, 4), "opponent_modeling": round(model_accuracy, 4), "efficiency": round(efficiency, 4), "adaptation": round(adaptation, 4)},
            deal_value=deal_value,
            rounds_used=rounds_used,
            max_rounds=max_rounds,
            opponent_model_accuracy=round(model_accuracy, 4),
            value_claimed_ratio=round(value_ratio, 4),
        )

    def evaluate_trace(self, trace: ActionTrace, final_state: dict[str, Any]) -> SimulationResult:
        negotiation = self.evaluate_negotiation(final_state)
        action_success = trace.success_rate
        score = round(negotiation.score * 0.7 + action_success * 0.3, 4)
        return SimulationResult(
            score=score,
            reasoning=negotiation.reasoning,
            dimension_scores={"deal_quality": negotiation.dimension_scores.get("deal_quality", 0.0), "opponent_modeling": negotiation.dimension_scores.get("opponent_modeling", 0.0), "efficiency": negotiation.dimension_scores.get("efficiency", 0.0), "adaptation": negotiation.dimension_scores.get("adaptation", 0.0), "action_success": round(action_success, 4)},
            workflow_complete=final_state.get("deal_closed", False),
            actions_taken=len(trace.records),
            actions_successful=sum(1 for record in trace.records if record.result.success),
            recovery_attempts=len(final_state.get("failed_actions", [])),
            rollback_quality=negotiation.dimension_scores.get("efficiency", 0.0),
        )

    def get_rubric(self) -> str:
        return "Evaluate on deal quality relative to BATNA, opponent modeling accuracy, negotiation efficiency, and strategic adaptation across rounds."

    def max_steps(self) -> int:
        return ${spec.maxSteps}
`;
}

export class NegotiationCreator {
  private provider: LLMProvider;
  private model: string;
  private knowledgeRoot: string;

  constructor(opts: NegotiationCreatorOpts) {
    this.provider = opts.provider;
    this.model = opts.model ?? opts.provider.defaultModel();
    this.knowledgeRoot = opts.knowledgeRoot;
  }

  async create(description: string, name: string): Promise<NegotiationScenarioHandle> {
    const llmFn = async (system: string, user: string): Promise<string> => {
      const result = await this.provider.complete({
        systemPrompt: system,
        userPrompt: user,
        model: this.model,
      });
      return result.text;
    };
    const spec = await designNegotiation(description, llmFn);
    const errors = validateForFamily("negotiation", spec);
    if (errors.length > 0) {
      throw new Error(`negotiation spec validation failed: ${errors.join("; ")}`);
    }

    const customDir = join(this.knowledgeRoot, "_custom_scenarios");
    const scenarioDir = join(customDir, name);
    if (!existsSync(scenarioDir)) mkdirSync(scenarioDir, { recursive: true });

    writeFileSync(join(scenarioDir, "scenario.py"), generateScenarioSource(spec, name), "utf-8");
    writeFileSync(join(scenarioDir, "scenario_type.txt"), getScenarioTypeMarker("negotiation"), "utf-8");
    writeFileSync(
      join(scenarioDir, "spec.json"),
      JSON.stringify(
        {
          name,
          scenario_type: getScenarioTypeMarker("negotiation"),
          description: spec.description,
          environment_description: spec.environmentDescription,
          initial_state_description: spec.initialStateDescription,
          hidden_preferences: {
            priorities: spec.hiddenPreferences.priorities,
            reservation_value: spec.hiddenPreferences.reservationValue,
            aspiration_value: spec.hiddenPreferences.aspirationValue,
            batna_description: spec.hiddenPreferences.batnaDescription,
          },
          max_rounds: spec.maxRounds,
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

    return { family: "negotiation", name, spec };
  }
}
