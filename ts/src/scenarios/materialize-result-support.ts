import type { ScenarioFamilyName } from "./families.js";

const SUPPORTED_MATERIALIZE_FAMILIES = [
  "game",
  "agent_task",
  "simulation",
  "artifact_editing",
  "investigation",
  "workflow",
  "negotiation",
  "schema_evolution",
  "tool_fragility",
  "operator_loop",
  "coordination",
] satisfies readonly ScenarioFamilyName[];

function isSupportedMaterializeFamily(family: string): family is ScenarioFamilyName {
  const supportedFamilies: readonly string[] = SUPPORTED_MATERIALIZE_FAMILIES;
  return supportedFamilies.includes(family);
}

export function coerceMaterializeFamily(family: string): ScenarioFamilyName {
  if (isSupportedMaterializeFamily(family)) {
    return family;
  }
  return "agent_task";
}

export function buildMaterializeFailureResult(opts: {
  scenarioDir: string;
  family: string;
  name: string;
  errors: string[];
}): {
  persisted: boolean;
  generatedSource: boolean;
  scenarioDir: string;
  family: string;
  name: string;
  errors: string[];
} {
  return {
    persisted: false,
    generatedSource: false,
    scenarioDir: opts.scenarioDir,
    family: opts.family,
    name: opts.name,
    errors: opts.errors,
  };
}

export function buildUnsupportedGameMaterializeResult(opts: {
  scenarioDir: string;
  family: string;
  name: string;
}): {
  persisted: boolean;
  generatedSource: boolean;
  scenarioDir: string;
  family: string;
  name: string;
  errors: string[];
} {
  return buildMaterializeFailureResult({
    scenarioDir: opts.scenarioDir,
    family: opts.family,
    name: opts.name,
    errors: [
      "custom scenario materialization does not support family 'game'; use a built-in game scenario instead",
    ],
  });
}

export function buildSuccessfulMaterializeResult(opts: {
  generatedSource: boolean;
  scenarioDir: string;
  family: string;
  name: string;
}): {
  persisted: boolean;
  generatedSource: boolean;
  scenarioDir: string;
  family: string;
  name: string;
  errors: string[];
} {
  return {
    persisted: true,
    generatedSource: opts.generatedSource,
    scenarioDir: opts.scenarioDir,
    family: opts.family,
    name: opts.name,
    errors: [],
  };
}
