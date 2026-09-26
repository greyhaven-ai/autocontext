/**
 * Refuses a caller-chosen run id that already names a stored run (AC-1048).
 *
 * The TypeScript runtime cannot resume a run: re-entering one restarts at
 * generation 1 and rewrites the stored generations while the run row keeps its
 * scenario, target and status. Python `autoctx resume` continues existing runs,
 * so every TypeScript entry point that accepts a run id checks it here before
 * writing anything. The first three refusals match the Python loop's messages.
 */

import type { RunRow } from "../storage/index.js";

/** Run rows the generation loop did not write; mirrors Python's NON_LOOP_EXECUTOR_MODES. */
export const NON_LOOP_EXECUTOR_MODES: ReadonlySet<string> = new Set([
  "agent_task",
  "artifact_editing",
  "import",
]);

export interface RunLookup {
  getRun(runId: string): Pick<RunRow, "scenario" | "status" | "executor_mode"> | null;
}

/** Why `runId` cannot start a run of `scenarioName`, or null when no stored run has that id. */
export function existingRunRefusal(
  store: RunLookup,
  runId: string,
  scenarioName: string,
): string | null {
  const run = store.getRun(runId);
  if (!run) return null;
  if (run.status === "stopped") {
    return `run '${runId}' was stopped and is terminal; start a new run id to continue`;
  }
  if (run.scenario !== scenarioName) {
    return `run '${runId}' belongs to scenario '${run.scenario}', not '${scenarioName}'`;
  }
  if (NON_LOOP_EXECUTOR_MODES.has(run.executor_mode)) {
    return `run '${runId}' was not created by the generation loop and cannot be resumed`;
  }
  return `run '${runId}' already exists; continue it with the Python \`autoctx resume\` or use a new run id`;
}

export function assertRunIdAvailable(store: RunLookup, runId: string, scenarioName: string): void {
  const refusal = existingRunRefusal(store, runId, scenarioName);
  if (refusal) {
    throw new Error(refusal);
  }
}
