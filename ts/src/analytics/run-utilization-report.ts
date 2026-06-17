export interface RunUtilizationEventInput {
  event_id?: string;
  event_type?: string;
  event?: string;
  timestamp?: string;
  ts?: string;
  branch_id?: string;
  worker_id?: string;
  duration_seconds?: number;
  score?: number;
  verifier_passed?: boolean;
  outcome?: string;
  payload?: Record<string, unknown>;
}

export interface RunUtilizationRoleUsageInput {
  timestamp?: string;
  ts?: string;
  branch_id?: string;
  worker_id?: string;
  input_tokens?: number;
  output_tokens?: number;
  latency_ms?: number;
  model_wait_seconds?: number;
}

export interface UtilizationWindow {
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
}

export interface BranchUtilization {
  branch_count: number | null;
  max_parallel_branches: number | null;
  runner_capacity_seconds: number | null;
  active_runner_seconds: number | null;
  idle_runner_seconds: number | null;
  mean_runner_utilization: number | null;
}

export interface TokenUtilization {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  model_active_seconds: number | null;
  model_wait_seconds: number | null;
  mean_token_utilization: number | null;
  token_throughput_per_second: number | null;
  tokens_to_success: number | null;
}

export interface EvaluationUtilization {
  eval_count: number;
  eval_active_seconds: number | null;
  verifier_active_seconds: number | null;
  verifier_idle_seconds: number | null;
  eval_throughput_per_second: number | null;
}

export interface RunUtilizationReport {
  schema_version: 1;
  run_id: string;
  generated_at: string;
  window: UtilizationWindow;
  branch_utilization: BranchUtilization;
  token_utilization: TokenUtilization;
  evaluation_utilization: EvaluationUtilization;
}

export interface BuildRunUtilizationReportInput {
  runId: string;
  events: RunUtilizationEventInput[];
  roleUsage: RunUtilizationRoleUsageInput[];
  generatedAt?: string;
}

interface NormalizedUtilizationEvent {
  eventType: string;
  timestamp: string;
  branchId: string;
  durationSeconds: number | null;
  score: number | null;
  verifierPassed: boolean | null;
  outcome: string;
}

interface NormalizedRoleUsage {
  timestamp: string;
  branchId: string;
  inputTokens: number;
  outputTokens: number;
  latencyMs: number | null;
  modelWaitSeconds: number | null;
}

const RUNNER_ACTIVE = new Set(["runner_active", "worker_active"]);
const EVAL_FINISHED = new Set(["evaluation_finished", "eval_finished"]);
const EVAL_ACTIVE = new Set(["evaluation_finished", "eval_finished", "evaluation_active"]);
const VERIFIER_ACTIVE = new Set(["verifier_finished", "verification_finished", "verifier_active"]);

export function buildRunUtilizationReport(
  input: BuildRunUtilizationReportInput,
): RunUtilizationReport {
  const events = input.events.map(normalizeEvent);
  const roleUsage = input.roleUsage.map(normalizeUsage);
  const window = utilizationWindow(events, roleUsage);
  const branches = branchIds(events, roleUsage);
  const maxParallel = branches.size ? maxParallelBranches(events, window.completed_at) : null;
  const capacity =
    window.duration_seconds !== null && maxParallel
      ? round(window.duration_seconds * maxParallel)
      : null;
  const evalEvents = events.filter((event) => EVAL_FINISHED.has(event.eventType));
  const evalActive = sumDuration(events, EVAL_ACTIVE);
  const explicitRunnerActive = sumDuration(events, RUNNER_ACTIVE);
  const activeRunner = explicitRunnerActive ?? evalActive;
  const verifierActive = sumDuration(events, VERIFIER_ACTIVE);
  const inputTokens = roleUsage.reduce((total, row) => total + row.inputTokens, 0);
  const outputTokens = roleUsage.reduce((total, row) => total + row.outputTokens, 0);
  const totalTokens = inputTokens + outputTokens;
  const modelActive = sumUsageSeconds(roleUsage, "latencyMs", 1000);
  const modelWait = sumUsageSeconds(roleUsage, "modelWaitSeconds", 1);

  return parseRunUtilizationReport({
    schema_version: 1,
    run_id: input.runId,
    generated_at: input.generatedAt ?? new Date().toISOString(),
    window,
    branch_utilization: {
      branch_count: branches.size || null,
      max_parallel_branches: maxParallel,
      runner_capacity_seconds: capacity,
      active_runner_seconds: activeRunner,
      idle_runner_seconds: diffOrNull(capacity, activeRunner),
      mean_runner_utilization: ratio(activeRunner, capacity),
    },
    token_utilization: {
      input_tokens: inputTokens,
      output_tokens: outputTokens,
      total_tokens: totalTokens,
      model_active_seconds: modelActive,
      model_wait_seconds: modelWait,
      mean_token_utilization: ratio(modelActive, capacity),
      token_throughput_per_second: ratio(totalTokens, modelActive),
      tokens_to_success: tokensToSuccess(events, roleUsage),
    },
    evaluation_utilization: {
      eval_count: evalEvents.length,
      eval_active_seconds: evalActive,
      verifier_active_seconds: verifierActive,
      verifier_idle_seconds: diffOrNull(capacity, verifierActive),
      eval_throughput_per_second: ratio(evalEvents.length, window.duration_seconds),
    },
  });
}

