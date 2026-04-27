import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import type { CreatedScenarioResult } from "../scenarios/scenario-creator.js";
import { buildFamilyClassificationBrief } from "../scenarios/family-classifier-input.js";
import { SCENARIO_TYPE_MARKERS, getScenarioTypeMarker, type ScenarioFamilyName } from "../scenarios/families.js";
import { hasCodegen } from "../scenarios/codegen/registry.js";
import { materializeScenario, type MaterializeResult } from "../scenarios/materialize.js";
import { healSpec } from "../scenarios/spec-auto-heal.js";

export type SolveExecutionRoute =
  | "builtin_game"
  | "missing_game"
  | "agent_task"
  | "codegen"
  | "unsupported";

export interface PreparedSolveScenario extends CreatedScenarioResult {
  family: ScenarioFamilyName;
  spec: CreatedScenarioResult["spec"];
}

export const SOLVE_FAMILY_ALIASES: Readonly<Record<string, ScenarioFamilyName>> = {
  alignment_stress_test: "agent_task",
  capability_bootstrapping: "agent_task",
  compositional_generalization: "agent_task",
  meta_learning: "agent_task",
};

const FAMILY_HEADER_REGEX = /^\s*\*{0,2}family\*{0,2}:\s*(.+?)\s*$/im;
const SIMULATION_INTERFACE_HINT_REGEX =
  /\bsimulationinterface\b.*\bworldstate\b|\bworldstate\b.*\bsimulationinterface\b/is;
const AGENT_TASK_INTERFACE_HINT_REGEX = /\bagent[- ]task evaluation\b/i;

function normalizeSolveFamilyHintToken(token: string): string {
  return token
    .toLowerCase()
    .replace(/[^a-z0-9_\-\s]/g, " ")
    .trim()
    .replace(/-/g, "_")
    .replace(/\s+/g, "_");
}

function asScenarioFamilyName(candidate: string): ScenarioFamilyName | null {
  return candidate in SCENARIO_TYPE_MARKERS ? candidate as ScenarioFamilyName : null;
}

function readSolveFamilyHeaderTokens(description: string): string[] {
  const brief = buildFamilyClassificationBrief(description);
  const match = FAMILY_HEADER_REGEX.exec(brief);
  if (!match) {
    return [];
  }
  const rawHint = match[1] ?? "";
  return rawHint.split(/[\/,|]/).map(normalizeSolveFamilyHintToken).filter(Boolean);
}

export function resolveSolveFamilyHint(description: string): ScenarioFamilyName | null {
  const tokens = readSolveFamilyHeaderTokens(description);
  for (const token of tokens) {
    const family = asScenarioFamilyName(token);
    if (family) {
      return family;
    }
  }
  for (const token of tokens) {
    const aliased = SOLVE_FAMILY_ALIASES[token];
    if (aliased) {
      return aliased;
    }
  }
  return null;
}

export function resolveSolveFamilyAlias(description: string): ScenarioFamilyName | null {
  const hinted = resolveSolveFamilyHint(description);
  if (hinted) {
    return hinted;
  }
  const brief = buildFamilyClassificationBrief(description);
  if (SIMULATION_INTERFACE_HINT_REGEX.test(brief)) {
    return "simulation";
  }
  if (AGENT_TASK_INTERFACE_HINT_REGEX.test(brief)) {
    return "agent_task";
  }
  return null;
}

export function resolveSolveFamilyOverride(
  description: string,
  explicitFamily?: string,
): ScenarioFamilyName | undefined {
  return validateSolveFamilyOverride(explicitFamily)
    ?? resolveSolveFamilyAlias(description)
    ?? undefined;
}

export function coerceSolveFamily(family: string): ScenarioFamilyName {
  switch (family) {
    case "game":
    case "simulation":
    case "artifact_editing":
    case "investigation":
    case "workflow":
    case "schema_evolution":
    case "tool_fragility":
    case "negotiation":
    case "operator_loop":
    case "coordination":
    case "agent_task":
      return family;
    default:
      return "agent_task";
  }
}

export function validateSolveFamilyOverride(family: string | undefined): ScenarioFamilyName | undefined {
  const normalized = family?.trim().toLowerCase().replace(/-/g, "_");
  if (!normalized) {
    return undefined;
  }
  if (normalized in SCENARIO_TYPE_MARKERS) {
    return normalized as ScenarioFamilyName;
  }
  throw new Error(
    `Unknown solve family '${family}'. Valid families: ${Object.keys(SCENARIO_TYPE_MARKERS).sort().join(", ")}`,
  );
}

export function prepareSolveScenario(opts: {
  created: CreatedScenarioResult;
  description: string;
  familyOverride?: ScenarioFamilyName;
}): PreparedSolveScenario {
  const family = opts.familyOverride ?? coerceSolveFamily(opts.created.family);
  return {
    ...opts.created,
    family,
    spec: healSpec(
      opts.created.spec as Record<string, unknown>,
      family,
      opts.description,
    ) as CreatedScenarioResult["spec"],
  };
}

export function determineSolveExecutionRoute(
  created: PreparedSolveScenario,
  builtinScenarioNames: string[],
): SolveExecutionRoute {
  if (builtinScenarioNames.includes(created.name)) {
    return "builtin_game";
  }
  if (created.family === "game") {
    return "missing_game";
  }
  if (created.family === "agent_task") {
    return "agent_task";
  }
  if (hasCodegen(created.family)) {
    return "codegen";
  }
  return "unsupported";
}

function persistMissingGameScenario(opts: {
  created: PreparedSolveScenario;
  knowledgeRoot: string;
}): MaterializeResult {
  const scenarioDir = join(opts.knowledgeRoot, "_custom_scenarios", opts.created.name);
  if (!existsSync(scenarioDir)) {
    mkdirSync(scenarioDir, { recursive: true });
  }

  const scenarioType = getScenarioTypeMarker("game");
  writeFileSync(join(scenarioDir, "scenario_type.txt"), scenarioType, "utf-8");
  writeFileSync(
    join(scenarioDir, "spec.json"),
    JSON.stringify(
      {
        name: opts.created.name,
        family: "game",
        scenario_type: scenarioType,
        ...opts.created.spec,
      },
      null,
      2,
    ),
    "utf-8",
  );

  return {
    persisted: true,
    generatedSource: false,
    scenarioDir,
    family: "game",
    name: opts.created.name,
    errors: [],
  };
}

export async function persistSolveScenarioScaffold(opts: {
  created: PreparedSolveScenario;
  knowledgeRoot: string;
}): Promise<MaterializeResult> {
  if (opts.created.family === "game") {
    return persistMissingGameScenario(opts);
  }

  return materializeScenario({
    name: opts.created.name,
    family: opts.created.family,
    spec: opts.created.spec as Record<string, unknown>,
    knowledgeRoot: opts.knowledgeRoot,
  });
}
