/**
 * Mission checkpointing — save/restore durable state (AC-411).
 *
 * Checkpoints capture the full mission state as a JSON snapshot:
 * mission metadata, steps, subgoals, verifications, and budget usage.
 * Designed for restart-safe resume behavior.
 */

import { mkdirSync, writeFileSync, readFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { join } from "node:path";
import type { MissionStore } from "./store.js";

export interface MissionCheckpoint {
  version: 1;
  checkpointedAt: string;
  mission: Record<string, unknown>;
  steps: Array<Record<string, unknown>>;
  subgoals: Array<Record<string, unknown>>;
  verifications: Array<Record<string, unknown>>;
  budgetUsage: { stepsUsed: number; maxSteps?: number; maxCostUsd?: number; exhausted: boolean };
}

export function saveCheckpoint(store: MissionStore, missionId: string, checkpointDir: string): string {
  mkdirSync(checkpointDir, { recursive: true });

  const mission = store.getMission(missionId);
  if (!mission) throw new Error(`Mission not found: ${missionId}`);

  const steps = store.getSteps(missionId);
  const subgoals = store.getSubgoals(missionId);
  const verifications = store.getVerifications(missionId);
  const budgetUsage = store.getBudgetUsage(missionId);

  const checkpoint: MissionCheckpoint = {
    version: 1,
    checkpointedAt: new Date().toISOString(),
    mission: mission as unknown as Record<string, unknown>,
    steps: steps as unknown as Array<Record<string, unknown>>,
    subgoals: subgoals as unknown as Array<Record<string, unknown>>,
    verifications: verifications as unknown as Array<Record<string, unknown>>,
    budgetUsage,
  };

  const filename = `${missionId}-${Date.now()}.json`;
  const path = join(checkpointDir, filename);
  writeFileSync(path, JSON.stringify(checkpoint, null, 2), "utf-8");
  return path;
}

export function loadCheckpoint(store: MissionStore, checkpointPath: string): string {
  const raw = JSON.parse(readFileSync(checkpointPath, "utf-8")) as MissionCheckpoint;
  const mission = raw.mission;

  // Re-create the mission
  const missionId = store.createMission({
    name: mission.name as string,
    goal: mission.goal as string,
    budget: mission.budget as { maxSteps?: number; maxCostUsd?: number; maxDurationMinutes?: number } | undefined,
    metadata: (mission.metadata as Record<string, unknown>) ?? {},
  });

  // The store generates a new ID — we need to update it to the original
  // For checkpoint restore, we use the original ID by directly updating
  const db = (store as unknown as { db: { prepare: (sql: string) => { run: (...args: unknown[]) => void } } }).db;
  db.prepare("UPDATE missions SET id = ?, status = ?, updated_at = ?, completed_at = ? WHERE id = ?").run(
    mission.id as string,
    mission.status as string,
    (mission.updatedAt as string) ?? null,
    (mission.completedAt as string) ?? null,
    missionId,
  );

  const restoredId = mission.id as string;

  // Restore steps
  for (const step of raw.steps) {
    db.prepare(
      "INSERT INTO mission_steps (id, mission_id, description, status, result, created_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
    ).run(
      step.id, restoredId, step.description, step.status,
      step.result ?? null, step.createdAt, step.completedAt ?? null,
    );
  }

  // Restore subgoals
  for (const sg of raw.subgoals) {
    db.prepare(
      "INSERT INTO mission_subgoals (id, mission_id, description, priority, status, created_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
    ).run(
      sg.id, restoredId, sg.description, sg.priority, sg.status,
      sg.createdAt, sg.completedAt ?? null,
    );
  }

  // Restore verifications
  for (const v of raw.verifications) {
    const verificationId = typeof v.id === "string" && v.id.length > 0
      ? v.id
      : `verify-restored-${randomUUID().slice(0, 8)}`;
    db.prepare(
      "INSERT INTO mission_verifications (id, mission_id, passed, reason, suggestions, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
    ).run(
      verificationId, restoredId,
      v.passed ? 1 : 0, v.reason,
      JSON.stringify(v.suggestions ?? []),
      JSON.stringify(v.metadata ?? {}),
      v.createdAt,
    );
  }

  return restoredId;
}
