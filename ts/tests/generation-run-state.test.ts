import { describe, expect, it } from "vitest";

import {
  completeGenerationRun,
  consumeFreshStartHint,
  createGenerationRunState,
  failGenerationRun,
  queueFreshStartHint,
  recordGenerationResult,
} from "../src/loop/generation-run-state.js";

describe("generation run state", () => {
  it("starts a run with running status and defaults", () => {
    const state = createGenerationRunState({
      runId: "run-1",
      scenarioName: "linear_outage_escalation",
      targetGenerations: 3,
      startedAtMs: 123,
    });

    expect(state.status).toBe("running");
    expect(state.bestScore).toBe(0);
    expect(state.currentElo).toBe(1000);
    expect(state.generationsCompleted).toBe(0);
    expect(state.pendingFreshStartHint).toBeNull();
  });

  it("records generation outcomes and preserves the best score", () => {
    const started = createGenerationRunState({
      runId: "run-1",
      scenarioName: "linear_outage_escalation",
      targetGenerations: 3,
      startedAtMs: 100,
    });

    const afterFirst = recordGenerationResult(started, {
      generation: 1,
      bestScore: 0.6,
      elo: 1005,
    });
    const afterSecond = recordGenerationResult(afterFirst, {
      generation: 2,
      bestScore: 0.4,
      elo: 999,
    });

    expect(afterFirst.generationsCompleted).toBe(1);
    expect(afterFirst.bestScore).toBe(0.6);
    expect(afterSecond.generationsCompleted).toBe(2);
    expect(afterSecond.bestScore).toBe(0.6);
    expect(afterSecond.currentElo).toBe(999);
  });

  it("queues and consumes fresh-start hints as one-shot state", () => {
    const started = createGenerationRunState({
      runId: "run-1",
      scenarioName: "linear_outage_escalation",
      targetGenerations: 3,
      startedAtMs: 100,
    });

    const queued = queueFreshStartHint(started, "Try a fresh direction");
    const consumed = consumeFreshStartHint(queued);

    expect(queued.pendingFreshStartHint).toBe("Try a fresh direction");
    expect(consumed.hint).toBe("Try a fresh direction");
    expect(consumed.state.pendingFreshStartHint).toBeNull();
  });

  it("marks runs completed or failed", () => {
    const started = createGenerationRunState({
      runId: "run-1",
      scenarioName: "linear_outage_escalation",
      targetGenerations: 3,
      startedAtMs: 100,
    });

    const completed = completeGenerationRun(started, { finishedAtMs: 150 });
    const failed = failGenerationRun(started, {
      finishedAtMs: 175,
      error: "boom",
    });

    expect(completed.status).toBe("completed");
    expect(completed.finishedAtMs).toBe(150);
    expect(failed.status).toBe("failed");
    expect(failed.error).toBe("boom");
  });
});
