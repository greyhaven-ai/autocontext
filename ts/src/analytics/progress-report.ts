export const PROGRESS_MILESTONE_NAMES = [
  "first_valid_candidate",
  "first_passing_verifier",
  "first_advancement",
  "threshold_success",
] as const;

export type ProgressMilestoneName = (typeof PROGRESS_MILESTONE_NAMES)[number];

export interface RunProgressEvent {
  event_id: string;
  event_type: string;
  timestamp: string;
  generation_index?: number;
  hypothesis_node_id?: string;
  parent_hypothesis_node_id?: string;
  candidate_id?: string;
  score?: number;
  verifier_passed?: boolean;
  gate_decision?: string;
  decision?: string;
}

export interface RunProgressEventStreamRow {
  event?: string;
  ts?: string;
  seq?: number;
  payload?: Record<string, unknown>;
}

export type RunProgressEventInput = RunProgressEvent | RunProgressEventStreamRow;

export interface ProgressPoint {
  event_id: string;
  timestamp: string;
  elapsed_seconds: number;
  generation_index: number | null;
  hypothesis_node_id: string | null;
  candidate_id: string | null;
  score: number;
  best_score: number;
  improved: boolean;
  milestone_names: ProgressMilestoneName[];
}

export interface MilestoneTiming {
  name: ProgressMilestoneName;
  reached: boolean;
  event_id: string | null;
  timestamp: string | null;
  elapsed_seconds: number | null;
  generation_index: number | null;
  hypothesis_node_id: string | null;
  score: number | null;
}

export interface PassAtKSummary {
  k: number;
  trials_considered: number;
  successes: number;
  passed: boolean;
  best_score: number | null;
  threshold: number;
}

export interface BranchLineageEdge {
  parent_hypothesis_node_id: string;
  child_hypothesis_node_id: string;
  event_id: string;
  generation_index: number | null;
}

export interface RunProgressReport {
  schema_version: 1;
  run_id: string;
  generated_at: string;
  threshold: number;
  progress_points: ProgressPoint[];
  milestones: MilestoneTiming[];
  pass_at_k: PassAtKSummary[];
  branch_lineage: BranchLineageEdge[];
}

export interface BuildRunProgressReportInput {
  runId: string;
  events: RunProgressEventInput[];
  threshold: number;
  passAtKValues?: number[];
  generatedAt?: string;
}

export interface ProgressReportReference {
  run_id: string;
  threshold: number;
  best_score: number | null;
  milestones_reached: number;
  pass_at_k: PassAtKSummary[];
}

export function buildRunProgressReport(input: BuildRunProgressReportInput): RunProgressReport {
  const events = input.events.map(normalizeEvent).sort(eventSort);
  const start = startTime(events, input.generatedAt);
  const scored = events.filter((event) => scoreOf(event) !== null);
  const milestones = milestoneMap(events, scored, input.threshold, start);
  return parseRunProgressReport({
    schema_version: 1,
    run_id: input.runId,
    generated_at: input.generatedAt ?? new Date().toISOString(),
    threshold: input.threshold,
    progress_points: progressPoints(scored, milestones, start),
    milestones: PROGRESS_MILESTONE_NAMES.map((name) => milestones[name]),
    pass_at_k: passAtK(scored, input.threshold, input.passAtKValues ?? [1, 5, 10]),
    branch_lineage: branchLineage(events),
  });
}

export function parseRunProgressReport(value: unknown): RunProgressReport {
  const report = record(value, "progress report");
  exact(report, ["schema_version", "run_id", "generated_at", "threshold", "progress_points", "milestones", "pass_at_k", "branch_lineage"]);
  if (report.schema_version !== 1) throw new Error("schema_version must be 1");
  return {
    schema_version: 1,
    run_id: string(report.run_id, "run_id"),
    generated_at: string(report.generated_at, "generated_at"),
    threshold: number(report.threshold, "threshold"),
    progress_points: array(report.progress_points, "progress_points").map(parseProgressPoint),
    milestones: array(report.milestones, "milestones").map(parseMilestone),
    pass_at_k: array(report.pass_at_k, "pass_at_k").map(parsePassAtK),
    branch_lineage: array(report.branch_lineage, "branch_lineage").map(parseLineageEdge),
  };
}

export function progressReportReference(report: RunProgressReport): ProgressReportReference {
  return {
    run_id: report.run_id,
    threshold: report.threshold,
    best_score: report.progress_points.reduce<number | null>(
      (best, point) => (best === null || point.best_score > best ? point.best_score : best),
      null,
    ),
    milestones_reached: report.milestones.filter((milestone) => milestone.reached).length,
    pass_at_k: report.pass_at_k.map((summary) => ({ ...summary })),
  };
}

