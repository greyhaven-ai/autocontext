import type { AgentTaskSpec } from "./agent-task-spec.js";
import { ArtifactEditingSpecSchema, type ArtifactEditingSpec } from "./artifact-editing-spec.js";
import { validateSpec as validateAgentTaskSpec } from "./agent-task-validator.js";
import { type ScenarioFamilyName } from "./families.js";
import { SimulationSpecSchema, type SimulationSpec } from "./simulation-spec.js";

export interface FamilyPipeline<TSpec> {
  readonly familyName: ScenarioFamilyName;
  validateSpec(spec: TSpec): string[];
}

export class UnsupportedFamilyError extends Error {
  readonly familyName: string;
  readonly availablePipelines: ScenarioFamilyName[];

  constructor(familyName: string, availablePipelines: ScenarioFamilyName[]) {
    super(
      `No pipeline registered for family '${familyName}'. Available: ${availablePipelines.join(", ")}`,
    );
    this.familyName = familyName;
    this.availablePipelines = availablePipelines;
  }
}

const agentTaskPipeline: FamilyPipeline<AgentTaskSpec> = {
  familyName: "agent_task",
  validateSpec(spec: AgentTaskSpec): string[] {
    return validateAgentTaskSpec(spec);
  },
};

const simulationPipeline: FamilyPipeline<SimulationSpec> = {
  familyName: "simulation",
  validateSpec(spec: SimulationSpec): string[] {
    const result = SimulationSpecSchema.safeParse(spec);
    if (!result.success) {
      return result.error.issues.map(
        (issue) => `${issue.path.join(".")}: ${issue.message}`,
      );
    }
    return [];
  },
};

const artifactEditingPipeline: FamilyPipeline<ArtifactEditingSpec> = {
  familyName: "artifact_editing",
  validateSpec(spec: ArtifactEditingSpec): string[] {
    const result = ArtifactEditingSpecSchema.safeParse(spec);
    if (!result.success) {
      return result.error.issues.map(
        (issue) => `${issue.path.join(".")}: ${issue.message}`,
      );
    }
    return [];
  },
};

const PIPELINE_REGISTRY = {
  agent_task: agentTaskPipeline,
  simulation: simulationPipeline,
  artifact_editing: artifactEditingPipeline,
} as const;

export function hasPipeline(family: string): family is keyof typeof PIPELINE_REGISTRY {
  return family in PIPELINE_REGISTRY;
}

export function getPipeline(family: string): (typeof PIPELINE_REGISTRY)[keyof typeof PIPELINE_REGISTRY] {
  if (!hasPipeline(family)) {
    throw new UnsupportedFamilyError(family, Object.keys(PIPELINE_REGISTRY) as ScenarioFamilyName[]);
  }
  return PIPELINE_REGISTRY[family];
}

export function validateForFamily(
  family: string,
  spec: AgentTaskSpec | SimulationSpec | ArtifactEditingSpec,
): string[] {
  const pipeline = getPipeline(family);
  return pipeline.validateSpec(spec as never);
}