export function parseRunUtilizationReport(value: unknown): RunUtilizationReport {
  const report = record(value, "utilization report");
  exact(report, [
    "schema_version",
    "run_id",
    "generated_at",
    "window",
    "branch_utilization",
    "token_utilization",
    "evaluation_utilization",
  ]);
  if (report.schema_version !== 1) throw new Error("schema_version must be 1");
  return {
    schema_version: 1,
    run_id: string(report.run_id, "run_id"),
    generated_at: string(report.generated_at, "generated_at"),
    window: parseWindow(report.window),
    branch_utilization: parseBranchUtilization(report.branch_utilization),
    token_utilization: parseTokenUtilization(report.token_utilization),
    evaluation_utilization: parseEvaluationUtilization(report.evaluation_utilization),
  };
}

function parseWindow(value: unknown): UtilizationWindow {
  const item = record(value, "window");
  exact(item, ["started_at", "completed_at", "duration_seconds"]);
  return {
    started_at: nullableString(item.started_at, "started_at"),
    completed_at: nullableString(item.completed_at, "completed_at"),
    duration_seconds: nullableNumber(item.duration_seconds, "duration_seconds"),
  };
}

function parseBranchUtilization(value: unknown): BranchUtilization {
  const item = record(value, "branch utilization");
  exact(item, [
    "branch_count",
    "max_parallel_branches",
    "runner_capacity_seconds",
    "active_runner_seconds",
    "idle_runner_seconds",
    "mean_runner_utilization",
  ]);
  return {
    branch_count: nullableInteger(item.branch_count, "branch_count"),
    max_parallel_branches: nullableInteger(item.max_parallel_branches, "max_parallel_branches"),
    runner_capacity_seconds: nullableNumber(
      item.runner_capacity_seconds,
      "runner_capacity_seconds",
    ),
    active_runner_seconds: nullableNumber(item.active_runner_seconds, "active_runner_seconds"),
    idle_runner_seconds: nullableNumber(item.idle_runner_seconds, "idle_runner_seconds"),
    mean_runner_utilization: nullableNumber(
      item.mean_runner_utilization,
      "mean_runner_utilization",
    ),
  };
}

function parseTokenUtilization(value: unknown): TokenUtilization {
  const item = record(value, "token utilization");
  exact(item, [
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "model_active_seconds",
    "model_wait_seconds",
    "mean_token_utilization",
    "token_throughput_per_second",
    "tokens_to_success",
  ]);
  return {
    input_tokens: nonNegativeInteger(item.input_tokens, "input_tokens"),
    output_tokens: nonNegativeInteger(item.output_tokens, "output_tokens"),
    total_tokens: nonNegativeInteger(item.total_tokens, "total_tokens"),
    model_active_seconds: nullableNumber(item.model_active_seconds, "model_active_seconds"),
    model_wait_seconds: nullableNumber(item.model_wait_seconds, "model_wait_seconds"),
    mean_token_utilization: nullableNumber(item.mean_token_utilization, "mean_token_utilization"),
    token_throughput_per_second: nullableNumber(
      item.token_throughput_per_second,
      "token_throughput_per_second",
    ),
    tokens_to_success: nullableNonNegativeInteger(item.tokens_to_success, "tokens_to_success"),
  };
}

