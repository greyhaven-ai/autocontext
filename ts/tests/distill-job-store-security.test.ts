import {
  chmodSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
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
});
