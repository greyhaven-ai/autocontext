import type { ScenarioFamilyName } from "./families.js";
import { parseRawSpec } from "./agent-task-spec.js";
import { parseRawArtifactEditingSpec } from "./artifact-editing-spec.js";
import { parseRawCoordinationSpec } from "./coordination-spec.js";
import { parseRawInvestigationSpec } from "./investigation-spec.js";
import { parseRawNegotiationSpec } from "./negotiation-spec.js";
import { parseRawOperatorLoopSpec } from "./operator-loop-spec.js";
import { parseRawSchemaEvolutionSpec } from "./schema-evolution-spec.js";
import { parseRawSimulationSpec } from "./simulation-spec.js";
import { parseRawToolFragilitySpec } from "./tool-fragility-spec.js";
import { parseRawWorkflowSpec } from "./workflow-spec.js";

function pick(spec: Record<string, unknown>, ...keys: string[]): unknown {
  for (const key of keys) {
    if (key in spec && spec[key] !== undefined) {
      return spec[key];
    }
  }
  return undefined;
}

function normalizeActions(value: unknown): unknown {
  if (!Array.isArray(value)) return value;
  return value.map((action) => {
    const raw = action as Record<string, unknown>;
    return {
      name: pick(raw, "name"),
      description: pick(raw, "description"),
      parameters: pick(raw, "parameters") ?? {},
      preconditions: pick(raw, "preconditions") ?? [],
      effects: pick(raw, "effects") ?? [],
    };
  });
}

function normalizeArtifacts(value: unknown): unknown {
  if (!Array.isArray(value)) return value;
  return value.map((artifact) => {
    const raw = artifact as Record<string, unknown>;
    return {
      path: pick(raw, "path"),
      content: pick(raw, "content"),
      content_type: pick(raw, "content_type", "contentType"),
      metadata: pick(raw, "metadata") ?? {},
    };
  });
}

function normalizeWorkers(value: unknown): unknown {
  if (!Array.isArray(value)) return value;
  return value.map((worker) => {
    const raw = worker as Record<string, unknown>;
    return {
      worker_id: pick(raw, "worker_id", "workerId"),
      role: pick(raw, "role"),
    };
  });
}

function normalizeMutations(value: unknown): unknown {
  if (!Array.isArray(value)) return value;
  return value.map((mutation) => {
    const raw = mutation as Record<string, unknown>;
    return {
      version: pick(raw, "version"),
      description: pick(raw, "description"),
      breaking: pick(raw, "breaking"),
      fields_added: pick(raw, "fields_added", "fieldsAdded") ?? [],
      fields_removed: pick(raw, "fields_removed", "fieldsRemoved") ?? [],
      fields_modified: pick(raw, "fields_modified", "fieldsModified") ?? {},
    };
  });
}

function normalizeToolContracts(value: unknown): unknown {
  if (!Array.isArray(value)) return value;
  return value.map((toolContract) => {
    const raw = toolContract as Record<string, unknown>;
    return {
      tool_name: pick(raw, "tool_name", "toolName"),
      version: pick(raw, "version"),
      description: pick(raw, "description"),
    };
  });
}

function normalizeHiddenPreferences(value: unknown): unknown {
  if (value == null || typeof value !== "object") return value;
  const raw = value as Record<string, unknown>;
  return {
    priorities: pick(raw, "priorities"),
    reservation_value: pick(raw, "reservation_value", "reservationValue"),
    aspiration_value: pick(raw, "aspiration_value", "aspirationValue"),
    batna_description: pick(raw, "batna_description", "batnaDescription"),
  };
}

function normalizeEscalationPolicy(value: unknown): unknown {
  if (value == null || typeof value !== "object") return value;
  const raw = value as Record<string, unknown>;
  return {
    escalation_threshold: pick(raw, "escalation_threshold", "escalationThreshold"),
    max_escalations: pick(raw, "max_escalations", "maxEscalations"),
  };
}

