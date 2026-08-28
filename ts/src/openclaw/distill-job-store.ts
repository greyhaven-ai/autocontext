import { randomUUID } from "node:crypto";

import {
  ensureSafeArtifactId,
  ensureSafeLegacyArtifactReadId,
} from "./artifact-contract.js";
import {
  countSecureDirectoryEntries,
  listSecureDirectoryNames,
  readSecureTextFile,
  writeSecureTextFile,
} from "../security/secure-local-files.js";

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

const JOB_DIRECTORY = "_openclaw_distill_jobs";
const VALID_JOB_ID = /^[a-f0-9]{32}$/;
const MAX_JOB_JSON_BYTES = 512 * 1024;
export const MAX_DISTILL_JOB_FILES = 10_000;
const MAX_SCENARIO_CHARS = 128;
const MAX_SOURCE_ARTIFACTS = 1_000;

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

function isSafeLegacyArtifactId(value: unknown): value is string {
  if (typeof value !== "string") return false;
  try {
    ensureSafeLegacyArtifactReadId(value);
    return true;
  } catch {
    return false;
  }
}

export function ensureSafeDistillJobId(jobId: string): string {
  if (!VALID_JOB_ID.test(jobId)) {
    throw new DistillJobError("distillation job ID must be 32 lowercase hexadecimal characters");
  }
  return jobId;
}

export function isDistillJobStatus(value: string): value is DistillJobStatus {
  return value === "pending" || value === "running" || value === "completed" || value === "failed";
}

