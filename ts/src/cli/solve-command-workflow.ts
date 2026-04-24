export const SOLVE_HELP_TEXT = `autoctx solve — create and solve a scenario from a plain-language description

Usage:
  autoctx solve --description "..." [--gens N] [--json]

Options:
  -d, --description <text>   Natural-language scenario/problem description
  -g, --gens <N>             Generations to run (default: 5)
  --timeout <seconds>        Maximum time to wait for solve completion (default: 300)
  --json                     Output structured JSON
  -h, --help                 Show this help

Examples:
  autoctx solve --description "improve customer-support replies for billing disputes" --gens 3
  autoctx solve -d "investigate a production outage from logs" --gens 2 --json`;

export interface SolveCommandValues {
  description?: string;
  gens?: string;
  timeout?: string;
  json?: boolean;
}

export interface SolveCommandPlan {
  description: string;
  generations: number;
  timeoutMs: number;
  json: boolean;
}

export interface SolveManagerLike {
  submit(description: string, generations: number): string;
  getStatus(jobId: string): Record<string, unknown>;
  getResult(jobId: string): Record<string, unknown> | null;
}

export interface SolveCommandSummary {
  jobId: string;
  status: string;
  description: string;
  scenarioName: string | null;
  family: string | null;
  generations: number;
  progress: number;
  result: Record<string, unknown>;
}

const DEFAULT_TIMEOUT_MS = 300_000;
const DEFAULT_POLL_INTERVAL_MS = 250;

export function planSolveCommand(
  values: SolveCommandValues,
  parsePositiveInteger: (raw: string | undefined, label: string) => number,
): SolveCommandPlan {
  const description = values.description?.trim();
  if (!description) {
    throw new Error("Error: --description is required. Run 'autoctx solve --help' for usage.");
  }

  const timeoutMs = values.timeout
    ? parsePositiveInteger(values.timeout, "--timeout") * 1000
    : DEFAULT_TIMEOUT_MS;

  return {
    description,
    generations: values.gens ? parsePositiveInteger(values.gens, "--gens") : 5,
    timeoutMs,
    json: Boolean(values.json),
  };
}

export async function executeSolveCommandWorkflow(opts: {
  manager: SolveManagerLike;
  plan: SolveCommandPlan;
  now?: () => number;
  sleep?: (ms: number) => Promise<void>;
  pollIntervalMs?: number;
}): Promise<SolveCommandSummary> {
  const now = opts.now ?? Date.now;
  const sleep = opts.sleep ?? ((ms) => new Promise<void>((resolve) => setTimeout(resolve, ms)));
  const pollIntervalMs = opts.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS;
  const deadline = now() + opts.plan.timeoutMs;
  const jobId = opts.manager.submit(opts.plan.description, opts.plan.generations);

  let status = opts.manager.getStatus(jobId);
  while (!isTerminalSolveStatus(status)) {
    if (now() >= deadline) {
      throw new Error(`Solve timed out waiting for job '${jobId}'`);
    }
    await sleep(pollIntervalMs);
    status = opts.manager.getStatus(jobId);
  }

  if (String(status.status) !== "completed") {
    throw new Error(String(status.error ?? `Solve failed with status '${String(status.status)}'`));
  }

  const result = opts.manager.getResult(jobId);
  if (!result) {
    throw new Error(`Solve job '${jobId}' completed without an exported result`);
  }

  return {
    jobId,
    status: String(status.status),
    description: String(status.description ?? opts.plan.description),
    scenarioName: stringOrNull(status.scenarioName),
    family: stringOrNull(status.family),
    generations: numberOrDefault(status.generations, opts.plan.generations),
    progress: numberOrDefault(status.progress, 0),
    result,
  };
}

export function renderSolveCommandSummary(summary: SolveCommandSummary, json: boolean): string {
  if (json) {
    return JSON.stringify(summary, null, 2);
  }

  return [
    "Solve completed",
    `  Job ID: ${summary.jobId}`,
    `  Scenario: ${summary.scenarioName ?? "unknown"}`,
    `  Family: ${summary.family ?? "unknown"}`,
    `  Generations: ${summary.generations}`,
    `  Progress: ${summary.progress}`,
  ].join("\n");
}

function isTerminalSolveStatus(status: Record<string, unknown>): boolean {
  return ["completed", "failed", "not_found"].includes(String(status.status ?? ""));
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function numberOrDefault(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}