export function normalizeScenarioRevisionSpec(
  family: string,
  spec: Record<string, unknown>,
): Record<string, unknown> {
  switch (family as ScenarioFamilyName) {
    case "agent_task": {
      const normalized = parseRawSpec({
        task_prompt: pick(spec, "task_prompt", "taskPrompt"),
        judge_rubric: pick(spec, "judge_rubric", "judgeRubric", "rubric"),
        output_format: pick(spec, "output_format", "outputFormat") ?? "free_text",
        judge_model: pick(spec, "judge_model", "judgeModel") ?? "",
        difficulty_tiers: pick(spec, "difficulty_tiers", "difficultyTiers") ?? null,
        reference_context: pick(spec, "reference_context", "referenceContext") ?? null,
        reference_sources: pick(spec, "reference_sources", "referenceSources") ?? null,
        required_concepts: pick(spec, "required_concepts", "requiredConcepts") ?? null,
        calibration_examples: pick(spec, "calibration_examples", "calibrationExamples") ?? null,
        context_preparation: pick(spec, "context_preparation", "contextPreparation") ?? null,
        required_context_keys: pick(spec, "required_context_keys", "requiredContextKeys") ?? null,
        max_rounds: pick(spec, "max_rounds", "maxRounds") ?? 1,
        quality_threshold: pick(spec, "quality_threshold", "qualityThreshold") ?? 0.9,
        revision_prompt: pick(spec, "revision_prompt", "revisionPrompt") ?? null,
        sample_input: pick(spec, "sample_input", "sampleInput") ?? null,
      });
      return {
        ...normalized,
        rubric: normalized.judgeRubric,
        ...(typeof pick(spec, "description") === "string"
          ? { description: pick(spec, "description") as string }
          : {}),
      };
    }
    case "simulation":
      return parseRawSimulationSpec({
        description: pick(spec, "description"),
        environment_description: pick(spec, "environment_description", "environmentDescription"),
        initial_state_description: pick(spec, "initial_state_description", "initialStateDescription"),
        success_criteria: pick(spec, "success_criteria", "successCriteria"),
        failure_modes: pick(spec, "failure_modes", "failureModes") ?? [],
        actions: normalizeActions(pick(spec, "actions")),
        max_steps: pick(spec, "max_steps", "maxSteps") ?? 10,
      });
    case "artifact_editing":
      return parseRawArtifactEditingSpec({
        task_description: pick(spec, "task_description", "taskDescription"),
        rubric: pick(spec, "rubric"),
        validation_rules: pick(spec, "validation_rules", "validationRules"),
        artifacts: normalizeArtifacts(pick(spec, "artifacts")),
      });
    case "investigation":
      return parseRawInvestigationSpec({
        description: pick(spec, "description"),
        environment_description: pick(spec, "environment_description", "environmentDescription"),
        initial_state_description: pick(spec, "initial_state_description", "initialStateDescription"),
        evidence_pool_description: pick(spec, "evidence_pool_description", "evidencePoolDescription"),
        diagnosis_target: pick(spec, "diagnosis_target", "diagnosisTarget"),
        success_criteria: pick(spec, "success_criteria", "successCriteria"),
        failure_modes: pick(spec, "failure_modes", "failureModes") ?? [],
        actions: normalizeActions(pick(spec, "actions")),
        max_steps: pick(spec, "max_steps", "maxSteps") ?? 10,
      });
    case "workflow":
      return parseRawWorkflowSpec({
        description: pick(spec, "description"),
        environment_description: pick(spec, "environment_description", "environmentDescription"),
        initial_state_description: pick(spec, "initial_state_description", "initialStateDescription"),
        workflow_steps: pick(spec, "workflow_steps", "workflowSteps"),
        success_criteria: pick(spec, "success_criteria", "successCriteria"),
        failure_modes: pick(spec, "failure_modes", "failureModes") ?? [],
        actions: normalizeActions(pick(spec, "actions")),
        max_steps: pick(spec, "max_steps", "maxSteps") ?? 10,
      });
    case "schema_evolution":
      return parseRawSchemaEvolutionSpec({
        description: pick(spec, "description"),
        environment_description: pick(spec, "environment_description", "environmentDescription"),
        initial_state_description: pick(spec, "initial_state_description", "initialStateDescription"),
        mutations: normalizeMutations(pick(spec, "mutations")),
        success_criteria: pick(spec, "success_criteria", "successCriteria"),
        failure_modes: pick(spec, "failure_modes", "failureModes") ?? [],
        actions: normalizeActions(pick(spec, "actions")),
        max_steps: pick(spec, "max_steps", "maxSteps") ?? 10,
      });
    case "tool_fragility":
      return parseRawToolFragilitySpec({
        description: pick(spec, "description"),
        environment_description: pick(spec, "environment_description", "environmentDescription"),
        initial_state_description: pick(spec, "initial_state_description", "initialStateDescription"),
        tool_contracts: normalizeToolContracts(pick(spec, "tool_contracts", "toolContracts")),
        success_criteria: pick(spec, "success_criteria", "successCriteria"),
        failure_modes: pick(spec, "failure_modes", "failureModes") ?? [],
        actions: normalizeActions(pick(spec, "actions")),
        max_steps: pick(spec, "max_steps", "maxSteps") ?? 10,
      });
    case "negotiation":
      return parseRawNegotiationSpec({
        description: pick(spec, "description"),
        environment_description: pick(spec, "environment_description", "environmentDescription"),
        initial_state_description: pick(spec, "initial_state_description", "initialStateDescription"),
        hidden_preferences: normalizeHiddenPreferences(
          pick(spec, "hidden_preferences", "hiddenPreferences"),
        ),
        max_rounds: pick(spec, "max_rounds", "maxRounds"),
        success_criteria: pick(spec, "success_criteria", "successCriteria"),
        failure_modes: pick(spec, "failure_modes", "failureModes") ?? [],
        actions: normalizeActions(pick(spec, "actions")),
        max_steps: pick(spec, "max_steps", "maxSteps") ?? 0,
      });
    case "operator_loop":
      return parseRawOperatorLoopSpec({
        description: pick(spec, "description"),
        environment_description: pick(spec, "environment_description", "environmentDescription"),
        initial_state_description: pick(spec, "initial_state_description", "initialStateDescription"),
        escalation_policy: normalizeEscalationPolicy(
          pick(spec, "escalation_policy", "escalationPolicy"),
        ),
        success_criteria: pick(spec, "success_criteria", "successCriteria"),
        failure_modes: pick(spec, "failure_modes", "failureModes") ?? [],
        actions: normalizeActions(pick(spec, "actions")),
        max_steps: pick(spec, "max_steps", "maxSteps") ?? 10,
      });
    case "coordination":
      return parseRawCoordinationSpec({
        description: pick(spec, "description"),
        environment_description: pick(spec, "environment_description", "environmentDescription"),
        initial_state_description: pick(spec, "initial_state_description", "initialStateDescription"),
        workers: normalizeWorkers(pick(spec, "workers")),
        success_criteria: pick(spec, "success_criteria", "successCriteria"),
        failure_modes: pick(spec, "failure_modes", "failureModes") ?? [],
        actions: normalizeActions(pick(spec, "actions")),
        max_steps: pick(spec, "max_steps", "maxSteps") ?? 10,
      });
    default:
      throw new Error(`Unsupported scenario family for revision: ${family}`);
  }
}
