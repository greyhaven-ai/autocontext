export interface TrainingRecord {
  run_id: string;
  scenario: string;
  generation_index: number;
  strategy: string;
  score: number;
  gate_decision: string;
  context: Record<string, unknown>;
}

export interface MatchRecord {
  run_id: string;
  generation_index: number;
  seed: number;
  score: number;
  passed_validation: boolean;
  validation_errors: string;
}

export interface ExportOpts {
  runId?: string;
  scenario?: string;
  keptOnly?: boolean;
  includeMatches?: boolean;
  onProgress?: (progress: ExportProgress) => void;
}

export type TrainingExportRecord = TrainingRecord | MatchRecord;

export interface ExportProgress {
  phase: "start" | "run" | "generation";
  totalRuns: number;
  runIndex: number;
  runId: string;
  scenario: string;
  generationIndex?: number;
  recordsEmitted: number;
}

export interface ExportRunRef {
  run_id: string;
  scenario: string;
}
