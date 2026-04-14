import { describe, expect, it, vi } from "vitest";

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
});
