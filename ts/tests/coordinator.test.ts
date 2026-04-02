import { describe, expect, it } from "vitest";
import { Coordinator, CoordinatorEventType, Worker, WorkerStatus } from "../src/session/coordinator.js";

describe("Worker", () => {
  it("creates with pending status", () => {
    const w = Worker.create({ task: "Research auth", role: "researcher" });
    expect(w.workerId).toBeTruthy();
    expect(w.status).toBe(WorkerStatus.PENDING);
  });

  it("lifecycle: start → complete", () => {
    const w = Worker.create({ task: "t1", role: "r1" });
    w.start();
    expect(w.status).toBe(WorkerStatus.RUNNING);
    w.complete("Found 3 libraries");
    expect(w.status).toBe(WorkerStatus.COMPLETED);
    expect(w.result).toBe("Found 3 libraries");
  });

  it("failure", () => {
    const w = Worker.create({ task: "t1", role: "r1" });
    w.start();
    w.fail("API timeout");
    expect(w.status).toBe(WorkerStatus.FAILED);
  });

  it("redirect", () => {
    const w = Worker.create({ task: "wrong", role: "r1" });
    w.start();
    w.redirect("dead end");
    expect(w.status).toBe(WorkerStatus.REDIRECTED);
  });

  it("tracks lineage", () => {
    const w1 = Worker.create({ task: "t1", role: "r1" });
    const w2 = Worker.create({ task: "t2", role: "r1", parentWorkerId: w1.workerId });
    expect(w2.parentWorkerId).toBe(w1.workerId);
  });
});

describe("Coordinator", () => {
  it("delegates and tracks workers", () => {
    const coord = Coordinator.create("s1", "Build API");
    const w = coord.delegate("Research auth", "researcher");
    expect(coord.workers).toHaveLength(1);
    expect(w.status).toBe(WorkerStatus.PENDING);
  });

  it("fan-out creates multiple workers", () => {
    const coord = Coordinator.create("s1", "test");
    const workers = coord.fanOut([
      { task: "t1", role: "r1" },
      { task: "t2", role: "r1" },
      { task: "t3", role: "r1" },
    ]);
    expect(workers).toHaveLength(3);
    expect(coord.workers).toHaveLength(3);
  });

  it("fan-in collects completed results", () => {
    const coord = Coordinator.create("s1", "test");
    const workers = coord.fanOut([
      { task: "t1", role: "r1" },
      { task: "t2", role: "r1" },
    ]);
    workers[0].start(); workers[0].complete("result-1");
    workers[1].start(); workers[1].complete("result-2");
    expect(coord.fanIn()).toEqual(["result-1", "result-2"]);
  });

  it("active workers excludes completed", () => {
    const coord = Coordinator.create("s1", "test");
    const w1 = coord.delegate("t1", "r1");
    const w2 = coord.delegate("t2", "r1");
    w1.start(); w2.start(); w2.complete("done");
    expect(coord.activeWorkers).toHaveLength(1);
    expect(coord.activeWorkers[0].workerId).toBe(w1.workerId);
  });

  it("retry creates continuation with lineage", () => {
    const coord = Coordinator.create("s1", "test");
    const w1 = coord.delegate("t1", "r1");
    w1.start(); w1.fail("timeout");
    const w2 = coord.retry(w1.workerId, "t1 retry");
    expect(w2.parentWorkerId).toBe(w1.workerId);
    expect(coord.workers).toHaveLength(2);
  });

  it("rejects invalid worker lifecycle transitions", () => {
    const coord = Coordinator.create("s1", "test");
    const worker = coord.delegate("t1", "r1");

    expect(() => coord.completeWorker(worker.workerId, "done")).toThrow("status=pending");

    worker.start();
    coord.completeWorker(worker.workerId, "done");
    expect(() => coord.stopWorker(worker.workerId, "too late")).toThrow("status=completed");
  });

  it("only retries failed or redirected workers", () => {
    const coord = Coordinator.create("s1", "test");
    const worker = coord.delegate("t1", "r1");
    worker.start();
    coord.completeWorker(worker.workerId, "done");

    expect(() => coord.retry(worker.workerId, "retry")).toThrow("failed or redirected");
  });

  it("emits structured events", () => {
    const coord = Coordinator.create("s1", "test");
    coord.delegate("t1", "r1");
    const types = coord.events.map((e) => e.eventType);
    expect(types).toContain(CoordinatorEventType.COORDINATOR_CREATED);
    expect(types).toContain(CoordinatorEventType.WORKER_DELEGATED);
  });
});
