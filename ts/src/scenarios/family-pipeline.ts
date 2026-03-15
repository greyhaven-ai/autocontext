import type { AgentTaskSpec } from "./agent-task-spec.js";
import { ArtifactEditingSpecSchema, type ArtifactEditingSpec } from "./artifact-editing-spec.js";
import { validateSpec as validateAgentTaskSpec } from "./agent-task-validator.js";
import { type ScenarioFamilyName } from "./families.js";
import { InvestigationSpecSchema, type InvestigationSpec } from "./investigation-spec.js";
import { NegotiationSpecSchema, type NegotiationSpec } from "./negotiation-spec.js";
import { SchemaEvolutionSpecSchema, type SchemaEvolutionSpec } from "./schema-evolution-spec.js";
import { SimulationSpecSchema, type SimulationSpec } from "./simulation-spec.js";
import { ToolFragilitySpecSchema, type ToolFragilitySpec } from "./tool-fragility-spec.js";
import { WorkflowSpecSchema, type WorkflowSpec } from "./workflow-spec.js";

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

const investigationPipeline: FamilyPipeline<InvestigationSpec> = {
  familyName: "investigation",
  validateSpec(spec: InvestigationSpec): string[] {
    const result = InvestigationSpecSchema.safeParse(spec);
    if (!result.success) {
      return result.error.issues.map(
        (issue) => `${issue.path.join(".")}: ${issue.message}`,
      );
    }
    return [];
  },
};

const workflowPipeline: FamilyPipeline<WorkflowSpec> = {
  familyName: "workflow",
  validateSpec(spec: WorkflowSpec): string[] {
    const result = WorkflowSpecSchema.safeParse(spec);
    if (!result.success) {
      return result.error.issues.map(
        (issue) => `${issue.path.join(".")}: ${issue.message}`,
      );
    }
    return [];
  },
};

const schemaEvolutionPipeline: FamilyPipeline<SchemaEvolutionSpec> = {
  familyName: "schema_evolution",
  validateSpec(spec: SchemaEvolutionSpec): string[] {
    const result = SchemaEvolutionSpecSchema.safeParse(spec);
    if (!result.success) {
      return result.error.issues.map(
        (issue) => `${issue.path.join(".")}: ${issue.message}`,
      );
    }
    return [];
  },
};

const toolFragilityPipeline: FamilyPipeline<ToolFragilitySpec> = {
  familyName: "tool_fragility",
  validateSpec(spec: ToolFragilitySpec): string[] {
    const result = ToolFragilitySpecSchema.safeParse(spec);
    if (!result.success) {
      return result.error.issues.map(
        (issue) => `${issue.path.join(".")}: ${issue.message}`,
      );
    }
    return [];
  },
};

const negotiationPipeline: FamilyPipeline<NegotiationSpec> = {
  familyName: "negotiation",
  validateSpec(spec: NegotiationSpec): string[] {
    const result = NegotiationSpecSchema.safeParse(spec);
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
  investigation: investigationPipeline,
  workflow: workflowPipeline,
  schema_evolution: schemaEvolutionPipeline,
  tool_fragility: toolFragilityPipeline,
  negotiation: negotiationPipeline,
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
  spec:
    | AgentTaskSpec
    | SimulationSpec
    | ArtifactEditingSpec
    | InvestigationSpec
    | WorkflowSpec
    | SchemaEvolutionSpec
    | ToolFragilitySpec
    | NegotiationSpec,
): string[] {
  const pipeline = getPipeline(family);
  return pipeline.validateSpec(spec as never);
}
