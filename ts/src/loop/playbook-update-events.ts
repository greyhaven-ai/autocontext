export const PLAYBOOK_UPDATE_SKIPPED_EVENT = "playbook_update_skipped";

export type PlaybookUpdateSkippedReason = "guard_rejected" | "missing_markers";

export interface PlaybookUpdateSkippedPayload extends Record<string, unknown> {
  run_id: string;
  scenario: string;
  generation: number;
  reason: PlaybookUpdateSkippedReason;
  missing_markers: string[];
  guard_reason?: string;
}

/** Render the skipped-update event for human-facing CLI and TUI surfaces. */
export function formatPlaybookUpdateSkipped(
  payload: Record<string, unknown>,
): string {
  const generation = typeof payload.generation === "number"
    ? ` in generation ${payload.generation}`
    : "";

  if (payload.reason === "missing_markers") {
    const missingMarkers = Array.isArray(payload.missing_markers)
      ? payload.missing_markers.filter((marker): marker is string => typeof marker === "string")
      : [];
    const markerDetail = missingMarkers.length > 0
      ? `: missing coach markers ${missingMarkers.join(", ")}`
      : ": coach output is missing required markers";
    return `playbook update skipped${generation}${markerDetail}`;
  }

  if (payload.reason === "guard_rejected") {
    const guardDetail = typeof payload.guard_reason === "string" && payload.guard_reason.trim()
      ? ` (${payload.guard_reason.trim()})`
      : "";
    return `playbook update skipped${generation}: guard rejected the proposed playbook${guardDetail}`;
  }

  return `playbook update skipped${generation}`;
}
