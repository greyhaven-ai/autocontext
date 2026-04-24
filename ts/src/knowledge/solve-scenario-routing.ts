import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import type { CreatedScenarioResult } from "../scenarios/scenario-creator.js";
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

export function buildSolveScenarioDescription(
  description: string,
  familyOverride?: ScenarioFamilyName,
): string {
  if (!familyOverride) {
    return description;
  }
  return `**Family:** ${familyOverride}\n\n${description}`;
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
