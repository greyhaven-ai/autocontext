/** Evidence workspace domain model (AC-504). */

export interface EvidenceArtifact {
  artifactId: string;
  sourceRunId: string;
  kind: "trace" | "role_output" | "report" | "tool" | "gate_decision" | "log";
  path: string;
  summary: string;
  sizeBytes: number;
  generation: number | null;
}

export interface EvidenceWorkspace {
  workspaceDir: string;
  sourceRuns: string[];
  artifacts: EvidenceArtifact[];
  totalSizeBytes: number;
  materializedAt: string;
  accessedArtifacts: string[];
}

export function getArtifact(
  workspace: EvidenceWorkspace,
  artifactId: string,
): EvidenceArtifact | null {
  return workspace.artifacts.find((a) => a.artifactId === artifactId) ?? null;
}

export function listByKind(
  workspace: EvidenceWorkspace,
  kind: EvidenceArtifact["kind"],
): EvidenceArtifact[] {
  return workspace.artifacts.filter((a) => a.kind === kind);
}
