export const RUN_STATUS_HELP_TEXT = `autoctx status — show queue status or a run status

Usage:
  autoctx status
  autoctx status <run-id> [--json]
  autoctx status --run-id <run-id> [--json]

Examples:
  autoctx status
  autoctx status run-123`;

export const SHOW_HELP_TEXT = `autoctx show — show the best or latest generation for a run

Usage:
  autoctx show <run-id> [--best] [--json]
  autoctx show --run-id <run-id> [--generation N] [--json]

Examples:
  autoctx show run-123 --best
  autoctx show --run-id run-123 --generation 2 --json`;

export const WATCH_HELP_TEXT = `autoctx watch — follow a run until it finishes

Usage:
  autoctx watch <run-id> [--interval seconds] [--json]
  autoctx watch --run-id <run-id> [--interval seconds] [--json]

Examples:
  autoctx watch run-123
  autoctx watch --run-id run-123 --interval 2`;

export interface RunInspectionRun {
  run_id: string;
  scenario: string;
  target_generations: number;
  executor_mode: string;
  status: string;
  agent_provider: string;
  created_at: string;
  updated_at: string;
}

export interface RunInspectionGeneration {
  generation_index: number;
  mean_score: number;
  best_score: number;
  elo: number;
  gate_decision: string;
  status: string;
  duration_seconds: number | null;
  created_at: string;
  updated_at: string;
}

export interface RunIdValues {
  "run-id"?: string;
}

export interface ShowValues extends RunIdValues {
  generation?: string;
  best?: boolean;
  json?: boolean;
}

export function resolveRunId(
  values: RunIdValues,
  positionals: string[],
  commandName: "status" | "show" | "watch",
): string {
  const runId = values["run-id"]?.trim() || positionals[0]?.trim();
  if (!runId) {
    throw new Error(`Error: ${commandName} needs a run id. Use 'autoctx ${commandName} <run-id>'.`);
  }
  return runId;
}

export function parseWatchIntervalSeconds(raw: string | undefined): number {
  const parsed = Number.parseFloat(raw ?? "2");
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error("Error: --interval must be a positive number of seconds");
  }
  return parsed;
}

export function renderRunStatus(
  run: RunInspectionRun,
  generations: RunInspectionGeneration[],
  json: boolean,
): string {
  const latest = latestGeneration(generations);
  if (json) {
    return JSON.stringify({ run, latest_generation: latest }, null, 2);
  }

  return [
    `Run ${run.run_id}`,
    `  Status: ${run.status}`,
    `  Scenario: ${run.scenario}`,
    `  Generations: ${generations.length}/${run.target_generations}`,
    latest ? `  Latest best score: ${formatScore(latest.best_score)} (generation ${latest.generation_index})` : null,
    latest ? `  Latest gate: ${latest.gate_decision}` : null,
  ].filter((line): line is string => line !== null).join("\n");
}

export function renderRunShow(
  run: RunInspectionRun,
  generations: RunInspectionGeneration[],
  values: ShowValues,
): string {
  const generation = selectGeneration(generations, values);
  if (!generation) {
    throw new Error(`Error: run '${run.run_id}' has no generations yet`);
  }

  if (values.json) {
    return JSON.stringify({ run, generation }, null, 2);
  }

  return [
    `Run ${run.run_id}`,
    `  Scenario: ${run.scenario}`,
    `  Generation: ${generation.generation_index}`,
    `  Status: ${generation.status}`,
    `  Best score: ${formatScore(generation.best_score)}`,
    `  Mean score: ${formatScore(generation.mean_score)}`,
    `  ELO: ${formatScore(generation.elo)}`,
    `  Gate: ${generation.gate_decision}`,
  ].join("\n");
}

function selectGeneration(
  generations: RunInspectionGeneration[],
  values: ShowValues,
): RunInspectionGeneration | null {
  if (values.generation) {
    const requested = Number.parseInt(values.generation, 10);
    if (!Number.isInteger(requested) || requested <= 0) {
      throw new Error("Error: --generation must be a positive integer");
    }
    return generations.find((generation) => generation.generation_index === requested) ?? null;
  }

  return values.best ? bestGeneration(generations) : latestGeneration(generations);
}

function latestGeneration(generations: RunInspectionGeneration[]): RunInspectionGeneration | null {
  return generations.reduce<RunInspectionGeneration | null>(
    (latest, generation) =>
      !latest || generation.generation_index > latest.generation_index ? generation : latest,
    null,
  );
}

function bestGeneration(generations: RunInspectionGeneration[]): RunInspectionGeneration | null {
  return generations.reduce<RunInspectionGeneration | null>(
    (best, generation) =>
      !best || generation.best_score > best.best_score ? generation : best,
    null,
  );
}

function formatScore(score: number): string {
  return Number.isFinite(score) ? score.toFixed(3) : String(score);
}