function normalizeEvent(event: RunProgressEventInput): RunProgressEvent {
  if (!("payload" in event) || !event.payload) return event as RunProgressEvent;
  const payload = event.payload;
  return {
    event_id: maybeString((event as RunProgressEvent).event_id) ?? (event.seq !== undefined ? `seq-${event.seq}` : ""),
    event_type: event.event ?? "",
    timestamp: event.ts ?? (event as RunProgressEvent).timestamp ?? "",
    generation_index: maybeNumber(payload.generation_index) ?? maybeNumber(payload.generation),
    hypothesis_node_id:
      maybeString(payload.hypothesis_node_id) ?? maybeString(payload.node_id) ?? maybeString(payload.child_id),
    parent_hypothesis_node_id: maybeString(payload.parent_hypothesis_node_id) ?? maybeString(payload.parent_id),
    candidate_id: maybeString(payload.candidate_id),
    score: maybeNumber(payload.score) ?? maybeNumber(payload.best_score),
    verifier_passed: maybeBoolean(payload.verifier_passed),
    gate_decision: maybeString(payload.gate_decision) ?? maybeString(payload.decision),
  };
}

function eventSort(a: RunProgressEvent, b: RunProgressEvent): number {
  return a.timestamp.localeCompare(b.timestamp) || a.event_id.localeCompare(b.event_id);
}

function startTime(events: RunProgressEvent[], generatedAt?: string): number {
  const timestamps = events.map((event) => Date.parse(event.timestamp)).filter(Number.isFinite);
  if (timestamps.length > 0) return Math.min(...timestamps);
  return generatedAt ? Date.parse(generatedAt) : Date.now();
}

function milestoneMap(
  events: RunProgressEvent[],
  scored: RunProgressEvent[],
  threshold: number,
  start: number,
): Record<ProgressMilestoneName, MilestoneTiming> {
  return {
    first_valid_candidate: milestone("first_valid_candidate", scored[0], start),
    first_passing_verifier: milestone("first_passing_verifier", scored.find((event) => event.verifier_passed), start),
    first_advancement: milestone(
      "first_advancement",
      events.find((event) => (event.gate_decision ?? event.decision) === "advance"),
      start,
    ),
    threshold_success: milestone(
      "threshold_success",
      scored.find((event) => (scoreOf(event) ?? 0) >= threshold),
      start,
    ),
  };
}

function milestone(name: ProgressMilestoneName, event: RunProgressEvent | undefined, start: number): MilestoneTiming {
  if (!event) return { name, reached: false, event_id: null, timestamp: null, elapsed_seconds: null, generation_index: null, hypothesis_node_id: null, score: null };
  return {
    name,
    reached: true,
    event_id: event.event_id,
    timestamp: event.timestamp,
    elapsed_seconds: elapsedSeconds(event.timestamp, start),
    generation_index: event.generation_index ?? null,
    hypothesis_node_id: event.hypothesis_node_id ?? null,
    score: scoreOf(event),
  };
}

function progressPoints(
  scored: RunProgressEvent[],
  milestones: Record<ProgressMilestoneName, MilestoneTiming>,
  start: number,
): ProgressPoint[] {
  const pointMilestones = new Map<string, ProgressMilestoneName[]>();
  for (const item of Object.values(milestones)) {
    if (!item.event_id) continue;
    pointMilestones.set(item.event_id, [...(pointMilestones.get(item.event_id) ?? []), item.name]);
  }

  let best = Number.NEGATIVE_INFINITY;
  return scored.map((event) => {
    const score = scoreOf(event) ?? 0;
    const improved = score > best;
    if (improved) best = score;
    return {
      event_id: event.event_id,
      timestamp: event.timestamp,
      elapsed_seconds: elapsedSeconds(event.timestamp, start),
      generation_index: event.generation_index ?? null,
      hypothesis_node_id: event.hypothesis_node_id ?? null,
      candidate_id: event.candidate_id ?? null,
      score,
      best_score: best,
      improved,
      milestone_names: pointMilestones.get(event.event_id) ?? [],
    };
  });
}

function passAtK(scored: RunProgressEvent[], threshold: number, values: number[]): PassAtKSummary[] {
  const scores = scored.map(scoreOf).filter((score): score is number => score !== null);
  return values.filter((k) => k > 0).map((k) => {
    const window = scores.slice(0, k);
    const successes = window.filter((score) => score >= threshold).length;
    return { k, trials_considered: window.length, successes, passed: successes > 0, best_score: window.length ? Math.max(...window) : null, threshold };
  });
}

function branchLineage(events: RunProgressEvent[]): BranchLineageEdge[] {
  const seen = new Set<string>();
  const edges: BranchLineageEdge[] = [];
  for (const event of events) {
    const parent = event.parent_hypothesis_node_id;
    const child = event.hypothesis_node_id;
    if (!parent || !child || parent === child) continue;
    const key = `${parent}\u0000${child}`;
    if (seen.has(key)) continue;
    seen.add(key);
    edges.push({ parent_hypothesis_node_id: parent, child_hypothesis_node_id: child, event_id: event.event_id, generation_index: event.generation_index ?? null });
  }
  return edges;
}

