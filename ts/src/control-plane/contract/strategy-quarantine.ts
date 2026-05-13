import type { Scenario } from "./branded-ids.js";
import type { ActuatorType, Artifact, StrategyIdentity, StrategyQuarantine } from "./types.js";
import { detectStrategyDuplicate } from "./strategy-identity.js";

type QuarantineSourceArtifact = Pick<
  Artifact,
  "id" | "activationState" | "actuatorType" | "scenario" | "payloadHash" | "strategyIdentity" | "strategyQuarantine"
>;

export function assessStrategyQuarantine(
  candidate: StrategyIdentity,
  actuatorType: ActuatorType,
  scenario: Scenario,
  existingArtifacts: readonly QuarantineSourceArtifact[],
): StrategyQuarantine | null {
  const invalidSources = existingArtifacts.filter(isInvalidStrategySource);
  const duplicate = detectStrategyDuplicate(candidate, actuatorType, scenario, invalidSources);
  if (duplicate === null) return null;

  return {
    status: "quarantined",
    reason: "repeated-invalid-strategy",
    sourceArtifactIds: [duplicate.artifactId],
    sourceFingerprints: [duplicate.fingerprint],
    detail: `${duplicate.kind} duplicate of disabled/quarantined artifact ${duplicate.artifactId}`,
  };
}

export function describeStrategyQuarantine(
  artifact: Pick<Artifact, "strategyQuarantine">,
  label: string,
): string | null {
  const quarantine = artifact.strategyQuarantine;
  if (quarantine === undefined) return null;
  if (quarantine.status !== "quarantined") return null;
  return `${label} strategy is quarantined (${quarantine.reason})`;
}

function isInvalidStrategySource(artifact: QuarantineSourceArtifact): boolean {
  return (
    artifact.activationState === "disabled" ||
    artifact.strategyQuarantine?.status === "quarantined"
  );
}
