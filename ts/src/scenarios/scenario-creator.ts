/**
 * NL → Scenario creation flow (AC-348 Task 30).
 * Converts a natural language description into a scenario spec via LLM.
 */

import type { LLMProvider } from "../types/index.js";
import { designCoordination } from "./coordination-designer.js";
import type { CoordinationSpec } from "./coordination-spec.js";
import { classifyScenarioFamily, routeToFamily } from "./family-classifier.js";
import { SCENARIO_TYPE_MARKERS, type ScenarioFamilyName } from "./families.js";
import { designInvestigation } from "./investigation-designer.js";
import type { InvestigationSpec } from "./investigation-spec.js";
import { designNegotiation } from "./negotiation-designer.js";
import type { NegotiationSpec } from "./negotiation-spec.js";
import { designOperatorLoop } from "./operator-loop-designer.js";
import type { OperatorLoopSpec } from "./operator-loop-spec.js";
import { designSchemaEvolution } from "./schema-evolution-designer.js";
import type { SchemaEvolutionSpec } from "./schema-evolution-spec.js";
import { designSimulation } from "./simulation-designer.js";
import type { SimulationSpec } from "./simulation-spec.js";
import { healSpec } from "./spec-auto-heal.js";
import { designWorkflow } from "./workflow-designer.js";
import type { WorkflowSpec } from "./workflow-spec.js";

export interface CreatedScenarioResult {
  name: string;
  family: string;
  spec: {
    taskPrompt: string;
    rubric: string;
    description: string;
    [key: string]: unknown;
  };
}

type LlmFn = (system: string, user: string) => Promise<string>;
type FamilyAwareScenarioFamily =
  | "coordination"
  | "investigation"
  | "negotiation"
  | "operator_loop"
  | "schema_evolution"
  | "simulation"
  | "workflow";

type SimulationLikeCreatedSpecInput = {
  descriptionPrompt: string;
  rubric: string;
  scenarioDescription: string;
  environmentDescription: string;
  initialStateDescription: string;
  successCriteria: string[];
  failureModes: string[];
  actions: unknown[];
  maxSteps: number;
  extras?: Record<string, unknown>;
};

/**
 * Derive a snake_case scenario name from a description.
 */
export function deriveScenarioName(description: string): string {
  return (
    description
      .toLowerCase()
      .replace(/[^a-z0-9\s]/g, "")
      .split(/\s+/)
      .filter((w) => w.length > 2)
      .slice(0, 4)
      .join("_") || "custom_task"
  );
}

/**
 * Detect the most likely scenario family from a description.
 *
 * Delegates to the full `classifyScenarioFamily` weighted classifier
 * and returns just the family name for the custom-scenario creation path.
 *
 * `game` is intentionally excluded here because free-form game creation is not
 * a supported custom-scenario surface yet; letting NL creation auto-route into
 * `game` turns ordinary CLI requests into dead-end failures downstream.
 *
 * @see classifyScenarioFamily for the full classification with confidence scores
 */
export function detectScenarioFamily(description: string): ScenarioFamilyName {
  if (!description.trim()) return "agent_task";
  try {
    const family = routeToFamily(classifyScenarioFamily(description), 0.15);
    return family === "game" ? "agent_task" : family;
  } catch {
    // LowConfidenceError — fall back to agent_task
    return "agent_task";
  }
}

export function isScenarioFamilyName(value: string): value is ScenarioFamilyName {
  return value in SCENARIO_TYPE_MARKERS;
}

function scenarioCreationInstructions(): string {
  const familyNames = Object.keys(SCENARIO_TYPE_MARKERS)
    .filter((family) => family !== "game")
    .sort()
    .join(", ");
  return [
    "You are a scenario designer for an agent evaluation harness.",
    "Given a user's description, generate a JSON spec with these fields:",
    "  - name: a short snake_case scenario identifier",
    `  - family: the best-fit scenario family (${familyNames})`,
    "  - taskPrompt: the task the agent will be given",
    "  - rubric: evaluation criteria for judging the output",
    "  - description: a brief description of what the scenario tests",
    "Respond with ONLY the JSON object, no markdown fences.",
  ].join("\n");
}

