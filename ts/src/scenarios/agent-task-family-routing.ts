import type { LLMProvider } from "../types/index.js";
import {
  type ArtifactEditingScenarioHandle,
  ArtifactEditingCreator,
} from "./artifact-editing-creator.js";
import {
  type CoordinationScenarioHandle,
  CoordinationCreator,
} from "./coordination-creator.js";
import { classifyScenarioFamily, routeToFamily } from "./family-classifier.js";
import type { ScenarioFamilyName } from "./families.js";
import {
  type InvestigationScenarioHandle,
  InvestigationCreator,
} from "./investigation-creator.js";
import {
  type NegotiationScenarioHandle,
  NegotiationCreator,
} from "./negotiation-creator.js";
import {
  OperatorLoopCreator,
  type OperatorLoopScenarioHandle,
} from "./operator-loop-creator.js";
import {
  type SchemaEvolutionScenarioHandle,
  SchemaEvolutionCreator,
} from "./schema-evolution-creator.js";
import {
  type SimulationScenarioHandle,
  SimulationCreator,
} from "./simulation-creator.js";
import {
  type ToolFragilityScenarioHandle,
  ToolFragilityCreator,
} from "./tool-fragility-creator.js";
import {
  type WorkflowScenarioHandle,
  WorkflowCreator,
} from "./workflow-creator.js";

export type RoutedAgentTaskScenario =
  | ArtifactEditingScenarioHandle
  | CoordinationScenarioHandle
  | InvestigationScenarioHandle
  | NegotiationScenarioHandle
  | OperatorLoopScenarioHandle
  | SchemaEvolutionScenarioHandle
  | SimulationScenarioHandle
  | ToolFragilityScenarioHandle
  | WorkflowScenarioHandle;

export function classifyAgentTaskFamily(description: string): ScenarioFamilyName {
  return routeToFamily(classifyScenarioFamily(description));
}

export async function routeAgentTaskScenarioCreation(opts: {
  family: ScenarioFamilyName;
  description: string;
  name: string;
  provider: LLMProvider;
  model: string;
  knowledgeRoot: string;
}): Promise<RoutedAgentTaskScenario | null> {
  const shared = {
    provider: opts.provider,
    model: opts.model,
    knowledgeRoot: opts.knowledgeRoot,
  };

  if (opts.family === "simulation") {
    return new SimulationCreator(shared).create(opts.description, opts.name);
  }
  if (opts.family === "artifact_editing") {
    return new ArtifactEditingCreator(shared).create(opts.description, opts.name);
  }
  if (opts.family === "investigation") {
    return new InvestigationCreator(shared).create(opts.description, opts.name);
  }
  if (opts.family === "workflow") {
    return new WorkflowCreator(shared).create(opts.description, opts.name);
  }
  if (opts.family === "schema_evolution") {
    return new SchemaEvolutionCreator(shared).create(opts.description, opts.name);
  }
  if (opts.family === "tool_fragility") {
    return new ToolFragilityCreator(shared).create(opts.description, opts.name);
  }
  if (opts.family === "negotiation") {
    return new NegotiationCreator(shared).create(opts.description, opts.name);
  }
  if (opts.family === "operator_loop") {
    return new OperatorLoopCreator(shared).create(opts.description, opts.name);
  }
  if (opts.family === "coordination") {
    return new CoordinationCreator(shared).create(opts.description, opts.name);
  }
  if (opts.family === "agent_task") {
    return null;
  }

  throw new Error(`Scenario family '${opts.family}' is not yet supported for custom scaffolding`);
}