function parseProgressPoint(value: unknown): ProgressPoint {
  const item = record(value, "progress point");
  exact(item, ["event_id", "timestamp", "elapsed_seconds", "generation_index", "hypothesis_node_id", "candidate_id", "score", "best_score", "improved", "milestone_names"]);
  return {
    event_id: string(item.event_id, "event_id"),
    timestamp: string(item.timestamp, "timestamp"),
    elapsed_seconds: number(item.elapsed_seconds, "elapsed_seconds"),
    generation_index: nullableInteger(item.generation_index, "generation_index"),
    hypothesis_node_id: nullableString(item.hypothesis_node_id, "hypothesis_node_id"),
    candidate_id: nullableString(item.candidate_id, "candidate_id"),
    score: number(item.score, "score"),
    best_score: number(item.best_score, "best_score"),
    improved: boolean(item.improved, "improved"),
    milestone_names: array(item.milestone_names, "milestone_names").map(milestoneName),
  };
}

function parseMilestone(value: unknown): MilestoneTiming {
  const item = record(value, "milestone");
  exact(item, ["name", "reached", "event_id", "timestamp", "elapsed_seconds", "generation_index", "hypothesis_node_id", "score"]);
  return {
    name: milestoneName(item.name),
    reached: boolean(item.reached, "reached"),
    event_id: nullableString(item.event_id, "event_id"),
    timestamp: nullableString(item.timestamp, "timestamp"),
    elapsed_seconds: nullableNumber(item.elapsed_seconds, "elapsed_seconds"),
    generation_index: nullableInteger(item.generation_index, "generation_index"),
    hypothesis_node_id: nullableString(item.hypothesis_node_id, "hypothesis_node_id"),
    score: nullableNumber(item.score, "score"),
  };
}

function parsePassAtK(value: unknown): PassAtKSummary {
  const item = record(value, "pass_at_k");
  exact(item, ["k", "trials_considered", "successes", "passed", "best_score", "threshold"]);
  return {
    k: integer(item.k, "k"),
    trials_considered: integer(item.trials_considered, "trials_considered"),
    successes: integer(item.successes, "successes"),
    passed: boolean(item.passed, "passed"),
    best_score: nullableNumber(item.best_score, "best_score"),
    threshold: number(item.threshold, "threshold"),
  };
}

function parseLineageEdge(value: unknown): BranchLineageEdge {
  const item = record(value, "branch lineage edge");
  exact(item, ["parent_hypothesis_node_id", "child_hypothesis_node_id", "event_id", "generation_index"]);
  return {
    parent_hypothesis_node_id: string(item.parent_hypothesis_node_id, "parent_hypothesis_node_id"),
    child_hypothesis_node_id: string(item.child_hypothesis_node_id, "child_hypothesis_node_id"),
    event_id: string(item.event_id, "event_id"),
    generation_index: nullableInteger(item.generation_index, "generation_index"),
  };
}

function scoreOf(event: RunProgressEvent): number | null {
  return typeof event.score === "number" && Number.isFinite(event.score) ? event.score : null;
}

function elapsedSeconds(timestamp: string, start: number): number {
  return (Date.parse(timestamp) - start) / 1000;
}

function milestoneName(value: unknown): ProgressMilestoneName {
  if (typeof value !== "string" || !PROGRESS_MILESTONE_NAMES.includes(value as ProgressMilestoneName)) throw new Error("unknown milestone name");
  return value as ProgressMilestoneName;
}

function record(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} must be an object`);
  return value as Record<string, unknown>;
}

function exact(item: Record<string, unknown>, allowed: readonly string[]): void {
  const allowedSet = new Set(allowed);
  const extra = Object.keys(item).filter((key) => !allowedSet.has(key));
  if (extra.length) throw new Error(`unexpected field(s): ${extra.sort().join(", ")}`);
}

function string(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) throw new Error(`${label} must be a string`);
  return value;
}

function nullableString(value: unknown, label: string): string | null {
  return value === null || value === undefined ? null : string(value, label);
}

function number(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new Error(`${label} must be a number`);
  return value;
}

function nullableNumber(value: unknown, label: string): number | null {
  return value === null || value === undefined ? null : number(value, label);
}

function integer(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isInteger(value)) throw new Error(`${label} must be an integer`);
  return value;
}

function nullableInteger(value: unknown, label: string): number | null {
  return value === null || value === undefined ? null : integer(value, label);
}

function boolean(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") throw new Error(`${label} must be boolean`);
  return value;
}

function array(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`${label} must be an array`);
  return value;
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
