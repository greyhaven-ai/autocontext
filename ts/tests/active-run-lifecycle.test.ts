import { describe, expect, it, vi } from "vitest";

import { RunStopRequestedError } from "../src/loop/controller.js";
import {
  buildIdleRunStatePatch,
  buildQueuedRunStatePatch,
  createManagedRunExecution,
} from "../src/server/active-run-lifecycle.js";

describe("active run lifecycle", () => {
  it("builds the queued run state patch for a newly accepted run", () => {
    expect(buildQueuedRunStatePatch({
      runId: "run_123",
      scenario: "grid_ctf",
      paused: true,
    })).toEqual({
      active: true,
      paused: true,
      runId: "run_123",
      scenario: "grid_ctf",
      generation: null,
      phase: "queued",
    });
  });

  it("builds the idle run state patch used after run completion or failure", () => {
    expect(buildIdleRunStatePatch(false)).toEqual({
      active: false,
      paused: false,
      generation: null,
      phase: null,
    });
  });

  it("emits run_failed and finalizes active state when execution rejects", async () => {
    const emit = vi.fn();
    const updateState = vi.fn();
    const setActive = vi.fn();

    await createManagedRunExecution({
      runId: "run_123",
      execute: async () => {
        throw new Error("boom");
      },
      events: { emit },
      getPaused: () => true,
      setActive,
      updateState,
    });

    expect(emit).toHaveBeenCalledWith("run_failed", {
      run_id: "run_123",
      error: "boom",
    });
    expect(setActive).toHaveBeenCalledWith(false);
    expect(updateState).toHaveBeenCalledWith({
      active: false,
      paused: true,
      generation: null,
      phase: null,
    });
  });

  it("still finalizes active state when execution succeeds", async () => {
    const emit = vi.fn();
    const updateState = vi.fn();
    const setActive = vi.fn();

    await createManagedRunExecution({
      runId: "run_456",
      execute: async () => {},
      events: { emit },
      getPaused: () => false,
      setActive,
      updateState,
    });

    expect(emit).not.toHaveBeenCalled();
    expect(setActive).toHaveBeenCalledWith(false);
    expect(updateState).toHaveBeenCalledWith({
      active: false,
      paused: false,
      generation: null,
      phase: null,
    });
  });

  it("emits one stopped terminal with retained progress instead of run_failed", async () => {
    const emit = vi.fn();
    const updateState = vi.fn();
    const setActive = vi.fn();
    const stopRequest = new RunStopRequestedError({
      runId: "run_stop_1",
      commandId: "cmd_stop_1",
      progress: {
        completedGenerations: 2,
        bestScore: 0.6,
      },
    });

    await createManagedRunExecution({
      runId: "run_stop_1",
      execute: async () => {
        throw new Error("provider failed after stop");
      },
      events: { emit },
      getPaused: () => false,
      getStopRequest: () => stopRequest,
      getStopProgress: () => ({
        completedGenerations: 3,
        bestScore: 0.75,
      }),
      setActive,
      updateState,
    });

    expect(emit).toHaveBeenCalledOnce();
    expect(emit).toHaveBeenCalledWith("run_stopped", {
      run_id: "run_stop_1",
      reason: "operator",
      command_id: "cmd_stop_1",
      completed_generations: 3,
      best_score: 0.75,
    });
    expect(emit).not.toHaveBeenCalledWith("run_failed", expect.anything());
  });

  it("does not replace an observed terminal event with a later failure", async () => {
    const emit = vi.fn();

    await createManagedRunExecution({
      runId: "run_terminal_1",
      execute: async () => {
        throw new Error("late provider failure");
      },
      events: { emit },
      getPaused: () => false,
      getRunPhase: () => "stopped",
      setActive: vi.fn(),
      updateState: vi.fn(),
    });

    expect(emit).not.toHaveBeenCalled();
  });
});
