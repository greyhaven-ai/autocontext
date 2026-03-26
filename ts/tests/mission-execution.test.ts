/**
 * Tests for AC-412: Verifier-driven mission execution loop.
 *
 * - StepExecutor interface
 * - runStep: execute one step with budget check
 * - runUntilDone: loop until verified, blocked, or budget exhausted
 * - Honest failure states: blocked, budget_exhausted
 * - State machine transition validation
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-exec-"));
}

// ---------------------------------------------------------------------------
// Extended MissionStatus — honest failure states
// ---------------------------------------------------------------------------

describe("Extended mission statuses", () => {
  it("MissionStatusSchema includes blocked, budget_exhausted, and verifier_failed", async () => {
    const { MissionStatusSchema } = await import("../src/mission/types.js");
    expect(MissionStatusSchema.parse("blocked")).toBe("blocked");
    expect(MissionStatusSchema.parse("budget_exhausted")).toBe("budget_exhausted");
    expect(MissionStatusSchema.parse("verifier_failed")).toBe("verifier_failed");
  });
});

// ---------------------------------------------------------------------------
// StepExecutor interface
// ---------------------------------------------------------------------------

describe("StepExecutor", () => {
  it("StepResult type is exported", async () => {
    const mod = await import("../src/mission/executor.js");
    expect(mod).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// runStep — single bounded step
// ---------------------------------------------------------------------------

describe("runStep", () => {
  let dir: string;
  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("executes one step and records it", async () => {
    const { MissionManager } = await import("../src/mission/manager.js");
    const { runStep } = await import("../src/mission/executor.js");
    const manager = new MissionManager(join(dir, "test.db"));

    const mId = manager.create({ name: "Test", goal: "g" });
    const result = await runStep(manager, mId, async () => ({
      description: "Created migration file",
      status: "completed" as const,
    }));

    expect(result.stepRecorded).toBe(true);
    expect(manager.steps(mId).length).toBe(1);
    manager.close();
  });

  it("returns budget_exhausted when budget is exceeded", async () => {
    const { MissionManager } = await import("../src/mission/manager.js");
    const { runStep } = await import("../src/mission/executor.js");
    const manager = new MissionManager(join(dir, "test.db"));

    const mId = manager.create({ name: "Test", goal: "g", budget: { maxSteps: 1 } });
    // First step — within budget
    await runStep(manager, mId, async () => ({
      description: "Step 1",
      status: "completed" as const,
    }));

    // Second step — exceeds budget
    const result = await runStep(manager, mId, async () => ({
      description: "Step 2",
      status: "completed" as const,
    }));

    expect(result.budgetExhausted).toBe(true);
    expect(manager.get(mId)!.status).toBe("budget_exhausted");
    manager.close();
  });

  it("records step as failed when executor throws", async () => {
    const { MissionManager } = await import("../src/mission/manager.js");
    const { runStep } = await import("../src/mission/executor.js");
    const manager = new MissionManager(join(dir, "test.db"));

    const mId = manager.create({ name: "Test", goal: "g" });
    const result = await runStep(manager, mId, async () => {
      throw new Error("git push rejected");
    });

    expect(result.stepRecorded).toBe(true);
    expect(result.error).toContain("git push rejected");
    const steps = manager.steps(mId);
    expect(steps[0].status).toBe("failed");
    manager.close();
  });

  it("marks mission as blocked when step returns blocked", async () => {
    const { MissionManager } = await import("../src/mission/manager.js");
    const { runStep } = await import("../src/mission/executor.js");
    const manager = new MissionManager(join(dir, "test.db"));

    const mId = manager.create({ name: "Test", goal: "g" });
    const result = await runStep(manager, mId, async () => ({
      description: "Waiting for PR review",
      status: "blocked" as const,
      blockReason: "Needs approval from code owner",
    }));

    expect(result.blocked).toBe(true);
    expect(manager.get(mId)!.status).toBe("blocked");
    const steps = manager.steps(mId);
    expect(steps).toHaveLength(1);
    expect(steps[0].status).toBe("blocked");
    expect(steps[0].result).toBe("Needs approval from code owner");
    manager.close();
  });

  it("does not execute steps for paused missions", async () => {
    const { MissionManager } = await import("../src/mission/manager.js");
    const { runStep } = await import("../src/mission/executor.js");
    const manager = new MissionManager(join(dir, "test.db"));

    const mId = manager.create({ name: "Test", goal: "g" });
    manager.pause(mId);

    const result = await runStep(manager, mId, async () => ({
      description: "Should never run",
      status: "completed" as const,
    }));

    expect(result.stepRecorded).toBe(false);
    expect(result.finalStatus).toBe("paused");
    expect(manager.steps(mId)).toHaveLength(0);
    manager.close();
  });
});

// ---------------------------------------------------------------------------
// runUntilDone — execution loop
// ---------------------------------------------------------------------------

describe("runUntilDone", () => {
  let dir: string;
  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("loops steps until verifier passes", async () => {
    const { MissionManager } = await import("../src/mission/manager.js");
    const { runUntilDone } = await import("../src/mission/executor.js");
    const manager = new MissionManager(join(dir, "test.db"));

    const mId = manager.create({ name: "Test", goal: "g" });
    let stepCount = 0;
    manager.setVerifier(mId, async () => ({
      passed: stepCount >= 3,
      reason: stepCount >= 3 ? "All done" : "Not yet",
    }));

    const result = await runUntilDone(manager, mId, async () => {
      stepCount++;
      return { description: `Step ${stepCount}`, status: "completed" as const };
    });

    expect(result.finalStatus).toBe("completed");
    expect(result.stepsExecuted).toBe(3);
    expect(manager.get(mId)!.status).toBe("completed");
    manager.close();
  });

  it("stops on budget exhaustion", async () => {
    const { MissionManager } = await import("../src/mission/manager.js");
    const { runUntilDone } = await import("../src/mission/executor.js");
    const manager = new MissionManager(join(dir, "test.db"));

    const mId = manager.create({ name: "Test", goal: "g", budget: { maxSteps: 2 } });
    manager.setVerifier(mId, async () => ({ passed: false, reason: "Never done" }));

    const result = await runUntilDone(manager, mId, async () => ({
      description: "work",
      status: "completed" as const,
    }));

    expect(result.finalStatus).toBe("budget_exhausted");
    expect(result.stepsExecuted).toBe(2);
    manager.close();
  });

  it("stops on blocked step", async () => {
    const { MissionManager } = await import("../src/mission/manager.js");
    const { runUntilDone } = await import("../src/mission/executor.js");
    const manager = new MissionManager(join(dir, "test.db"));

    const mId = manager.create({ name: "Test", goal: "g" });
    let stepCount = 0;
    manager.setVerifier(mId, async () => ({ passed: false, reason: "Not yet" }));

    const result = await runUntilDone(manager, mId, async () => {
      stepCount++;
      if (stepCount === 2) {
        return { description: "Blocked on review", status: "blocked" as const, blockReason: "Needs approval" };
      }
      return { description: `Step ${stepCount}`, status: "completed" as const };
    });

    expect(result.finalStatus).toBe("blocked");
    expect(result.stepsExecuted).toBe(2);
    manager.close();
  });

  it("does not execute canceled missions", async () => {
    const { MissionManager } = await import("../src/mission/manager.js");
    const { runUntilDone } = await import("../src/mission/executor.js");
    const manager = new MissionManager(join(dir, "test.db"));

    const mId = manager.create({ name: "Test", goal: "g" });
    manager.cancel(mId);
    let callCount = 0;

    const result = await runUntilDone(manager, mId, async () => {
      callCount++;
      return { description: "Should never run", status: "completed" as const };
    }, { maxIterations: 3 });

    expect(callCount).toBe(0);
    expect(result.finalStatus).toBe("canceled");
    expect(result.stepsExecuted).toBe(0);
    expect(manager.steps(mId)).toHaveLength(0);
    manager.close();
  });

  it("returns verifier_failed when the verifier throws", async () => {
    const { MissionManager } = await import("../src/mission/manager.js");
    const { MissionStore } = await import("../src/mission/store.js");
    const { runUntilDone } = await import("../src/mission/executor.js");
    const dbPath = join(dir, "test.db");
    const manager = new MissionManager(dbPath);

    const mId = manager.create({ name: "Test", goal: "g" });
    manager.setVerifier(mId, async () => {
      throw new Error("Verifier transport failed");
    });

    const result = await runUntilDone(manager, mId, async () => ({
      description: "work",
      status: "completed" as const,
    }), { maxIterations: 1 });

    expect(result.finalStatus).toBe("verifier_failed");
    expect(result.verifierPassed).toBe(false);
    expect(manager.get(mId)!.status).toBe("verifier_failed");
    manager.close();

    const store = new MissionStore(dbPath);
    const verifications = store.getVerifications(mId);
    expect(verifications).toHaveLength(1);
    expect(verifications[0].reason).toContain("Verifier error: Verifier transport failed");
    store.close();
  });

  it("respects maxIterations safety limit", async () => {
    const { MissionManager } = await import("../src/mission/manager.js");
    const { runUntilDone } = await import("../src/mission/executor.js");
    const manager = new MissionManager(join(dir, "test.db"));

    const mId = manager.create({ name: "Test", goal: "g" });
    manager.setVerifier(mId, async () => ({ passed: false, reason: "Never" }));

    const result = await runUntilDone(manager, mId, async () => ({
      description: "work",
      status: "completed" as const,
    }), { maxIterations: 5 });

    expect(result.stepsExecuted).toBe(5);
    expect(result.finalStatus).toBe("active");
    manager.close();
  });
});
