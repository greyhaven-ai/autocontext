export interface SolveJob {
  jobId: string;
  description: string;
  generations: number;
  familyOverride?: string;
  generationTimeBudgetSeconds?: number | null;
  llmClassifierFallbackUsed?: boolean;
  status: "pending" | "creating_scenario" | "running" | "completed" | "failed";
  scenarioName?: string;
  family?: string;
  progress?: number;
  result?: Record<string, unknown>;
  error?: string;
}

export function createSolveJob(
  jobId: string,
  description: string,
  generations: number,
  opts: {
    familyOverride?: string;
    generationTimeBudgetSeconds?: number | null;
  } = {},
): SolveJob {
  return {
    jobId,
    description,
    generations,
    familyOverride: opts.familyOverride,
    generationTimeBudgetSeconds: opts.generationTimeBudgetSeconds,
    llmClassifierFallbackUsed: false,
    status: "pending",
  };
}

export function getSolveJobStatus(
  jobId: string,
  job?: SolveJob,
): Record<string, unknown> {
  if (!job) {
    return { status: "not_found", jobId, error: `Job '${jobId}' not found` };
  }

  return {
    jobId,
    status: job.status,
    description: job.description,
    scenarioName: job.scenarioName ?? null,
    family: job.family ?? null,
    familyOverride: job.familyOverride ?? null,
    generations: job.generations,
    generationTimeBudgetSeconds: job.generationTimeBudgetSeconds ?? null,
    generation_time_budget_seconds: job.generationTimeBudgetSeconds ?? null,
    llmClassifierFallbackUsed: job.llmClassifierFallbackUsed ?? false,
    llm_classifier_fallback_used: job.llmClassifierFallbackUsed ?? false,
    progress: job.progress ?? 0,
    error: job.error,
  };
}

export function getCompletedSolveJobResult(
  job?: SolveJob,
): Record<string, unknown> | null {
  if (!job || job.status !== "completed") {
    return null;
  }
  return job.result ?? null;
}

export function failSolveJob(job: SolveJob, error: unknown): void {
  job.status = "failed";
  job.error = error instanceof Error ? error.message : String(error);
}
