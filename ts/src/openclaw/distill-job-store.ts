import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { randomUUID } from "node:crypto";

export type DistillJobStatus = "pending" | "running" | "completed" | "failed";

export interface DistillJob {
  job_id: string;
  scenario: string;
  status: DistillJobStatus;
  source_artifact_ids: string[];
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  result_artifact_id: string | null;
  error_message: string | null;
  training_config: Record<string, unknown>;
  training_metrics: Record<string, unknown>;
}

export class DistillJobError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DistillJobError";
  }
}

const VALID_TRANSITIONS: Record<DistillJobStatus, ReadonlySet<DistillJobStatus>> = {
  pending: new Set(["running", "failed"]),
  running: new Set(["completed", "failed"]),
  completed: new Set(),
  failed: new Set(),
};

function nowIso(): string {
  return new Date().toISOString();
}

function createJobId(): string {
  return randomUUID().replace(/-/g, "");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function parseJob(raw: unknown): DistillJob | null {
  if (!isRecord(raw)) return null;
  if (typeof raw.job_id !== "string" || typeof raw.scenario !== "string") return null;
  if (!["pending", "running", "completed", "failed"].includes(String(raw.status))) return null;
  return {
    job_id: raw.job_id,
    scenario: raw.scenario,
    status: raw.status as DistillJobStatus,
    source_artifact_ids: Array.isArray(raw.source_artifact_ids)
      ? raw.source_artifact_ids.filter((entry): entry is string => typeof entry === "string")
      : [],
    created_at: typeof raw.created_at === "string" ? raw.created_at : nowIso(),
    started_at: typeof raw.started_at === "string" ? raw.started_at : null,
    completed_at: typeof raw.completed_at === "string" ? raw.completed_at : null,
    result_artifact_id: typeof raw.result_artifact_id === "string" ? raw.result_artifact_id : null,
    error_message: typeof raw.error_message === "string" ? raw.error_message : null,
    training_config: isRecord(raw.training_config) ? raw.training_config : {},
    training_metrics: isRecord(raw.training_metrics) ? raw.training_metrics : {},
  };
}

export class DistillJobStore {
  readonly #jobsDir: string;

  constructor(knowledgeRoot: string) {
    this.#jobsDir = join(knowledgeRoot, "_openclaw_distill_jobs");
  }

  createJob(opts: {
    scenario: string;
    sourceArtifactIds?: string[];
    trainingConfig?: Record<string, unknown>;
  }): DistillJob {
    const job: DistillJob = {
      job_id: createJobId(),
      scenario: opts.scenario,
      status: "pending",
      source_artifact_ids: opts.sourceArtifactIds ?? [],
      created_at: nowIso(),
      started_at: null,
      completed_at: null,
      result_artifact_id: null,
      error_message: null,
      training_config: opts.trainingConfig ?? {},
      training_metrics: {},
    };
    this.#writeJob(job);
    return job;
  }

  listJobs(scenario?: string): DistillJob[] {
    if (!existsSync(this.#jobsDir)) {
      return [];
    }
    return readdirSync(this.#jobsDir)
      .filter((name) => name.endsWith(".json"))
      .sort()
      .map((name) => this.#readJobFromPath(join(this.#jobsDir, name)))
      .filter((job): job is DistillJob => job !== null)
      .filter((job) => scenario === undefined || job.scenario === scenario);
  }

  getJob(jobId: string): DistillJob | null {
    return this.#readJobFromPath(this.#jobPath(jobId));
  }

  transition(
    jobId: string,
    targetStatus: DistillJobStatus,
    opts: {
      resultArtifactId?: string | null;
      errorMessage?: string | null;
      trainingMetrics?: Record<string, unknown> | null;
    } = {},
  ): DistillJob | null {
    const job = this.getJob(jobId);
    if (!job) return null;

    const allowed = VALID_TRANSITIONS[job.status];
    if (!allowed.has(targetStatus)) {
      throw new DistillJobError(
        `Invalid transition: ${job.status} -> ${targetStatus} (allowed: ${allowed.size > 0 ? [...allowed].join(", ") : "none"})`,
      );
    }
    if (targetStatus === "completed" && !(opts.resultArtifactId ?? job.result_artifact_id)) {
      throw new DistillJobError("Completed distill jobs require a result_artifact_id");
    }
    if (targetStatus === "failed" && !(opts.errorMessage ?? job.error_message)) {
      throw new DistillJobError("Failed distill jobs require an error_message");
    }

    const timestamp = nowIso();
    job.status = targetStatus;
    if (targetStatus === "running") {
      job.started_at = timestamp;
    }
    if (targetStatus === "completed" || targetStatus === "failed") {
      job.completed_at = timestamp;
    }
    if (opts.resultArtifactId !== undefined) {
      job.result_artifact_id = opts.resultArtifactId;
    }
    if (opts.errorMessage !== undefined) {
      job.error_message = opts.errorMessage;
    }
    if (opts.trainingMetrics !== undefined && opts.trainingMetrics !== null) {
      job.training_metrics = opts.trainingMetrics;
    }
    this.#writeJob(job);
    return job;
  }

  activeJobCount(): number {
    return this.listJobs().filter((job) => job.status === "pending" || job.status === "running").length;
  }

  #jobPath(jobId: string): string {
    return join(this.#jobsDir, `${jobId}.json`);
  }

  #readJobFromPath(path: string): DistillJob | null {
    if (!existsSync(path)) {
      return null;
    }
    try {
      return parseJob(JSON.parse(readFileSync(path, "utf-8")) as unknown);
    } catch {
      return null;
    }
  }

  #writeJob(job: DistillJob): void {
    mkdirSync(this.#jobsDir, { recursive: true });
    writeFileSync(this.#jobPath(job.job_id), JSON.stringify(job, null, 2) + "\n", "utf-8");
  }
}
