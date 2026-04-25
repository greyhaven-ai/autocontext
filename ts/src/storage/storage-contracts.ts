export interface TaskQueueRow {
  id: string;
  spec_name: string;
  status: string;
  priority: number;
  config_json: string | null;
  scheduled_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  best_score: number | null;
  best_output: string | null;
  total_rounds: number | null;
  met_threshold: number;
  result_json: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface HumanFeedbackRow {
  id: number;
  scenario_name: string;
  generation_id: string | null;
  agent_output: string;
  human_score: number | null;
  human_notes: string;
  created_at: string;
}

export interface RunRow {
  run_id: string;
  scenario: string;
  target_generations: number;
  executor_mode: string;
  status: string;
  agent_provider: string;
  created_at: string;
  updated_at: string;
}

export interface GenerationRow {
  run_id: string;
  generation_index: number;
  mean_score: number;
  best_score: number;
  elo: number;
  wins: number;
  losses: number;
  gate_decision: string;
  status: string;
  duration_seconds: number | null;
  dimension_summary_json: string | null;
  scoring_backend: string;
  rating_uncertainty: number | null;
  created_at: string;
  updated_at: string;
}

export interface MatchRow {
  id: number;
  run_id: string;
  generation_index: number;
  seed: number;
  score: number;
  passed_validation: number;
  validation_errors: string;
  winner: string;
  strategy_json: string;
  replay_json: string;
  created_at: string;
}

export interface AgentOutputRow {
  id: number;
  run_id: string;
  generation_index: number;
  role: string;
  content: string;
  created_at: string;
}

export interface TrajectoryRow {
  generation_index: number;
  mean_score: number;
  best_score: number;
  elo: number;
  gate_decision: string;
  delta: number;
  dimension_summary: Record<string, unknown>;
  scoring_backend: string;
  rating_uncertainty: number | null;
}

export interface NotebookRow {
  session_id: string;
  scenario_name: string;
  current_objective: string;
  current_hypotheses: string[];
  best_run_id: string | null;
  best_generation: number | null;
  best_score: number | null;
  unresolved_questions: string[];
  operator_observations: string[];
  follow_ups: string[];
  created_at: string;
  updated_at: string;
}

export interface UpsertNotebookOpts {
  sessionId: string;
  scenarioName: string;
  currentObjective?: string | null;
  currentHypotheses?: string[] | null;
  bestRunId?: string | null;
  bestGeneration?: number | null;
  bestScore?: number | null;
  unresolvedQuestions?: string[] | null;
  operatorObservations?: string[] | null;
  followUps?: string[] | null;
}

export interface HubSessionRow {
  session_id: string;
  owner: string;
  status: string;
  lease_expires_at: string;
  last_heartbeat_at: string;
  shared: boolean;
  external_link: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface UpsertHubSessionOpts {
  owner?: string | null;
  status?: string | null;
  leaseExpiresAt?: string | null;
  lastHeartbeatAt?: string | null;
  shared?: boolean | null;
  externalLink?: string | null;
  metadata?: Record<string, unknown> | null;
}

export interface HubPackageRecordRow {
  package_id: string;
  scenario_name: string;
  scenario_family: string;
  source_run_id: string;
  source_generation: number;
  title: string;
  description: string;
  promotion_level: string;
  best_score: number;
  best_elo: number;
  payload_path: string;
  strategy_package_path: string;
  tags: string[];
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface SaveHubPackageRecordOpts {
  packageId: string;
  scenarioName: string;
  scenarioFamily: string;
  sourceRunId: string;
  sourceGeneration: number;
  title: string;
  description: string;
  promotionLevel: string;
  bestScore: number;
  bestElo: number;
  payloadPath: string;
  strategyPackagePath: string;
  tags: string[];
  metadata?: Record<string, unknown>;
  createdAt?: string;
}

export interface HubResultRecordRow {
  result_id: string;
  scenario_name: string;
  run_id: string;
  package_id: string | null;
  title: string;
  best_score: number;
  best_elo: number;
  payload_path: string;
  tags: string[];
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface SaveHubResultRecordOpts {
  resultId: string;
  scenarioName: string;
  runId: string;
  packageId?: string | null;
  title: string;
  bestScore: number;
  bestElo: number;
  payloadPath: string;
  tags: string[];
  metadata?: Record<string, unknown>;
  createdAt?: string;
}

export interface HubPromotionRecordRow {
  event_id: string;
  package_id: string;
  source_run_id: string;
  action: string;
  actor: string;
  label: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface SaveHubPromotionRecordOpts {
  eventId: string;
  packageId: string;
  sourceRunId: string;
  action: string;
  actor: string;
  label?: string | null;
  metadata?: Record<string, unknown>;
  createdAt?: string;
}

export interface MonitorConditionRow {
  id: string;
  name: string;
  condition_type: string;
  params: Record<string, unknown>;
  scope: string;
  active: number;
  created_at: string;
}

export interface MonitorAlertRow {
  id: string;
  condition_id: string;
  condition_name: string;
  condition_type: string;
  scope: string;
  detail: string;
  payload: Record<string, unknown>;
  fired_at: string;
}

export interface InsertMonitorConditionOpts {
  id: string;
  name: string;
  conditionType: string;
  params?: Record<string, unknown>;
  scope?: string;
  active?: boolean;
}

export interface InsertMonitorAlertOpts {
  id: string;
  conditionId: string;
  conditionName: string;
  conditionType: string;
  scope?: string;
  detail?: string;
  payload?: Record<string, unknown>;
  firedAt?: string;
}

export interface UpsertGenerationOpts {
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
}

export interface RecordMatchOpts {
  seed: number;
  score: number;
  passedValidation: boolean;
  validationErrors: string;
  winner?: string;
  strategyJson?: string;
  replayJson?: string;
}
