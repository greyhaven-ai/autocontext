export function runtimeSessionIdForRun(runId: string): string {
  return `run:${runId}:runtime`;
}
