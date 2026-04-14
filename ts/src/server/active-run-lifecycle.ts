import type { EventStreamEmitter } from "../loop/events.js";
import type { RunManagerState } from "./run-manager.js";

export function buildQueuedRunStatePatch(opts: {
  runId: string;
  scenario: string;
  paused: boolean;
}): Partial<RunManagerState> {
  return {
    active: true,
    paused: opts.paused,
    runId: opts.runId,
    scenario: opts.scenario,
    generation: null,
    phase: "queued",
  };
}

export function buildIdleRunStatePatch(paused: boolean): Partial<RunManagerState> {
  return {
    active: false,
    paused,
    generation: null,
    phase: null,
  };
}

export async function createManagedRunExecution(opts: {
  runId: string;
  execute: () => Promise<void>;
  events: Pick<EventStreamEmitter, "emit">;
  getPaused: () => boolean;
  setActive: (active: boolean) => void;
  updateState: (patch: Partial<RunManagerState>) => void;
}): Promise<void> {
  try {
    await opts.execute();
  } catch (err) {
    opts.events.emit("run_failed", {
      run_id: opts.runId,
      error: err instanceof Error ? err.message : String(err),
    });
  } finally {
    opts.setActive(false);
    opts.updateState(buildIdleRunStatePatch(opts.getPaused()));
  }
}
