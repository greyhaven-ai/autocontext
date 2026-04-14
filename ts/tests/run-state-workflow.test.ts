import { describe, expect, it, vi } from "vitest";

import {
  buildRunEventStatePatch,
  mergeRunManagerState,
  notifyRunStateSubscribers,
} from "../src/server/run-state-workflow.js";

describe("run state workflow", () => {
  it("maps run lifecycle events into state patches", () => {
    expect(buildRunEventStatePatch("run_started", {
      run_id: "run_1",
      scenario: "grid_ctf",
    }, {
      active: true,
      paused: false,
      runId: null,
      scenario: null,
      generation: null,
      phase: "queued",
    })).toEqual({
      runId: "run_1",
      scenario: "grid_ctf",
      phase: "run",
    });

    expect(buildRunEventStatePatch("generation_started", {
      generation: 3,
    }, {
      active: true,
      paused: false,
      runId: "run_1",
      scenario: "grid_ctf",
      generation: 2,
      phase: "run",
    })).toEqual({
      generation: 3,
      phase: "agents",
    });

    expect(buildRunEventStatePatch("run_completed", {}, {
      active: true,
      paused: false,
      runId: "run_1",
      scenario: "grid_ctf",
      generation: 3,
      phase: "support",
    })).toEqual({
      phase: "completed",
    });
  });

  it("returns null for events that do not affect run state", () => {
    expect(buildRunEventStatePatch("unknown_event", {}, {
      active: false,
      paused: false,
      runId: null,
      scenario: null,
      generation: null,
      phase: null,
    })).toBeNull();
  });

  it("merges a run state snapshot with a patch", () => {
    expect(mergeRunManagerState({
      active: true,
      paused: false,
      runId: "run_1",
      scenario: "grid_ctf",
      generation: 1,
      phase: "agents",
    }, {
      generation: 2,
      phase: "gate",
    })).toEqual({
      active: true,
      paused: false,
      runId: "run_1",
      scenario: "grid_ctf",
      generation: 2,
      phase: "gate",
    });
  });

  it("notifies subscribers without letting one failure stop the rest", () => {
    const first = vi.fn(() => {
      throw new Error("boom");
    });
    const second = vi.fn();

    notifyRunStateSubscribers([
      first,
      second,
    ], {
      active: false,
      paused: false,
      runId: null,
      scenario: null,
      generation: null,
      phase: null,
    });

    expect(first).toHaveBeenCalledOnce();
    expect(second).toHaveBeenCalledOnce();
  });
});
