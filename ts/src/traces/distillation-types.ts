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

export type FailurePolicy = "exclude" | "eval_only" | "contrastive";

export interface DistillationPolicy {
  minScore?: number;
  topQuartile?: boolean;
  advanceOnly?: boolean;
  familyFilter?: string[];
  heldOutRatio?: number;
  failurePolicy?: FailurePolicy;
  requireTrainingConsent?: boolean;
}

export interface DistillationManifest {
  totalTraces: number;
  includedTraces: number;
  excludedTraces: number;
  trainSize: number;
  heldOutSize: number;
  evalOnlySize: number;
  contrastiveSize: number;
  curationPolicy: DistillationPolicy;
  sources: Record<string, number>;
  createdAt: string;
}

export interface DistillationResult {
  status: "completed" | "failed";
  totalTraces: number;
  includedTraces: number;
  excludedTraces: number;
  trainSize: number;
  heldOutSize: number;
  evalOnlyTraces: number;
  contrastiveTraces: number;
  outputDir: string;
  warnings: string[];
  error?: string;
}

export interface DistillationPipelineConfig {
  traceDir: string;
  outputDir: string;
  policy?: DistillationPolicy;
}

export interface DistillationBuildBuckets {
  included: TraceEntry[];
  excluded: TraceEntry[];
  evalOnly: TraceEntry[];
  contrastive: TraceEntry[];
}

export interface DistillationLoadResult {
  entries: TraceEntry[];
  warnings: string[];
}