export function buildScenarioCreationPrompt(description: string): string {
  return [scenarioCreationInstructions(), "", `User description: ${description}`].join("\n");
}

function createProviderLlmFn(provider: LLMProvider): LlmFn {
  return async (system: string, user: string): Promise<string> => {
    const result = await provider.complete({
      systemPrompt: system,
      userPrompt: user,
    });
    return result.text;
  };
}

function hasFamilyAwareScenarioFactory(
  family: ScenarioFamilyName,
): family is FamilyAwareScenarioFamily {
  return family in FAMILY_AWARE_SCENARIO_FACTORIES;
}

function buildSimulationLikeCreatedSpec(
  input: SimulationLikeCreatedSpecInput,
): CreatedScenarioResult["spec"] {
  return {
    taskPrompt: input.descriptionPrompt,
    rubric: input.rubric,
    description: input.scenarioDescription,
    environment_description: input.environmentDescription,
    initial_state_description: input.initialStateDescription,
    success_criteria: input.successCriteria,
    failure_modes: input.failureModes,
    actions: input.actions,
    max_steps: input.maxSteps,
    ...input.extras,
  };
}

function buildSimulationCreatedSpec(
  description: string,
  spec: SimulationSpec,
): CreatedScenarioResult["spec"] {
  return buildSimulationLikeCreatedSpec({
    descriptionPrompt: description,
    rubric: "Evaluate action sequencing, state progression, recovery, and completion quality.",
    scenarioDescription: spec.description,
    environmentDescription: spec.environmentDescription,
    initialStateDescription: spec.initialStateDescription,
    successCriteria: spec.successCriteria,
    failureModes: spec.failureModes,
    actions: spec.actions,
    maxSteps: spec.maxSteps,
  });
}

function buildInvestigationCreatedSpec(
  description: string,
  spec: InvestigationSpec,
): CreatedScenarioResult["spec"] {
  return buildSimulationLikeCreatedSpec({
    descriptionPrompt: description,
    rubric: "Evaluate evidence gathering, diagnosis accuracy, and red-herring resistance.",
    scenarioDescription: spec.description,
    environmentDescription: spec.environmentDescription,
    initialStateDescription: spec.initialStateDescription,
    successCriteria: spec.successCriteria,
    failureModes: spec.failureModes,
    actions: spec.actions,
    maxSteps: spec.maxSteps,
    extras: {
      evidence_pool_description: spec.evidencePoolDescription,
      diagnosis_target: spec.diagnosisTarget,
      evidencePool: [
        {
          id: "investigation_brief",
          content: spec.evidencePoolDescription,
          isRedHerring: false,
          relevance: 1,
        },
      ],
      correctDiagnosis: spec.diagnosisTarget,
    },
  });
}

function buildSchemaEvolutionCreatedSpec(
  description: string,
  spec: SchemaEvolutionSpec,
): CreatedScenarioResult["spec"] {
  return buildSimulationLikeCreatedSpec({
    descriptionPrompt: description,
    rubric: "Evaluate breaking-change detection, stale-assumption recovery, and adaptation speed.",
    scenarioDescription: spec.description,
    environmentDescription: spec.environmentDescription,
    initialStateDescription: spec.initialStateDescription,
    successCriteria: spec.successCriteria,
    failureModes: spec.failureModes,
    actions: spec.actions,
    maxSteps: spec.maxSteps,
    extras: {
      mutations: spec.mutations.map((mutation) => ({
        version: mutation.version,
        description: mutation.description,
        breaking: mutation.breaking,
        fields_added: mutation.fieldsAdded,
        fields_removed: mutation.fieldsRemoved,
        fields_modified: mutation.fieldsModified,
      })),
    },
  });
}