function parseJob(raw: unknown, expectedJobId: string): DistillJob | null {
  if (!isRecord(raw)) return null;
  if (raw.job_id !== expectedJobId) return null;
  if (
    typeof raw.scenario !== "string"
    || !raw.scenario
    || raw.scenario.length > MAX_SCENARIO_CHARS
  ) return null;
  if (typeof raw.status !== "string" || !isDistillJobStatus(raw.status)) return null;
  if (!Array.isArray(raw.source_artifact_ids) || raw.source_artifact_ids.length > MAX_SOURCE_ARTIFACTS) return null;
  const sourceArtifactIds = raw.source_artifact_ids.filter(isSafeLegacyArtifactId);
  if (sourceArtifactIds.length !== raw.source_artifact_ids.length) return null;
  if (
    raw.result_artifact_id !== undefined
    && raw.result_artifact_id !== null
    && !isSafeLegacyArtifactId(raw.result_artifact_id)
  ) return null;
  return {
    job_id: expectedJobId,
    scenario: raw.scenario,
    status: raw.status,
    source_artifact_ids: [...sourceArtifactIds],
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
  readonly #knowledgeRoot: string;

  constructor(knowledgeRoot: string) {
    this.#knowledgeRoot = knowledgeRoot;
  }

  createJob(opts: {
    scenario: string;
    sourceArtifactIds?: string[];
    trainingConfig?: Record<string, unknown>;
  }): DistillJob {
    if (!opts.scenario || opts.scenario.length > MAX_SCENARIO_CHARS) {
      throw new DistillJobError(
        `distillation scenario must be 1-${MAX_SCENARIO_CHARS} characters`,
      );
    }
    const sourceArtifactIds = opts.sourceArtifactIds ?? [];
    if (sourceArtifactIds.length > MAX_SOURCE_ARTIFACTS) {
      throw new DistillJobError(
        `source_artifact_ids exceeds ${MAX_SOURCE_ARTIFACTS} item limit`,
      );
    }
    for (const artifactId of sourceArtifactIds) ensureSafeArtifactId(artifactId);
    const job: DistillJob = {
      job_id: createJobId(),
      scenario: opts.scenario,
      status: "pending",
      source_artifact_ids: [...sourceArtifactIds],
      created_at: nowIso(),
      started_at: null,
      completed_at: null,
      result_artifact_id: null,
      error_message: null,
      training_config: opts.trainingConfig ?? {},
      training_metrics: {},
    };
    this.#writeJob(job, false);
    return job;
  }

  listJobs(scenario?: string): DistillJob[] {
    return listSecureDirectoryNames(
      this.#knowledgeRoot,
      [JOB_DIRECTORY],
      MAX_DISTILL_JOB_FILES,
    )
      .filter((name) => name.endsWith(".json") && VALID_JOB_ID.test(name.slice(0, -5)))
      .sort()
      .map((name) => {
        const jobId = name.slice(0, -5);
        return this.#readJob(jobId);
      })
      .filter((job): job is DistillJob => job !== null)
      .filter((job) => scenario === undefined || job.scenario === scenario);
  }

  getJob(jobId: string): DistillJob | null {
    return this.#readJob(ensureSafeDistillJobId(jobId));
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
    const safeJobId = ensureSafeDistillJobId(jobId);
    const job = this.#readJob(safeJobId);
    if (!job) return null;
    if (opts.resultArtifactId !== undefined && opts.resultArtifactId !== null) {
      ensureSafeArtifactId(opts.resultArtifactId);
    }

    const allowed = VALID_TRANSITIONS[job.status];
    if (!allowed.has(targetStatus)) {
      throw new DistillJobError(
        `Invalid transition: ${job.status} -> ${targetStatus} (allowed: ${allowed.size > 0 ? [...allowed].join(", ") : "none"})`,
      );
    }
    const nextResultArtifactId = opts.resultArtifactId ?? job.result_artifact_id;
    const nextErrorMessage = opts.errorMessage ?? job.error_message;
    if (targetStatus === "completed" && !nextResultArtifactId) {
      throw new DistillJobError("Completed distill jobs require a result_artifact_id");
    }
    if (targetStatus === "failed" && !nextErrorMessage) {
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
    if (opts.resultArtifactId !== undefined && opts.resultArtifactId !== null) {
      job.result_artifact_id = opts.resultArtifactId;
    }
    if (opts.errorMessage !== undefined && opts.errorMessage !== null) {
      job.error_message = opts.errorMessage;
    }
    if (opts.trainingMetrics !== undefined && opts.trainingMetrics !== null) {
      job.training_metrics = opts.trainingMetrics;
    }
    this.#writeJob(job, true);
    return job;
  }

  activeJobCount(): number {
    return this.listJobs().filter((job) => job.status === "pending" || job.status === "running").length;
  }

  #readJob(jobId: string): DistillJob | null {
    const raw = readSecureTextFile(
      this.#knowledgeRoot,
      [JOB_DIRECTORY],
      `${jobId}.json`,
      MAX_JOB_JSON_BYTES,
    );
    if (raw === null) return null;
    try {
      return parseJob(JSON.parse(raw), jobId);
    } catch {
      return null;
    }
  }

  #writeJob(job: DistillJob, replace: boolean): void {
    const jobId = ensureSafeDistillJobId(job.job_id);
    let serialized: string;
    try {
      serialized = `${JSON.stringify(job, null, 2)}\n`;
    } catch {
      throw new DistillJobError("distillation job data must be JSON serializable");
    }
    if (Buffer.byteLength(serialized, "utf-8") > MAX_JOB_JSON_BYTES) {
      throw new DistillJobError(`distillation job exceeds ${MAX_JOB_JSON_BYTES} byte limit`);
    }
    if (!replace) {
      const entryCount = countSecureDirectoryEntries(
        this.#knowledgeRoot,
        [JOB_DIRECTORY],
        MAX_DISTILL_JOB_FILES,
      );
      if (entryCount >= MAX_DISTILL_JOB_FILES) {
        throw new DistillJobError(
          `distillation job store reached ${MAX_DISTILL_JOB_FILES} file limit`,
        );
      }
    }
    writeSecureTextFile(
      this.#knowledgeRoot,
      [JOB_DIRECTORY],
      `${jobId}.json`,
      serialized,
      { maxBytes: MAX_JOB_JSON_BYTES, replace },
    );
  }
}
