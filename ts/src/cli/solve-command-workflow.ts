import { mkdirSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

export const SOLVE_HELP_TEXT = `autoctx solve — create and solve a scenario from a plain-language description

Usage:
  autoctx solve "..." [--iterations N] [--family name] [--json]
  autoctx solve --description "..." [--gens N] [--family name] [--json]

Options:
  <text>                     Plain-language scenario/problem description
  -d, --description <text>   Same description as a named option
  -g, --gens <N>             Generations to run (default: 5)
  --iterations <N>           Plain-language alias for --gens
  --family <name>            Force a scenario family before creation/routing
  --timeout <seconds>        Maximum time to wait for solve completion (default: 300)
  --generation-time-budget <seconds>
                              Soft per-generation solve runtime budget (0 = unlimited)
  --output <path>            Write the solved package JSON to a file
  --json                     Output structured JSON
  -h, --help                 Show this help

Examples:
  autoctx solve "improve customer-support replies for billing disputes" --iterations 3
  autoctx solve -d "investigate a production outage from logs" --family investigation --gens 2 --json`;

export interface SolveCommandValues {
  description?: string;
  positionals?: string[];
  gens?: string;
  iterations?: string;
  timeout?: string;
  "generation-time-budget"?: string;
  family?: string;
  output?: string;
  json?: boolean;
}

export interface SolveCommandPlan {
  description: string;
  generations: number;
  timeoutMs: number;
  generationTimeBudgetSeconds: number | null;
  familyOverride: string | null;
  outputPath: string | null;
  json: boolean;
}

export interface SolveManagerLike {
  submit(
    description: string,
    generations: number,
    opts?: {
      familyOverride?: string;
      generationTimeBudgetSeconds?: number | null;
    },
  ): string;
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
  generationTimeBudgetSeconds: number | null;
  outputPath: string | null;
  llmClassifierFallbackUsed: boolean;
  progress: number;
  result: Record<string, unknown>;
}

const DEFAULT_TIMEOUT_MS = 300_000;
const DEFAULT_POLL_INTERVAL_MS = 250;

export function planSolveCommand(
  values: SolveCommandValues,
  parsePositiveInteger: (raw: string | undefined, label: string) => number,
): SolveCommandPlan {
  const positionalDescription = values.positionals?.join(" ").trim();
  const description = values.description?.trim() || positionalDescription;
  if (!description) {
    throw new Error(
      "Error: --description is required. You can also run 'autoctx solve \"plain-language goal\"'. Run 'autoctx solve --help' for usage.",
    );
  }

  const timeoutMs = values.timeout
    ? parsePositiveInteger(values.timeout, "--timeout") * 1000
    : DEFAULT_TIMEOUT_MS;
  const generationTimeBudgetSeconds = values["generation-time-budget"] === undefined
    ? null
    : parseNonNegativeInteger(values["generation-time-budget"], "--generation-time-budget");

  const generationsRaw = values.gens ?? values.iterations;
  const generationsLabel = values.gens ? "--gens" : "--iterations";

  return {
    description,
    generations: generationsRaw ? parsePositiveInteger(generationsRaw, generationsLabel) : 5,
    timeoutMs,
    generationTimeBudgetSeconds,
    familyOverride: values.family?.trim() ? values.family.trim() : null,
    outputPath: values.output?.trim() ? values.output.trim() : null,
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
  const jobId = opts.manager.submit(opts.plan.description, opts.plan.generations, {
    familyOverride: opts.plan.familyOverride ?? undefined,
    generationTimeBudgetSeconds: opts.plan.generationTimeBudgetSeconds,
  });

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
    generationTimeBudgetSeconds: nullableNumberOrDefault(
      status.generationTimeBudgetSeconds ?? status.generation_time_budget_seconds,
      opts.plan.generationTimeBudgetSeconds,
    ),
    outputPath: opts.plan.outputPath,
    llmClassifierFallbackUsed: Boolean(
      status.llmClassifierFallbackUsed ?? status.llm_classifier_fallback_used,
    ),
    progress: numberOrDefault(status.progress, 0),
    result,
  };
}

export function writeSolveOutputFile(result: Record<string, unknown>, outputPath: string): void {
  mkdirSync(dirname(outputPath), { recursive: true });
  writeFileSync(outputPath, JSON.stringify(result, null, 2) + "\n", "utf-8");
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
    ...(summary.outputPath ? [`  Output: ${summary.outputPath}`] : []),
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

function nullableNumberOrDefault(value: unknown, fallback: number | null): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function parseNonNegativeInteger(raw: string | undefined, label: string): number {
  const parsed = Number.parseInt(raw ?? "", 10);
  if (!Number.isInteger(parsed) || parsed < 0) {
    throw new Error(`${label} must be a non-negative integer`);
  }
  return parsed;
}