function parseEvaluationUtilization(value: unknown): EvaluationUtilization {
  const item = record(value, "evaluation utilization");
  exact(item, [
    "eval_count",
    "eval_active_seconds",
    "verifier_active_seconds",
    "verifier_idle_seconds",
    "eval_throughput_per_second",
  ]);
  return {
    eval_count: nonNegativeInteger(item.eval_count, "eval_count"),
    eval_active_seconds: nullableNumber(item.eval_active_seconds, "eval_active_seconds"),
    verifier_active_seconds: nullableNumber(
      item.verifier_active_seconds,
      "verifier_active_seconds",
    ),
    verifier_idle_seconds: nullableNumber(item.verifier_idle_seconds, "verifier_idle_seconds"),
    eval_throughput_per_second: nullableNumber(
      item.eval_throughput_per_second,
      "eval_throughput_per_second",
    ),
  };
}

function normalizeEvent(event: RunUtilizationEventInput): NormalizedUtilizationEvent {
  const payload = event.payload ?? {};
  const score = event.score ?? maybeNumber(payload.score);
  const verifierPassed = event.verifier_passed ?? maybeBoolean(payload.verifier_passed) ?? null;
  return {
    eventType: event.event_type ?? event.event ?? "",
    timestamp: event.timestamp ?? event.ts ?? "",
    branchId:
      event.branch_id ??
      maybeString(payload.branch_id) ??
      event.worker_id ??
      maybeString(payload.worker_id) ??
      "",
    durationSeconds: event.duration_seconds ?? maybeNumber(payload.duration_seconds) ?? null,
    score: score ?? null,
    verifierPassed,
    outcome: event.outcome ?? maybeString(payload.outcome) ?? "",
  };
}

function normalizeUsage(row: RunUtilizationRoleUsageInput): NormalizedRoleUsage {
  return {
    timestamp: row.timestamp ?? row.ts ?? "",
    branchId: row.branch_id ?? row.worker_id ?? "",
    inputTokens: integerOrZero(row.input_tokens),
    outputTokens: integerOrZero(row.output_tokens),
    latencyMs: maybeNumber(row.latency_ms) ?? null,
    modelWaitSeconds: maybeNumber(row.model_wait_seconds) ?? null,
  };
}

function utilizationWindow(
  events: NormalizedUtilizationEvent[],
  usage: NormalizedRoleUsage[],
): UtilizationWindow {
  const stamps = [...events.map((event) => event.timestamp), ...usage.map((row) => row.timestamp)]
    .map(parseTime)
    .filter((stamp): stamp is number => stamp !== null);
  if (!stamps.length) return { started_at: null, completed_at: null, duration_seconds: null };
  const start = Math.min(...stamps);
  const end = Math.max(...stamps);
  return {
    started_at: formatTime(start),
    completed_at: formatTime(end),
    duration_seconds: round((end - start) / 1000),
  };
}

function branchIds(
  events: NormalizedUtilizationEvent[],
  usage: NormalizedRoleUsage[],
): Set<string> {
  return new Set(
    [...events.map((event) => event.branchId), ...usage.map((row) => row.branchId)].filter(Boolean),
  );
}

function maxParallelBranches(
  events: NormalizedUtilizationEvent[],
  completedAt: string | null,
): number | null {
  const starts = new Map<string, number>();
  const finishes = new Map<string, number>();
  for (const event of events) {
    const timestamp = parseTime(event.timestamp);
    if (!event.branchId || timestamp === null) continue;
    if (event.eventType === "branch_started") starts.set(event.branchId, timestamp);
    if (event.eventType === "branch_finished") finishes.set(event.branchId, timestamp);
  }
  const fallbackEnd = completedAt ? parseTime(completedAt) : null;
  const points: Array<[number, number]> = [];
  for (const [branch, start] of starts) {
    points.push([start, 1]);
    const end = finishes.get(branch) ?? fallbackEnd;
    if (end !== null) points.push([end, -1]);
  }
  if (!points.length) return branchIds(events, [] as NormalizedRoleUsage[]).size || null;
  let current = 0;
  let maxSeen = 0;
  for (const [, delta] of points.sort((left, right) => left[0] - right[0] || left[1] - right[1])) {
    current += delta;
    maxSeen = Math.max(maxSeen, current);
  }
  return maxSeen || null;
}