function buildWorkflowCreatedSpec(
  description: string,
  spec: WorkflowSpec,
): CreatedScenarioResult["spec"] {
  const actionsByName = new Map(spec.actions.map((action) => [action.name, action]));
  return buildSimulationLikeCreatedSpec({
    descriptionPrompt: description,
    rubric: "Evaluate workflow ordering, compensation logic, and side-effect handling.",
    scenarioDescription: spec.description,
    environmentDescription: spec.environmentDescription,
    initialStateDescription: spec.initialStateDescription,
    successCriteria: spec.successCriteria,
    failureModes: spec.failureModes,
    actions: spec.actions,
    maxSteps: spec.maxSteps,
    extras: {
      workflow_steps: spec.workflowSteps.map((step) => ({
        ...step,
        compensationAction: step.compensation ?? undefined,
        sideEffects: actionsByName.get(step.name)?.effects ?? [],
      })),
    },
  });
}

function buildNegotiationCreatedSpec(
  description: string,
  spec: NegotiationSpec,
): CreatedScenarioResult["spec"] {
  return buildSimulationLikeCreatedSpec({
    descriptionPrompt: description,
    rubric: "Evaluate negotiation quality, opponent modeling, and outcome efficiency.",
    scenarioDescription: spec.description,
    environmentDescription: spec.environmentDescription,
    initialStateDescription: spec.initialStateDescription,
    successCriteria: spec.successCriteria,
    failureModes: spec.failureModes,
    actions: spec.actions,
    maxSteps: spec.maxSteps,
    extras: {
      hidden_preferences: {
        priorities: spec.hiddenPreferences.priorities,
        reservation_value: spec.hiddenPreferences.reservationValue,
        aspiration_value: spec.hiddenPreferences.aspirationValue,
        batna_description: spec.hiddenPreferences.batnaDescription,
      },
      totalRounds: spec.maxRounds,
    },
  });
}

function buildOperatorLoopCreatedSpec(
  description: string,
  spec: OperatorLoopSpec,
): CreatedScenarioResult["spec"] {
  return buildSimulationLikeCreatedSpec({
    descriptionPrompt: description,
    rubric: "Evaluate escalation judgment, safe autonomy, and clarification quality.",
    scenarioDescription: spec.description,
    environmentDescription: spec.environmentDescription,
    initialStateDescription: spec.initialStateDescription,
    successCriteria: spec.successCriteria,
    failureModes: spec.failureModes,
    actions: spec.actions,
    maxSteps: spec.maxSteps,
    extras: {
      escalation_policy: {
        escalation_threshold: spec.escalationPolicy.escalationThreshold,
        max_escalations: spec.escalationPolicy.maxEscalations,
      },
    },
  });
}

function buildCoordinationCreatedSpec(
  description: string,
  spec: CoordinationSpec,
): CreatedScenarioResult["spec"] {
  return buildSimulationLikeCreatedSpec({
    descriptionPrompt: description,
    rubric: "Evaluate worker coordination, handoff quality, and merged-output consistency.",
    scenarioDescription: spec.description,
    environmentDescription: spec.environmentDescription,
    initialStateDescription: spec.initialStateDescription,
    successCriteria: spec.successCriteria,
    failureModes: spec.failureModes,
    actions: spec.actions,
    maxSteps: spec.maxSteps,
    extras: {
      workers: spec.workers.map((worker) => ({
        id: worker.workerId,
        role: worker.role,
        partialContext: {},
      })),
    },
  });
}

const FAMILY_AWARE_SCENARIO_FACTORIES: Record<
  FamilyAwareScenarioFamily,
  (description: string, llmFn: LlmFn) => Promise<CreatedScenarioResult["spec"]>
