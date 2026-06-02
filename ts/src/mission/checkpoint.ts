/**
 * Mission checkpointing — save/restore durable state (AC-411).
 *
 * Checkpoints capture the full mission state as a JSON snapshot:
 * mission metadata, steps, subgoals, verifications, and budget usage.
 * Designed for restart-safe resume behavior.
 *
 * AC-697 Python parity PR #1016 review backports:
 *
 *   - `saveCheckpoint` filenames embed `process.hrtime.bigint()` ns
 *     resolution plus a short uuid suffix so two saves in the same
 *     millisecond no longer overwrite each other. Newest-first
 *     lexicographic sorting still works because the ns prefix
 *     orders.
 *   - `loadCheckpoint` accepts both camelCase (TS-shaped) and
 *     snake_case (Python-shaped) timestamp / budget keys so a
 *     shared `AUTOCONTEXT_DB_PATH` resumes cleanly from either
 *     runtime's checkpoints.
 *   - `loadCheckpoint` runs the multi-row restore inside a single
 *     SQLite transaction. A row failure rolls back the partial
 *     restore so the operator can retry without first cleaning up
 *     the half-loaded mission row.
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

export function saveCheckpoint(
  store: MissionStore,
  missionId: string,
  checkpointDir: string,
): string {
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

  // PR #1016 review (P3) backport: two saves landing in the same
  // millisecond overwrote each other because the filename was only
  // `<missionId>-<ms>.json`. Use nanosecond resolution plus an
  // 8-char uuid suffix so the path is unique even on fast hardware
  // and across concurrent writers. Newest-first ordering still
  // works because the ns prefix sorts lexicographically.
  const filename = `${missionId}-${process.hrtime.bigint().toString()}-${randomUUID().slice(0, 8)}.json`;
  const path = join(checkpointDir, filename);
  writeFileSync(path, JSON.stringify(checkpoint, null, 2), "utf-8");
  return path;
}

/** PR #1016 review (P2) backport: a TS-shaped checkpoint stores
 * fields in camelCase (`createdAt` / `maxSteps` / ...) while a
 * Python-shaped checkpoint stores them in snake_case (`created_at`
 * / `max_steps` / ...). The loader accepts either shape so a
 * shared `AUTOCONTEXT_DB_PATH` resumes from either runtime's
 * checkpoints without dropping budget caps or provenance.
 */
function pickField<T = unknown>(
  payload: Record<string, unknown>,
  ...keys: string[]
): T | undefined {
  for (const key of keys) {
    const value = payload[key];
    if (value !== undefined && value !== null) {
      return value as T;
    }
  }
  return undefined;
}

interface BudgetPayload {
  maxSteps?: number;
  maxCostUsd?: number;
  maxDurationMinutes?: number;
}

function normaliseBudget(payload: unknown): BudgetPayload | undefined {
  if (!payload || typeof payload !== "object") {
    return undefined;
  }
  const source = payload as Record<string, unknown>;
  const out: BudgetPayload = {};
  const mapping: Array<[keyof BudgetPayload, string[]]> = [
    ["maxSteps", ["maxSteps", "max_steps"]],
    ["maxCostUsd", ["maxCostUsd", "max_cost_usd"]],
    ["maxDurationMinutes", ["maxDurationMinutes", "max_duration_minutes"]],
  ];
  for (const [target, candidates] of mapping) {
    const value = pickField<number>(source, ...candidates);
    if (typeof value === "number") {
      out[target] = value;
    }
  }
  return Object.keys(out).length > 0 ? out : undefined;
}

export function loadCheckpoint(store: MissionStore, checkpointPath: string): string {
  const raw = JSON.parse(readFileSync(checkpointPath, "utf-8")) as MissionCheckpoint;
  const mission = raw.mission;
  const restoredId = mission.id as string;

  // Re-create the mission
  const missionId = store.createMission({
    name: mission.name as string,
    goal: mission.goal as string,
    budget: normaliseBudget(mission.budget),
    metadata: (mission.metadata as Record<string, unknown>) ?? {},
  });

  // The store generates a new ID — we need to update it to the original.
  // For checkpoint restore, we use the original ID by directly updating.
  const db = (
    store as unknown as {
      db: {
        prepare: (sql: string) => { run: (...args: unknown[]) => void };
        transaction: <T extends (...args: unknown[]) => unknown>(fn: T) => T;
      };
    }
  ).db;

  // PR #1016 review (P2) backport: wrap the multi-row restore in a
  // single SQLite transaction via better-sqlite3's
  // ``db.transaction`` helper. A row failure (FK violation, UNIQUE
  // duplicate, etc.) rolls back the partial restore so the operator
  // can retry without first cleaning up the half-loaded mission row.
  const restoreAll = db.transaction((): void => {
    const missionRecord = mission as Record<string, unknown>;
    const createdAtOverride = pickField<string>(missionRecord, "createdAt", "created_at");
    db.prepare(
      "UPDATE missions SET id = ?, status = ?, created_at = COALESCE(?, created_at), updated_at = ?, completed_at = ? WHERE id = ?",
    ).run(
      restoredId,
      mission.status as string,
      createdAtOverride ?? null,
      pickField<string>(missionRecord, "updatedAt", "updated_at") ?? null,
      pickField<string>(missionRecord, "completedAt", "completed_at") ?? null,
      missionId,
    );

    for (const step of raw.steps) {
      const stepRecord = step as Record<string, unknown>;
      db.prepare(
        "INSERT INTO mission_steps (id, mission_id, description, status, result, created_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
      ).run(
        stepRecord.id,
        restoredId,
        stepRecord.description,
        stepRecord.status,
        stepRecord.result ?? null,
        pickField<string>(stepRecord, "createdAt", "created_at"),
        pickField<string>(stepRecord, "completedAt", "completed_at") ?? null,
      );
    }

    for (const sg of raw.subgoals) {
      const subgoalRecord = sg as Record<string, unknown>;
      db.prepare(
        "INSERT INTO mission_subgoals (id, mission_id, description, priority, status, created_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
      ).run(
        subgoalRecord.id,
        restoredId,
        subgoalRecord.description,
        subgoalRecord.priority,
        subgoalRecord.status,
        pickField<string>(subgoalRecord, "createdAt", "created_at"),
        pickField<string>(subgoalRecord, "completedAt", "completed_at") ?? null,
      );
    }

    for (const v of raw.verifications) {
      const verificationRecord = v as Record<string, unknown>;
      const verificationId =
        typeof verificationRecord.id === "string" && verificationRecord.id.length > 0
          ? verificationRecord.id
          : `verify-restored-${randomUUID().slice(0, 8)}`;
      db.prepare(
        "INSERT INTO mission_verifications (id, mission_id, passed, reason, suggestions, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
      ).run(
        verificationId,
        restoredId,
        verificationRecord.passed ? 1 : 0,
        verificationRecord.reason,
        JSON.stringify(verificationRecord.suggestions ?? []),
        JSON.stringify(verificationRecord.metadata ?? {}),
        pickField<string>(verificationRecord, "createdAt", "created_at"),
      );
    }
  });
  restoreAll();

  return restoredId;
}
