import {
  newArtifactId,
  newHarnessProposalId,
  defaultEnvironmentTag,
  type ArtifactId,
  type ChangeSetId,
  type ContentHash,
  type EnvironmentTag,
  type HarnessProposalId,
  type Scenario,
  type SuiteId,
} from "./branded-ids.js";
import { CURRENT_SCHEMA_VERSION } from "./schema-version.js";
import type {
  ActivationState,
  ActuatorType,
  Artifact,
  EvalRun,
  AblationVerification,
  AdapterProvenance,
  EvalRunIntegrity,
  EvalRunReconciliation,
  EvalTrial,
  HarnessChangeDecision,
  HarnessChangeProposal,
  HarnessChangeSurface,
  HarnessExpectedImpact,
  HarnessProposedEdit,
  HarnessChangeProposalStatus,
  MemoryPackRef,
  MetricBundle,
  PromotionEvent,
  Provenance,
  RunTrack,
  StrategyIdentity,
  StrategyQuarantine,
} from "./types.js";

export interface CreateArtifactInputs {
  readonly actuatorType: ActuatorType;
  readonly scenario: Scenario;
  readonly environmentTag?: EnvironmentTag;
  readonly changeSetId?: ChangeSetId;
  readonly payloadHash: ContentHash;
  readonly provenance: Provenance;
  readonly strategyIdentity?: StrategyIdentity;
  readonly strategyQuarantine?: StrategyQuarantine;
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
    ...(inputs.strategyIdentity !== undefined ? { strategyIdentity: inputs.strategyIdentity } : {}),
    ...(inputs.strategyQuarantine !== undefined ? { strategyQuarantine: inputs.strategyQuarantine } : {}),
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
  readonly track?: RunTrack;
  readonly metrics: MetricBundle;
  readonly datasetProvenance: EvalRun["datasetProvenance"];
  readonly ingestedAt: string;
  readonly adapterProvenance?: AdapterProvenance;
  readonly integrity?: EvalRunIntegrity;
  readonly ablationVerification?: AblationVerification;
  readonly trials?: readonly EvalTrial[];
  readonly reconciliation?: EvalRunReconciliation;
  readonly memoryPacks?: readonly MemoryPackRef[];
}

export function createEvalRun(inputs: CreateEvalRunInputs): EvalRun {
  return {
    schemaVersion: CURRENT_SCHEMA_VERSION,
    runId: inputs.runId,
    artifactId: inputs.artifactId,
    suiteId: inputs.suiteId,
    ...(inputs.track !== undefined ? { track: inputs.track } : {}),
    metrics: inputs.metrics,
    datasetProvenance: inputs.datasetProvenance,
    ingestedAt: inputs.ingestedAt,
    ...(inputs.adapterProvenance !== undefined ? { adapterProvenance: inputs.adapterProvenance } : {}),
    ...(inputs.integrity !== undefined ? { integrity: inputs.integrity } : {}),
    ...(inputs.ablationVerification !== undefined ? { ablationVerification: inputs.ablationVerification } : {}),
    ...(inputs.trials !== undefined ? { trials: inputs.trials } : {}),
    ...(inputs.reconciliation !== undefined ? { reconciliation: inputs.reconciliation } : {}),
    ...(inputs.memoryPacks !== undefined ? { memoryPacks: inputs.memoryPacks } : {}),
  };
}

export interface CreateHarnessChangeProposalInputs {
  readonly id?: HarnessProposalId;
  readonly status?: HarnessChangeProposalStatus;
  readonly findingIds: readonly string[];
  readonly targetSurface: HarnessChangeSurface;
  readonly proposedEdit: HarnessProposedEdit;
  readonly expectedImpact?: HarnessExpectedImpact;
  readonly rollbackCriteria: readonly string[];
  readonly provenance: Provenance;
  readonly decision?: HarnessChangeDecision;
}

export function createHarnessChangeProposal(
  inputs: CreateHarnessChangeProposalInputs,
): HarnessChangeProposal {
  return {
    schemaVersion: CURRENT_SCHEMA_VERSION,
    id: inputs.id ?? newHarnessProposalId(),
    status: inputs.status ?? inputs.decision?.status ?? "proposed",
    findingIds: inputs.findingIds,
    targetSurface: inputs.targetSurface,
    proposedEdit: inputs.proposedEdit,
    expectedImpact: inputs.expectedImpact ?? {},
    rollbackCriteria: inputs.rollbackCriteria,
    provenance: inputs.provenance,
    ...(inputs.decision !== undefined ? { decision: inputs.decision } : {}),
  };
}

// appendPromotionEvent moved to promotion/append.ts — it is state-machine logic
// that depends on the transition allow-list, which must live in promotion/.
// See `control-plane/promotion/append.ts`.
