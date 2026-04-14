import type { RunManagerState } from "./run-manager.js";

export function buildRunEventStatePatch(
  event: string,
  payload: Record<string, unknown>,
  state: RunManagerState,
): Partial<RunManagerState> | null {
  switch (event) {
    case "run_started":
      return {
        runId: (payload.run_id as string) ?? state.runId,
        scenario: (payload.scenario as string) ?? state.scenario,
        phase: "run",
      };
    case "generation_started":
      return {
        generation: (payload.generation as number) ?? state.generation,
        phase: "agents",
      };
    case "agents_started":
      return { phase: "agents" };
    case "tournament_started":
      return { phase: "tournament" };
    case "gate_decided":
      return { phase: "gate" };
    case "generation_completed":
      return {
        generation: (payload.generation as number) ?? state.generation,
        phase: "support",
      };
    case "run_completed":
      return { phase: "completed" };
    case "run_failed":
      return { phase: "failed" };
    default:
      return null;
  }
}

export function mergeRunManagerState(
  state: RunManagerState,
  patch: Partial<RunManagerState>,
): RunManagerState {
  return { ...state, ...patch };
}

export function notifyRunStateSubscribers(
  subscribers: Array<(state: RunManagerState) => void>,
  snapshot: RunManagerState,
): void {
  for (const subscriber of [...subscribers]) {
    try {
      subscriber(snapshot);
    } catch {
      // State observers should never crash the active run.
    }
  }
}
