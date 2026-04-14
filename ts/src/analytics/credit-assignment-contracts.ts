export interface ComponentChangeDict {
  component: string;
  magnitude: number;
  description: string;
  metadata: Record<string, unknown>;
}

export interface GenerationChangeVectorDict {
  generation: number;
  score_delta: number;
  changes: ComponentChangeDict[];
  metadata: Record<string, unknown>;
}

export interface AttributionResultDict {
  generation: number;
  total_delta: number;
  credits: Record<string, number>;
  metadata: Record<string, unknown>;
}

export interface CreditAssignmentRecordDict {
  run_id: string;
  generation: number;
  vector: GenerationChangeVectorDict;
  attribution: AttributionResultDict;
  metadata: Record<string, unknown>;
}

export interface CreditPatternComponentSummary {
  component: string;
  generationCount: number;
  positiveGenerationCount: number;
  totalCredit: number;
  totalChangeMagnitude: number;
  averageCredit: number;
  averageShare: number;
}

export interface CreditPatternSummary {
  totalRecords: number;
  runCount: number;
  runIds: string[];
  components: CreditPatternComponentSummary[];
}
