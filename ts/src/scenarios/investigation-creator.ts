import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { LLMProvider } from "../types/index.js";
import { validateForFamily } from "./family-pipeline.js";
import { getScenarioTypeMarker } from "./families.js";
import type { InvestigationSpec } from "./investigation-spec.js";
import { designInvestigation } from "./investigation-designer.js";

export interface InvestigationCreatorOpts {
  provider: LLMProvider;
  model?: string;
  knowledgeRoot: string;
}

export interface InvestigationScenarioHandle {
  family: "investigation";
  name: string;
  spec: InvestigationSpec;
}

function className(name: string): string {
  return name.split(/[^a-zA-Z0-9]+/).filter(Boolean).map((part) => part[0]!.toUpperCase() + part.slice(1)).join("") + "Investigation";
}

function generateScenarioSource(spec: InvestigationSpec, name: string): string {
  const actions = spec.actions
    .map((action) => `            ActionSpec(name=${JSON.stringify(action.name)}, description=${JSON.stringify(action.description)}, parameters=${JSON.stringify(action.parameters)}, preconditions=${JSON.stringify(action.preconditions)}, effects=${JSON.stringify(action.effects)})`)
    .join(",\n");
  const requiredActions = JSON.stringify(spec.actions.map((action) => action.name));
  const evidenceItems = JSON.stringify([
    {
      id: "evidence_logs",
      content: `Primary evidence: ${spec.evidencePoolDescription}`,
      source: "logs",
      relevance: 0.95,
      is_red_herring: false,
    },
    {
      id: "evidence_metrics",
      content: `Corroborating signal for diagnosis target: ${spec.diagnosisTarget}`,
      source: "metrics",
      relevance: 0.85,
      is_red_herring: false,
    },
    {
      id: "red_herring",
      content: "Red herring: an unrelated background job appears suspicious but does not explain the incident.",
      source: "cron_logs",
      relevance: 0.15,
      is_red_herring: true,
    },
  ]);
  return `from __future__ import annotations

from typing import Any

from autocontext.scenarios.investigation import EvidenceChain, EvidenceItem, InvestigationInterface, InvestigationResult
from autocontext.scenarios.simulation import Action, ActionResult, ActionSpec, ActionTrace, EnvironmentSpec, SimulationResult


class ${className(name)}(InvestigationInterface):
    name = ${JSON.stringify(name)}
    _diagnosis_target = ${JSON.stringify(spec.diagnosisTarget)}
    _evidence_items = ${evidenceItems}

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
        return {"seed": seed or 0, "step": 0, "completed_actions": [], "failed_actions": [], "timeline": [], "collected_evidence_ids": [], "diagnosis": ""}

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

    def _ordered_evidence(self) -> list[EvidenceItem]:
        return [
            EvidenceItem(id=item["id"], content=item["content"], source=item["source"], relevance=item["relevance"], is_red_herring=item["is_red_herring"])
            for item in self._evidence_items
        ]

    def execute_action(self, state: dict[str, Any], action: Action) -> tuple[ActionResult, dict[str, Any]]:
        valid, reason = self.validate_action(state, action)
        next_state = dict(state)
        next_state["timeline"] = list(state.get("timeline", []))
        next_state["collected_evidence_ids"] = list(state.get("collected_evidence_ids", []))
        if not valid:
            next_state["failed_actions"] = [*state.get("failed_actions", []), action.name]
            return ActionResult(success=False, output="", state_changes={}, error=reason), next_state
        next_state["completed_actions"] = [*state.get("completed_actions", []), action.name]
        next_state["timeline"].append({"action": action.name, "parameters": action.parameters})
        evidence_pool = self._ordered_evidence()
        collected = set(next_state["collected_evidence_ids"])
        for item in evidence_pool:
            if item.id not in collected:
                next_state["collected_evidence_ids"].append(item.id)
                break
        if "diagnosis" in action.parameters:
            next_state["diagnosis"] = str(action.parameters["diagnosis"])
        elif "diagnos" in action.name:
            next_state["diagnosis"] = self._diagnosis_target
        return (
            ActionResult(success=True, output=f"executed {action.name}", state_changes={"completed_actions": list(next_state["completed_actions"]), "collected_evidence_ids": list(next_state["collected_evidence_ids"]), "diagnosis": next_state.get("diagnosis", "")}, side_effects=[action.name]),
            next_state,
        )

    def is_terminal(self, state: dict[str, Any]) -> bool:
        required = set(${requiredActions})
        completed = set(state.get("completed_actions", []))
        return bool(state.get("diagnosis")) or required.issubset(completed) or state.get("step", 0) >= ${spec.maxSteps}

    def get_evidence_pool(self, state: dict[str, Any]) -> list[EvidenceItem]:
        del state
        return self._ordered_evidence()

    def evaluate_evidence_chain(self, chain: EvidenceChain, state: dict[str, Any]) -> float:
        del state
        if not chain.items:
            return 0.0
        average_relevance = sum(item.relevance for item in chain.items) / len(chain.items)
        red_herring_penalty = 0.35 if chain.contains_red_herring else 0.0
        reasoning_bonus = 0.1 if chain.reasoning.strip() else 0.0
        return max(0.0, min(1.0, average_relevance - red_herring_penalty + reasoning_bonus))

    def evaluate_diagnosis(self, diagnosis: str, evidence_chain: EvidenceChain, state: dict[str, Any]) -> InvestigationResult:
        del state
        diagnosis_normalized = diagnosis.strip().lower()
        target_normalized = self._diagnosis_target.strip().lower()
        diagnosis_correct = diagnosis_normalized == target_normalized or target_normalized in diagnosis_normalized
        evidence_quality = self.evaluate_evidence_chain(evidence_chain, {})
        red_followed = sum(1 for item in evidence_chain.items if item.is_red_herring)
        red_avoided = max(sum(1 for item in self._ordered_evidence() if item.is_red_herring) - red_followed, 0)
        score = round((0.55 if diagnosis_correct else 0.15) + (evidence_quality * 0.45), 4)
        return InvestigationResult(
            score=min(score, 1.0),
            reasoning="Diagnosis matched ground truth." if diagnosis_correct else "Diagnosis did not match ground truth.",
            dimension_scores={"diagnosis_accuracy": 1.0 if diagnosis_correct else 0.0, "evidence_quality": round(evidence_quality, 4), "red_herring_avoidance": 1.0 if red_followed == 0 else max(0.0, 1.0 - (red_followed / max(len(evidence_chain.items), 1)))},
            diagnosis=diagnosis,
            evidence_collected=len(evidence_chain.items),
            red_herrings_avoided=red_avoided,
            red_herrings_followed=red_followed,
            diagnosis_correct=diagnosis_correct,
        )

    def evaluate_trace(self, trace: ActionTrace, final_state: dict[str, Any]) -> SimulationResult:
        evidence_by_id = {item.id: item for item in self._ordered_evidence()}
        chain = EvidenceChain(items=[evidence_by_id[eid] for eid in final_state.get("collected_evidence_ids", []) if eid in evidence_by_id], reasoning="Derived from collected evidence during the trace.")
        diagnosis = str(final_state.get("diagnosis", "") or self._diagnosis_target)
        diagnosis_result = self.evaluate_diagnosis(diagnosis, chain, final_state)
        action_success = trace.success_rate
        score = round((diagnosis_result.score * 0.7) + (action_success * 0.3), 4)
        return SimulationResult(
            score=score,
            reasoning=f"Collected {diagnosis_result.evidence_collected} evidence items and produced diagnosis '{diagnosis}'.",
            dimension_scores={"evidence_quality": round(diagnosis_result.dimension_scores["evidence_quality"], 4), "diagnosis_accuracy": round(diagnosis_result.dimension_scores["diagnosis_accuracy"], 4), "action_success": round(action_success, 4)},
            workflow_complete=diagnosis_result.diagnosis_correct,
            actions_taken=len(trace.records),
            actions_successful=sum(1 for record in trace.records if record.result.success),
            recovery_attempts=sum(1 for record in trace.records if not record.result.success),
            rollback_quality=diagnosis_result.dimension_scores["red_herring_avoidance"],
        )

    def get_rubric(self) -> str:
        return "Evaluate on evidence quality, red herring avoidance, and diagnosis accuracy."

    def max_steps(self) -> int:
        return ${spec.maxSteps}
`;
}

