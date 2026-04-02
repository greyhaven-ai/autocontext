import { describe, expect, it } from "vitest";
import { Supervisor, SupervisedEntry, SupervisorState } from "../src/session/supervisor.js";

describe("SupervisedEntry", () => {
  it("creates with launching state", () => {
    const entry = SupervisedEntry.create({ sessionId: "s1", goal: "test", workspace: "/tmp" });
    expect(entry.entryId).toBeTruthy();
    expect(entry.state).toBe(SupervisorState.LAUNCHING);
  });

  it("lifecycle transitions", () => {
    const entry = SupervisedEntry.create({ sessionId: "s1", goal: "test" });
    entry.markRunning();
    expect(entry.state).toBe(SupervisorState.RUNNING);
    entry.markWaiting("approval");
    expect(entry.state).toBe(SupervisorState.WAITING);
    expect(entry.blockedReason).toBe("approval");
    entry.markRunning();
    expect(entry.blockedReason).toBe("");
    entry.markCompleted();
    expect(entry.state).toBe(SupervisorState.COMPLETED);
  });

  it("is_alive for active states", () => {
    const entry = SupervisedEntry.create({ sessionId: "s1", goal: "test" });
    entry.markRunning();
    expect(entry.isAlive).toBe(true);
    entry.markCompleted();
    expect(entry.isAlive).toBe(false);
  });
});

describe("Supervisor", () => {
  it("launches and registers", () => {
    const sup = new Supervisor();
    const entry = sup.launch({ sessionId: "s1", goal: "test", workspace: "/tmp" });
    expect(entry.sessionId).toBe("s1");
    expect(sup.get("s1")).toBeTruthy();
  });

  it("lists active only", () => {
    const sup = new Supervisor();
    sup.launch({ sessionId: "s1", goal: "g1" });
    sup.launch({ sessionId: "s2", goal: "g2" });
    const e3 = sup.launch({ sessionId: "s3", goal: "g3" });
    e3.markRunning(); e3.markCompleted();
    expect(sup.listActive()).toHaveLength(2);
  });

  it("rejects duplicate session ids", () => {
    const sup = new Supervisor();
    sup.launch({ sessionId: "s1", goal: "test" });
    expect(() => sup.launch({ sessionId: "s1", goal: "test2" })).toThrow("already supervised");
  });

  it("stops a session", () => {
    const sup = new Supervisor();
    const entry = sup.launch({ sessionId: "s1", goal: "test" });
    entry.markRunning();
    sup.stop("s1");
    expect(entry.state).toBe(SupervisorState.STOPPING);
  });

  it("rejects reopening terminal entries", () => {
    const sup = new Supervisor();
    const entry = sup.launch({ sessionId: "s1", goal: "test" });
    entry.markCompleted();

    expect(() => sup.stop("s1")).toThrow("state=completed");
    expect(() => entry.markRunning()).toThrow("state=completed");
  });
});
