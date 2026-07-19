import type { EventStreamEmitter } from "../loop/events.js";
import {
  isRunStopRequestedError,
  type RunStopProgress,
  type RunStopRequestedError,
} from "../loop/controller.js";
import type { RunManagerState } from "./run-manager.js";
import { isTerminalRunPhase } from "./run-state-workflow.js";

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
  getRunPhase?: () => string | null;
  getStopProgress?: () => RunStopProgress;
  getStopRequest?: () => RunStopRequestedError | null;
  setActive: (active: boolean) => void;
  updateState: (patch: Partial<RunManagerState>) => void;
}): Promise<void> {
  try {
    await opts.execute();
  } catch (error) {
    if (!isTerminalRunPhase(opts.getRunPhase?.() ?? null)) {
      const stopRequest = isRunStopRequestedError(error)
        ? error
        : opts.getStopRequest?.() ?? null;
      if (stopRequest) {
        const progress = opts.getStopProgress?.() ?? {
          completedGenerations: stopRequest.completedGenerations,
          ...(stopRequest.bestScore === undefined ? {} : { bestScore: stopRequest.bestScore }),
        };
        const stopped = stopRequest.withProgress(progress);
        opts.events.emit("run_stopped", {
          run_id: opts.runId,
          reason: "operator",
          command_id: stopped.commandId,
          completed_generations: stopped.completedGenerations,
          ...(stopped.bestScore === undefined ? {} : { best_score: stopped.bestScore }),
        });
      } else {
        opts.events.emit("run_failed", {
          run_id: opts.runId,
          error: error instanceof Error ? error.message : String(error),
        });
      }
    }
  } finally {
    opts.setActive(false);
    opts.updateState(buildIdleRunStatePatch(opts.getPaused()));
  }
}
