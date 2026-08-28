import {
  chmodSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  symlinkSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  DistillJobStore,
  ensureSafeDistillJobId,
  MAX_DISTILL_JOB_FILES,
} from "../src/openclaw/distill-job-store.js";

const roots: string[] = [];

function root(prefix: string): string {
  const value = mkdtempSync(join(tmpdir(), prefix));
  roots.push(value);
  return value;
}

afterEach(() => {
  for (const value of roots.splice(0)) rmSync(value, { recursive: true, force: true });
});

describe("DistillJobStore path and persistence security", () => {
  it.each([
    "",
    "../outside",
    "..%2Foutside",
    "0123456789abcdef0123456789abcde/",
    "0123456789ABCDEF0123456789ABCDEF",
    "0".repeat(31),
    "0".repeat(33),
  ])("rejects non-canonical job ID %j", (jobId) => {
    expect(() => ensureSafeDistillJobId(jobId)).toThrow("job ID");
  });

  it("rejects a symbolic-link storage root and jobs directory", () => {
    const container = root("autoctx-distill-root-link-");
    const outside = join(container, "outside");
    mkdirSync(outside);
    const rootLink = join(container, "knowledge");
    symlinkSync(outside, rootLink, "dir");
    expect(() => new DistillJobStore(rootLink).createJob({ scenario: "grid_ctf" }))
      .toThrow("symbolic-link");

    const knowledge = join(container, "knowledge-2");
    mkdirSync(knowledge);
    symlinkSync(outside, join(knowledge, "_openclaw_distill_jobs"), "dir");
    expect(() => new DistillJobStore(knowledge).createJob({ scenario: "grid_ctf" }))
      .toThrow("symbolic-link");
  });

  it("rejects a symbolic-link job file without reading or overwriting its target", () => {
    const container = root("autoctx-distill-file-link-");
    const knowledge = join(container, "knowledge");
    const store = new DistillJobStore(knowledge);
    const job = store.createJob({ scenario: "grid_ctf" });
    const jobPath = join(knowledge, "_openclaw_distill_jobs", `${job.job_id}.json`);
    const sentinel = join(container, "sentinel.json");
    writeFileSync(sentinel, "sentinel", "utf-8");
    unlinkSync(jobPath);
    symlinkSync(sentinel, jobPath);

    expect(() => store.getJob(job.job_id)).toThrow("symbolic-link");
    expect(() => store.transition(job.job_id, "failed", { errorMessage: "failed" }))
      .toThrow("symbolic-link");
    expect(readFileSync(sentinel, "utf-8")).toBe("sentinel");
  });

  it("does not trust a persisted job whose embedded ID differs from its filename", () => {
    const container = root("autoctx-distill-id-mismatch-");
    const knowledge = join(container, "knowledge");
    const store = new DistillJobStore(knowledge);
    const job = store.createJob({ scenario: "grid_ctf" });
    const jobPath = join(knowledge, "_openclaw_distill_jobs", `${job.job_id}.json`);
    writeFileSync(jobPath, JSON.stringify({ ...job, job_id: "f".repeat(32) }), "utf-8");

    expect(store.getJob(job.job_id)).toBeNull();
    expect(store.listJobs()).toEqual([]);
  });

  it("reads legacy dotted artifact IDs while keeping new jobs and results strict", () => {
    const container = root("autoctx-distill-legacy-artifacts-");
    const knowledge = join(container, "knowledge");
    const store = new DistillJobStore(knowledge);
    const legacy = store.createJob({ scenario: "grid_ctf" });
    const legacyPath = join(
      knowledge,
      "_openclaw_distill_jobs",
      `${legacy.job_id}.json`,
    );
    writeFileSync(legacyPath, JSON.stringify({
      ...legacy,
      status: "completed",
      source_artifact_ids: ["policy.v1"],
      result_artifact_id: "model.v1",
    }), "utf-8");

    expect(store.getJob(legacy.job_id)).toMatchObject({
      source_artifact_ids: ["policy.v1"],
      result_artifact_id: "model.v1",
    });
    expect(store.listJobs()).toHaveLength(1);
    expect(() => store.createJob({
      scenario: "grid_ctf",
      sourceArtifactIds: ["policy.v2"],
    })).toThrow("invalid artifact id");

    const strict = store.createJob({ scenario: "grid_ctf" });
    store.transition(strict.job_id, "running");
    expect(() => store.transition(strict.job_id, "completed", {
      resultArtifactId: "model.v2",
    })).toThrow("invalid artifact id");
  });

  it("treats explicit null terminal fields as omission without breaking invariants", () => {
    const container = root("autoctx-distill-null-terminal-");
    const store = new DistillJobStore(join(container, "knowledge"));
    const completedJob = store.createJob({ scenario: "grid_ctf" });
    store.transition(completedJob.job_id, "running", { resultArtifactId: "model-1" });

    expect(store.transition(completedJob.job_id, "completed", {
      resultArtifactId: null,
    })).toMatchObject({
      status: "completed",
      result_artifact_id: "model-1",
    });

    const failedJob = store.createJob({ scenario: "grid_ctf" });
    store.transition(failedJob.job_id, "running", { errorMessage: "worker failed" });
    expect(store.transition(failedJob.job_id, "failed", {
      errorMessage: null,
    })).toMatchObject({
      status: "failed",
      error_message: "worker failed",
    });
  });

  it("writes private job files atomically and enforces the serialized JSON cap", () => {
    const container = root("autoctx-distill-size-");
    const knowledge = join(container, "knowledge");
    const store = new DistillJobStore(knowledge);
    const job = store.createJob({ scenario: "grid_ctf" });
    const jobPath = join(knowledge, "_openclaw_distill_jobs", `${job.job_id}.json`);
    if (process.platform !== "win32") {
      expect(statSync(jobPath).mode & 0o777).toBe(0o600);
    }

    const oversizedStore = new DistillJobStore(join(container, "oversized"));
    expect(() => oversizedStore.createJob({
      scenario: "grid_ctf",
      trainingConfig: { padding: "x".repeat(512 * 1024) },
    })).toThrow("byte limit");
    expect(oversizedStore.listJobs()).toEqual([]);
  });

  it("refuses unreadable non-regular and symbolic-link directory entries", () => {
    const container = root("autoctx-distill-list-link-");
    const knowledge = join(container, "knowledge");
    const store = new DistillJobStore(knowledge);
    const job = store.createJob({ scenario: "grid_ctf" });
    const jobs = join(knowledge, "_openclaw_distill_jobs");
    const target = join(container, "target");
    writeFileSync(target, "sentinel", "utf-8");
    symlinkSync(target, join(jobs, "untrusted.json"));
    // Keep the real job readable so this specifically exercises directory enumeration.
    chmodSync(join(jobs, `${job.job_id}.json`), 0o600);

    expect(() => store.listJobs()).toThrow("symbolic-link entry");
  });

  it("counts non-file entries before admitting a job at the scan limit", () => {
    const container = root("autoctx-distill-capacity-");
    const knowledge = join(container, "knowledge");
    const jobs = join(knowledge, "_openclaw_distill_jobs");
    mkdirSync(jobs, { recursive: true });
    for (let index = 0; index < MAX_DISTILL_JOB_FILES - 1; index += 1) {
      const jobId = index.toString(16).padStart(32, "0");
      writeFileSync(join(jobs, `${jobId}.json`), "{}", "utf-8");
    }
    mkdirSync(join(jobs, "structural-entry"));
    const store = new DistillJobStore(knowledge);

    expect(() => store.createJob({ scenario: "grid_ctf" }))
      .toThrow(`reached ${MAX_DISTILL_JOB_FILES} file limit`);
    expect(readdirSync(jobs)).toHaveLength(MAX_DISTILL_JOB_FILES);
  }, 15_000);
});
