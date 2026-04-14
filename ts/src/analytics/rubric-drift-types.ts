export interface DelightSignalLike {
  signalType: string;
}

export interface RunFacetLike {
  scenario: string;
  bestScore: number;
  createdAt?: string;
  totalGenerations?: number;
  delightSignals?: DelightSignalLike[];
  retries?: number;
  rollbacks?: number;
}

export interface RubricSnapshot {
  snapshotId: string;
  createdAt: string;
  windowStart: string;
  windowEnd: string;
  runCount: number;
  meanScore: number;
  medianScore: number;
  stddevScore: number;
  minScore: number;
  maxScore: number;
  scoreInflationRate: number;
  perfectScoreRate: number;
  revisionJumpRate: number;
  retryRate: number;
  rollbackRate: number;
  release: string;
  scenarioFamily: string;
  agentProvider: string;
  metadata: Record<string, unknown>;
}

export interface DriftThresholds {
  maxScoreInflation: number;
  maxPerfectRate: number;
  maxRevisionJumpRate: number;
  minStddev: number;
  maxRetryRate: number;
  maxRollbackRate: number;
}

export interface DriftWarning {
  warningId: string;
  createdAt: string;
  warningType: string;
  severity: string;
  description: string;
  snapshotId: string;
  metricName: string;
  metricValue: number;
  thresholdValue: number;
  affectedScenarios: string[];
  affectedProviders: string[];
  affectedReleases: string[];
  metadata: Record<string, unknown>;
}

export interface DriftReport {
  snapshot: RubricSnapshot;
  warnings: DriftWarning[];
  stable: boolean;
  meanScore: number;
  scoreCount: number;
}
