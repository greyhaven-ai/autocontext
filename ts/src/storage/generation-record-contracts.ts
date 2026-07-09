export interface UpsertGenerationRecordOpts {
  meanScore: number;
  bestScore: number;
  elo: number;
  wins: number;
  losses: number;
  gateDecision: string;
  status: string;
  durationSeconds?: number | null;
  dimensionSummaryJson?: string | null;
  scoringBackend?: string;
  ratingUncertainty?: number | null;
  evaluatorEpoch?: string | null;
}

export interface RecordMatchRecordOpts {
  seed: number;
  score: number;
  passedValidation: boolean;
  validationErrors: string;
  winner?: string;
  strategyJson?: string;
  replayJson?: string;
}