export class InvestigationCreator {
  private provider: LLMProvider;
  private model: string;
  private knowledgeRoot: string;

  constructor(opts: InvestigationCreatorOpts) {
    this.provider = opts.provider;
    this.model = opts.model ?? opts.provider.defaultModel();
    this.knowledgeRoot = opts.knowledgeRoot;
  }

  async create(description: string, name: string): Promise<InvestigationScenarioHandle> {
    const llmFn = async (system: string, user: string): Promise<string> => {
      const result = await this.provider.complete({
        systemPrompt: system,
        userPrompt: user,
        model: this.model,
      });
      return result.text;
    };
    const spec = await designInvestigation(description, llmFn);
    const errors = validateForFamily("investigation", spec);
    if (errors.length > 0) {
      throw new Error(`investigation spec validation failed: ${errors.join("; ")}`);
    }

    const customDir = join(this.knowledgeRoot, "_custom_scenarios");
    const scenarioDir = join(customDir, name);
    if (!existsSync(scenarioDir)) mkdirSync(scenarioDir, { recursive: true });

    writeFileSync(join(scenarioDir, "scenario.py"), generateScenarioSource(spec, name), "utf-8");
    writeFileSync(join(scenarioDir, "scenario_type.txt"), getScenarioTypeMarker("investigation"), "utf-8");
    writeFileSync(
      join(scenarioDir, "spec.json"),
      JSON.stringify(
        {
          name,
          scenario_type: getScenarioTypeMarker("investigation"),
          description: spec.description,
          environment_description: spec.environmentDescription,
          initial_state_description: spec.initialStateDescription,
          evidence_pool_description: spec.evidencePoolDescription,
          diagnosis_target: spec.diagnosisTarget,
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

    return { family: "investigation", name, spec };
  }
}