> = {
  coordination: async (description, llmFn) =>
    buildCoordinationCreatedSpec(description, await designCoordination(description, llmFn)),
  investigation: async (description, llmFn) =>
    buildInvestigationCreatedSpec(description, await designInvestigation(description, llmFn)),
  negotiation: async (description, llmFn) =>
    buildNegotiationCreatedSpec(description, await designNegotiation(description, llmFn)),
  operator_loop: async (description, llmFn) =>
    buildOperatorLoopCreatedSpec(description, await designOperatorLoop(description, llmFn)),
  schema_evolution: async (description, llmFn) =>
    buildSchemaEvolutionCreatedSpec(description, await designSchemaEvolution(description, llmFn)),
  simulation: async (description, llmFn) =>
    buildSimulationCreatedSpec(description, await designSimulation(description, llmFn)),
  workflow: async (description, llmFn) =>
    buildWorkflowCreatedSpec(description, await designWorkflow(description, llmFn)),
};

async function createFamilyAwareScenarioFromDescription(
  description: string,
  provider: LLMProvider,
  name: string,
  family: FamilyAwareScenarioFamily,
): Promise<CreatedScenarioResult> {
  return {
    name,
    family,
    spec: await FAMILY_AWARE_SCENARIO_FACTORIES[family](description, createProviderLlmFn(provider)),
  };
}

function shouldFallbackFromFamilyAwareCreation(error: unknown): boolean {
  if (error instanceof SyntaxError) {
    return true;
  }
  if (!(error instanceof Error)) {
    return false;
  }
  return error.name === "ZodError" || error.message.includes("response does not contain");
}

async function createGenericScenarioFromDescription(
  description: string,
  provider: LLMProvider,
  defaultName: string,
  defaultFamily: ScenarioFamilyName,
): Promise<CreatedScenarioResult> {
  const result = await provider.complete({
    systemPrompt: scenarioCreationInstructions(),
    userPrompt: description,
  });

  let spec: Record<string, unknown>;
  try {
    // Try to parse JSON from the response
    const text = result.text.trim();
    const jsonStart = text.indexOf("{");
    const jsonEnd = text.lastIndexOf("}");
    if (jsonStart !== -1 && jsonEnd !== -1) {
      spec = JSON.parse(text.slice(jsonStart, jsonEnd + 1));
    } else {
      spec = JSON.parse(text);
    }
  } catch {
    // Fallback: use the description directly
    spec = {
      taskPrompt: description,
      rubric: `Evaluate the quality of the response to: ${description}`,
      description: `Custom scenario: ${description}`,
    };
  }

  // Ensure required fields
  if (!spec.taskPrompt) spec.taskPrompt = description;
  if (!spec.rubric) spec.rubric = "Evaluate the quality of the response.";
  if (!spec.description) spec.description = `Custom scenario: ${description}`;
  const name = typeof spec.name === "string" && spec.name.trim() ? spec.name.trim() : defaultName;
  const family =
    typeof spec.family === "string" && isScenarioFamilyName(spec.family)
      ? spec.family
      : defaultFamily;
  const { name: _ignoredName, family: _ignoredFamily, ...specFields } = spec;

  return {
    name,
    family,
    spec: healSpec(
      specFields as Record<string, unknown>,
      family,
      description,
    ) as CreatedScenarioResult["spec"],
  };
}

/**
 * Create a scenario spec from a natural language description.
 * Uses the provider to generate a task prompt and rubric from the description.
 */
export async function createScenarioFromDescription(
  description: string,
  provider: LLMProvider,
): Promise<CreatedScenarioResult> {
  const defaultName = deriveScenarioName(description);
  const defaultFamily = detectScenarioFamily(description);

  if (hasFamilyAwareScenarioFactory(defaultFamily)) {
    try {
      const created = await createFamilyAwareScenarioFromDescription(
        description,
        provider,
        defaultName,
        defaultFamily,
      );
      return {
        ...created,
        spec: healSpec(
          created.spec as Record<string, unknown>,
          created.family,
          description,
        ) as CreatedScenarioResult["spec"],
      };
    } catch (error) {
      if (!shouldFallbackFromFamilyAwareCreation(error)) {
        throw error;
      }
    }
  }

  return createGenericScenarioFromDescription(description, provider, defaultName, defaultFamily);
}
