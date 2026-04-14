import type {
  ProvenanceManifest,
  PublicTrace,
  SubmissionAttestation,
} from "./public-schema.js";

export interface TraceArtifact {
  trace: PublicTrace;
  manifest: ProvenanceManifest;
  attestation: SubmissionAttestation;
  redactionSummary?: Record<string, unknown>;
}

export interface PublishResult {
  status: "published" | "dry_run" | "failed";
  host: string;
  location?: string;
  url?: string;
  payload?: Record<string, unknown>;
  error?: string;
}

export interface PublishOpts {
  dryRun?: boolean;
}

export interface IngestResult {
  status: "ingested" | "failed";
  tracesIngested: number;
  duplicatesSkipped: number;
  cacheDir?: string;
  error?: string;
}
