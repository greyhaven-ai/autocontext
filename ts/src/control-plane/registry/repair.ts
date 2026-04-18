import { mkdirSync, existsSync, unlinkSync } from "node:fs";
import { join } from "node:path";
import type { Artifact, PromotionEvent } from "../contract/types.js";
import {
  artifactDirectory,
  listArtifactIds,
  loadArtifact,
} from "./artifact-store.js";
import { readHistory } from "./history-store.js";
import {
  listStatePointers,
  statePointerPath,
  writeStatePointer,
} from "./state-pointer.js";

/**
 * Walk every artifact's promotion-history.jsonl, fold it to determine the
 * artifact's final state, and rebuild `state/active/` pointers from the
 * resulting set. Idempotent.
 *
 * Algorithm:
 *   1. Ensure `state/active/` exists.
 *   2. Group artifacts by (scenario, actuatorType, environmentTag).
 *   3. For each group, find the artifact that is currently in `active` state.
 *      If multiple, pick the one whose most recent promotion-to-active timestamp
 *      is latest (last-writer-wins).
 *   4. Write/overwrite the pointer to that artifact, or remove the pointer
 *      file entirely if no artifact is active in the group.
 */
export function repair(registryRoot: string): void {
  const stateRoot = join(registryRoot, ".autocontext", "state", "active");
  mkdirSync(stateRoot, { recursive: true });

  const ids = listArtifactIds(registryRoot);
  const artifacts: Artifact[] = [];
  for (const id of ids) {
    let art: Artifact;
    try {
      art = loadArtifact(registryRoot, id);
    } catch {
      // Skip unreadable artifacts; validate.ts surfaces them.
      continue;
    }
    // Use the on-disk history file as ground truth — it may be longer than
    // metadata.json's promotionHistory if a crash occurred between appendHistory
    // and updateArtifactMetadata.
    let history: PromotionEvent[];
    try {
      history = readHistory(join(artifactDirectory(registryRoot, id), "promotion-history.jsonl"));
    } catch {
      history = [...art.promotionHistory];
    }
    artifacts.push({ ...art, promotionHistory: history });
  }

  // Group by tuple, find the canonical "active" artifact for each.
  const groups = new Map<string, Artifact[]>();
  for (const a of artifacts) {
    const key = `${a.scenario}|${a.actuatorType}|${a.environmentTag}`;
    const arr = groups.get(key) ?? [];
    arr.push(a);
    groups.set(key, arr);
  }

  const desiredKeys = new Set<string>();
  for (const [key, arts] of groups) {
    // Final state is the foldedActivationState; for repair we trust the on-disk
    // history rather than metadata, but most of the time these agree.
    const actives = arts.filter((a) => foldedActivationState(a) === "active");
    if (actives.length === 0) continue;
    actives.sort((a, b) => latestActiveTimestamp(b).localeCompare(latestActiveTimestamp(a)));
    const winner = actives[0];
    writeStatePointer(
      registryRoot,
      winner.scenario,
      winner.actuatorType,
      winner.environmentTag,
      { artifactId: winner.id, asOf: latestActiveTimestamp(winner) },
    );
    desiredKeys.add(key);
  }

  // Remove stale pointers — any pointer file that doesn't correspond to a
  // group we wrote above must be deleted.
  for (const entry of listStatePointers(registryRoot)) {
    const key = `${entry.scenario}|${entry.actuatorType}|${entry.environmentTag}`;
    if (!desiredKeys.has(key)) {
      const p = statePointerPath(
        registryRoot,
        entry.scenario,
        entry.actuatorType,
        entry.environmentTag,
      );
      if (existsSync(p)) unlinkSync(p);
    }
  }
}

/**
 * Replay the promotion history to compute the artifact's current activation
 * state. If history is empty, falls back to the artifact's stored value.
 */
function foldedActivationState(a: Artifact): Artifact["activationState"] {
  if (a.promotionHistory.length === 0) return a.activationState;
  return a.promotionHistory[a.promotionHistory.length - 1].to;
}

/**
 * Returns the timestamp of the most recent transition INTO the active state.
 * Returns "" when none — used only for sorting, lexicographic ISO is fine.
 */
function latestActiveTimestamp(a: Artifact): string {
  let latest = "";
  for (const ev of a.promotionHistory) {
    if (ev.to === "active" && ev.timestamp > latest) latest = ev.timestamp;
  }
  return latest;
}
