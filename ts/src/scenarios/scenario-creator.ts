/**
 * NL → Scenario creation flow (AC-348 Task 30).
 * Converts a natural language description into a scenario spec via LLM.
 */

import { SCENARIO_TYPE_MARKERS, type ScenarioFamilyName } from "./families.js";
import { classifyScenarioFamily, routeToFamily } from "./family-classifier.js";
import { healSpec } from "./spec-auto-heal.js";
import type { LLMProvider } from "../types/index.js";

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

/**
 * Derive a snake_case scenario name from a description.
 */
export function deriveScenarioName(description: string): string {
  return description
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, "")
    .split(/\s+/)
    .filter((w) => w.length > 2)
    .slice(0, 4)
    .join("_") || "custom_task";
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
  return [
    scenarioCreationInstructions(),
    "",
    `User description: ${description}`,
  ].join("\n");
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
  if (!spec.rubric) spec.rubric = `Evaluate the quality of the response.`;
  if (!spec.description) spec.description = `Custom scenario: ${description}`;
  const name = typeof spec.name === "string" && spec.name.trim()
    ? spec.name.trim()
    : defaultName;
  const family = typeof spec.family === "string" && isScenarioFamilyName(spec.family)
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
