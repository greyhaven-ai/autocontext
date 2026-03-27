/**
 * Scenario materialization — persist runnable artifacts from specs (AC-433).
 *
 * This is the missing glue between "spec created" and "runnable scenario on disk."
 * Called by the CLI new-scenario command, MCP tools, and programmatic API.
 *
 * For each family:
 * - Writes spec.json (full spec, camelCase)
 * - Writes scenario_type.txt (family marker)
 * - For agent_task: writes agent_task_spec.json (snake_case for custom-loader)
 * - For codegen families: generates scenario.js via the codegen pipeline
 * - Validates generated code by execution before persisting
 *
 * After materialization, the scenario is discoverable by loadCustomScenarios()
 * and runnable through the appropriate execution path.
 */

import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { getScenarioTypeMarker, type ScenarioFamilyName } from "./families.js";
import { hasCodegen, generateScenarioSource } from "./codegen/index.js";
import { validateGeneratedScenario } from "./codegen/execution-validator.js";
import { healSpec } from "./spec-auto-heal.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface MaterializeOpts {
  /** Scenario name (used as directory name under _custom_scenarios/) */
  name: string;
  /** Scenario family */
  family: string;
  /** The scenario spec (taskPrompt, rubric, description, plus family-specific fields) */
  spec: Record<string, unknown>;
  /** Root knowledge directory (e.g., "./knowledge") */
  knowledgeRoot: string;
}

export interface MaterializeResult {
  /** Whether artifacts were persisted to disk */
  persisted: boolean;
  /** Whether executable JS source was generated (codegen families) */
  generatedSource: boolean;
  /** Absolute path to the scenario directory */
  scenarioDir: string;
  /** The family that was materialized */
  family: string;
  /** The scenario name */
  name: string;
  /** Validation errors, if any (empty = success) */
  errors: string[];
}

// Families that get an agent_task_spec.json for backward compat with custom-loader
const AGENT_TASK_FAMILY = "agent_task";

// ---------------------------------------------------------------------------
// Core
// ---------------------------------------------------------------------------

/**
 * Materialize a scenario spec into durable on-disk artifacts.
 *
 * After this call, the scenario is:
 * - Discoverable by loadCustomScenarios()
 * - Runnable through the appropriate execution path
 * - Persisted with all required metadata
 */
export async function materializeScenario(opts: MaterializeOpts): Promise<MaterializeResult> {
  const { name, spec, knowledgeRoot } = opts;
  const family = coerceFamily(opts.family);
  const scenarioDir = join(knowledgeRoot, "_custom_scenarios", name);
  const errors: string[] = [];

  // Create directory
  if (!existsSync(scenarioDir)) {
    mkdirSync(scenarioDir, { recursive: true });
  }

  // Auto-heal spec before persisting
  const healedSpec = healSpec(spec, family);

  // 1. Write scenario_type.txt
  const scenarioType = getScenarioTypeMarker(family);
  writeFileSync(join(scenarioDir, "scenario_type.txt"), scenarioType, "utf-8");

  // 2. Write spec.json (full spec)
  writeFileSync(
    join(scenarioDir, "spec.json"),
    JSON.stringify(
      {
        name,
        family,
        scenario_type: scenarioType,
        ...healedSpec,
      },
      null,
      2,
    ),
    "utf-8",
  );

  // 3. Write agent_task_spec.json for agent_task (custom-loader compat)
  if (family === AGENT_TASK_FAMILY) {
    writeFileSync(
      join(scenarioDir, "agent_task_spec.json"),
      JSON.stringify(
        {
          task_prompt: String(healedSpec.taskPrompt ?? ""),
          judge_rubric: String(healedSpec.rubric ?? healedSpec.judgeRubric ?? ""),
          output_format: String(healedSpec.outputFormat ?? healedSpec.output_format ?? "free_text"),
          judge_model: String(healedSpec.judgeModel ?? healedSpec.judge_model ?? ""),
          max_rounds: Number(healedSpec.maxRounds ?? healedSpec.max_rounds ?? 1),
          quality_threshold: Number(healedSpec.qualityThreshold ?? healedSpec.quality_threshold ?? 0.9),
          revision_prompt: healedSpec.revisionPrompt ?? healedSpec.revision_prompt ?? null,
          sample_input: healedSpec.sampleInput ?? healedSpec.sample_input ?? null,
          reference_context: healedSpec.referenceContext ?? healedSpec.reference_context ?? null,
          required_concepts: healedSpec.requiredConcepts ?? healedSpec.required_concepts ?? null,
        },
        null,
        2,
      ),
      "utf-8",
    );
  }

  // 4. Generate scenario.js for codegen-supported families
  let generatedSource = false;
  if (family !== AGENT_TASK_FAMILY && hasCodegen(family)) {
    try {
      const source = generateScenarioSource(family as ScenarioFamilyName, healedSpec, name);

      // Validate by execution before persisting
      const validation = await validateGeneratedScenario(source, family, name);
      if (!validation.valid) {
        errors.push(...validation.errors.map((e) => `codegen validation: ${e}`));
      } else {
        writeFileSync(join(scenarioDir, "scenario.js"), source, "utf-8");
        generatedSource = true;
      }
    } catch (err) {
      errors.push(`codegen failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  return {
    persisted: true,
    generatedSource,
    scenarioDir,
    family,
    name,
    errors,
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function coerceFamily(family: string): ScenarioFamilyName {
  const valid: ScenarioFamilyName[] = [
    "game", "agent_task", "simulation", "artifact_editing", "investigation",
    "workflow", "negotiation", "schema_evolution", "tool_fragility",
    "operator_loop", "coordination",
  ];
  if (valid.includes(family as ScenarioFamilyName)) return family as ScenarioFamilyName;
  return "agent_task";
}
