import {
  newArtifactId,
  defaultEnvironmentTag,
  type ArtifactId,
  type ChangeSetId,
  type ContentHash,
  type EnvironmentTag,
  type Scenario,
  type SuiteId,
} from "./branded-ids.js";
import { CURRENT_SCHEMA_VERSION } from "./schema-version.js";
import type {
  ActivationState,
  ActuatorType,
  Artifact,
  EvalRun,
  MetricBundle,
  PromotionEvent,
  Provenance,
} from "./types.js";

export interface CreateArtifactInputs {
  readonly actuatorType: ActuatorType;
  readonly scenario: Scenario;
  readonly environmentTag?: EnvironmentTag;
  readonly changeSetId?: ChangeSetId;
  readonly payloadHash: ContentHash;
  readonly provenance: Provenance;
  readonly id?: ArtifactId;
}

export function createArtifact(inputs: CreateArtifactInputs): Artifact {
  const artifact: Artifact = {
    schemaVersion: CURRENT_SCHEMA_VERSION,
    id: inputs.id ?? newArtifactId(),
    actuatorType: inputs.actuatorType,
    scenario: inputs.scenario,
    environmentTag: inputs.environmentTag ?? defaultEnvironmentTag(),
    ...(inputs.changeSetId !== undefined ? { changeSetId: inputs.changeSetId } : {}),
    activationState: "candidate",
    payloadHash: inputs.payloadHash,
    provenance: inputs.provenance,
    promotionHistory: [],
    evalRuns: [],
  };
  return artifact;
}

export interface CreatePromotionEventInputs {
  readonly from: ActivationState;
  readonly to: ActivationState;
  readonly reason: string;
  readonly timestamp: string;
  readonly evidence?: PromotionEvent["evidence"];
  readonly signature?: string;
}

export function createPromotionEvent(inputs: CreatePromotionEventInputs): PromotionEvent {
  const event: PromotionEvent = {
    from: inputs.from,
    to: inputs.to,
    reason: inputs.reason,
    timestamp: inputs.timestamp,
    ...(inputs.evidence !== undefined ? { evidence: inputs.evidence } : {}),
    ...(inputs.signature !== undefined ? { signature: inputs.signature } : {}),
  };
  return event;
}

export interface CreateEvalRunInputs {
  readonly runId: string;
  readonly artifactId: ArtifactId;
  readonly suiteId: SuiteId;
  readonly metrics: MetricBundle;
  readonly datasetProvenance: EvalRun["datasetProvenance"];
  readonly ingestedAt: string;
}

export function createEvalRun(inputs: CreateEvalRunInputs): EvalRun {
  return {
    schemaVersion: CURRENT_SCHEMA_VERSION,
    runId: inputs.runId,
    artifactId: inputs.artifactId,
    suiteId: inputs.suiteId,
    metrics: inputs.metrics,
    datasetProvenance: inputs.datasetProvenance,
    ingestedAt: inputs.ingestedAt,
  };
}

// appendPromotionEvent moved to promotion/append.ts — it is state-machine logic
// that depends on the transition allow-list, which must live in promotion/.
// See `control-plane/promotion/append.ts`.
