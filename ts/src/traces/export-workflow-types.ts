import type { PolicyAction } from "./redaction.js";

export interface ExportRequest {
  runId: string;
  scenario: string;
  submitterId: string;
  license: string;
  consentGiven: boolean;
  dataOrigin: string;
  allowRedistribution: boolean;
  allowTraining: boolean;
  consentNotes?: string;
}

export interface RedactionSummary {
  totalDetections: number;
  totalRedactions: number;
  blocked: boolean;
  blockReasons: string[];
  categoryCounts: Record<string, number>;
}

export interface ExportResult {
  status: "completed" | "blocked" | "failed";
  traceId: string;
  outputPath?: string;
  redactionSummary: RedactionSummary;
  warnings: string[];
  error?: string;
}

export interface TraceExportWorkflowOpts {
  runsRoot: string;
  outputDir: string;
  policyOverrides?: Record<string, PolicyAction>;
}