function sumDuration(events: NormalizedUtilizationEvent[], eventTypes: Set<string>): number | null {
  const total = events
    .filter((event) => eventTypes.has(event.eventType) && event.durationSeconds !== null)
    .reduce((sum, event) => sum + (event.durationSeconds ?? 0), 0);
  return total > 0 ? round(total) : null;
}

function sumUsageSeconds(
  rows: NormalizedRoleUsage[],
  key: "latencyMs" | "modelWaitSeconds",
  scale: number,
): number | null {
  const values = rows.map((row) => row[key]).filter((value): value is number => value !== null);
  return values.length ? round(values.reduce((sum, value) => sum + value, 0) / scale) : null;
}

function tokensToSuccess(
  events: NormalizedUtilizationEvent[],
  usage: NormalizedRoleUsage[],
): number | null {
  const successTimes = events
    .filter((event) => event.verifierPassed === true || event.outcome === "success")
    .map((event) => parseTime(event.timestamp))
    .filter((stamp): stamp is number => stamp !== null);
  if (!successTimes.length) return null;
  const firstSuccess = Math.min(...successTimes);
  return usage
    .filter((row) => {
      const timestamp = parseTime(row.timestamp);
      return timestamp !== null && timestamp <= firstSuccess;
    })
    .reduce((total, row) => total + row.inputTokens + row.outputTokens, 0);
}

function diffOrNull(left: number | null, right: number | null): number | null {
  return left !== null && right !== null ? round(Math.max(0, left - right)) : null;
}

function ratio(numerator: number | null, denominator: number | null): number | null {
  if (numerator === null || denominator === null || denominator === 0) return null;
  return round(numerator / denominator);
}

function round(value: number): number {
  return Math.round(value * 1_000_000) / 1_000_000;
}

function parseTime(value: string): number | null {
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : null;
}

function formatTime(timestamp: number): string {
  return new Date(timestamp).toISOString().replace(/\.000Z$/, "Z");
}

function record(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error(`${label} must be an object`);
  return value as Record<string, unknown>;
}

function exact(item: Record<string, unknown>, allowed: string[]): void {
  const allowedSet = new Set(allowed);
  const keys = Object.keys(item);
  const missing = allowed.filter((key) => !keys.includes(key));
  if (missing.length) throw new Error(`missing field(s): ${missing.sort().join(", ")}`);
  const extra = keys.filter((key) => !allowedSet.has(key));
  if (extra.length) throw new Error(`unexpected field(s): ${extra.sort().join(", ")}`);
}

function string(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) throw new Error(`${label} must be a string`);
  return value;
}

function nullableString(value: unknown, label: string): string | null {
  return value === null ? null : string(value, label);
}

function number(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value))
    throw new Error(`${label} must be a number`);
  return value;
}

function nullableNumber(value: unknown, label: string): number | null {
  return value === null ? null : number(value, label);
}

function integer(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isInteger(value))
    throw new Error(`${label} must be an integer`);
  return value;
}

function nullableInteger(value: unknown, label: string): number | null {
  return value === null ? null : integer(value, label);
}

function nonNegativeInteger(value: unknown, label: string): number {
  const result = integer(value, label);
  if (result < 0) throw new Error(`${label} must be a non-negative integer`);
  return result;
}

function nullableNonNegativeInteger(value: unknown, label: string): number | null {
  return value === null ? null : nonNegativeInteger(value, label);
}

function integerOrZero(value: unknown): number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : 0;
}

function maybeString(value: unknown): string | undefined {
  return typeof value === "string" && value.length ? value : undefined;
}

function maybeNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function maybeBoolean(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}
