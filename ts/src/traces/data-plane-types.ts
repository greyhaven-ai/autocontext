import type {
  ProvenanceManifest,
  PublicTrace,
  SubmissionAttestation,
} from "./public-schema.js";

export interface TraceEntry {
  trace: PublicTrace;
  manifest: ProvenanceManifest;
  attestation: SubmissionAttestation;
}

export interface CurationPolicy {
  minScore?: number;
  heldOutRatio?: number;
  requireTrainingConsent?: boolean;
}

export interface CuratedDataset {
  included: TraceEntry[];
  excluded: TraceEntry[];
  train: TraceEntry[];
  heldOut: TraceEntry[];
}

export interface DataPlaneConfig {
  traceDir: string;
  outputDir: string;
  curationPolicy?: CurationPolicy;
}

export interface DataPlaneBuildResult {
  status: "completed" | "failed";
  totalTraces: number;
  includedTraces: number;
  excludedTraces: number;
  trainSize: number;
  heldOutSize: number;
  outputDir: string;
  error?: string;
}

export interface DataPlaneStatus {
  totalTraces: number;
  includedTraces: number;
  trainSize: number;
  heldOutSize: number;
  outputDir: string;
  built: boolean;
}
