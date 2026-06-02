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

  // PR #1016 review (P3) + PR #1020 review (P2) backport: two saves
  // landing in the same millisecond used to overwrite each other.
  // The first attempt switched to `process.hrtime.bigint()` ns but
  // that counter is monotonic since process start (not Unix time),
  // so after a reboot a newer checkpoint can sort before an older
  // one and downstream artifact / analysis readers that pick
  // newest-by-filename would surface stale state. Use wall-clock
  // nanoseconds via `Date.now() * 1e6 + (hrtime % 1e6)` so the
  // prefix is Unix-time-based and survives reboots; the lower 6
  // digits come from hrtime's sub-ms resolution so two saves inside
  // the same ms still differ. The 8-char uuid suffix is the final
  // tiebreaker for fully-concurrent writers.
  const wallClockNanos = BigInt(Date.now()) * 1_000_000n + (process.hrtime.bigint() % 1_000_000n);
  const filename = `${missionId}-${wallClockNanos.toString()}-${randomUUID().slice(0, 8)}.json`;
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

  const db = (
    store as unknown as {
      db: {
        prepare: (sql: string) => {
          run: (...args: unknown[]) => void;
          get: (...args: unknown[]) => unknown;
        };
        transaction: <T extends (...args: unknown[]) => unknown>(fn: T) => T;
      };
    }
  ).db;

  // PR #1020 review (P2): refuse to restore over an existing
  // mission row. The previous behaviour was to let SQLite raise on
  // the INSERT, but a friendlier message helps the operator
  // distinguish "shared db with the original mission still in it"
  // from a more subtle FK / UNIQUE failure deeper in the restore.
  const existing = db.prepare("SELECT id FROM missions WHERE id = ?").get(restoredId) as
    | { id: string }
    | undefined;
  if (existing !== undefined) {
    throw new Error(`Cannot restore checkpoint: mission ${restoredId} already exists`);
  }

  // PR #1016 review (P2) backport + PR #1020 review (P2): wrap the
  // FULL restore (mission insert + child rows) in a single SQLite
  // transaction via better-sqlite3's ``db.transaction`` helper. The
  // previous shape called ``store.createMission()`` BEFORE the
  // transaction, so a later child-row failure rolled back only the
  // child inserts and left an orphan mission row with a freshly
  // generated id (not the original) committed. The fix inserts the
  // mission row directly via SQL inside the transaction so a
  // rollback returns the DB to its prior state with no orphan.
  const missionRecord = mission as Record<string, unknown>;
  const budgetDict = normaliseBudget(mission.budget);
  const budgetBlob = budgetDict ? JSON.stringify(budgetDict) : null;
  const metadataBlob = JSON.stringify(
    (mission.metadata as Record<string, unknown> | undefined) ?? {},
  );

  const restoreAll = db.transaction((): void => {
    db.prepare(
      "INSERT INTO missions (id, name, goal, status, budget, metadata, created_at, updated_at, completed_at) " +
        "VALUES (?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')), ?, ?)",
    ).run(
      restoredId,
      mission.name as string,
      mission.goal as string,
      mission.status as string,
      budgetBlob,
      metadataBlob,
      pickField<string>(missionRecord, "createdAt", "created_at") ?? null,
      pickField<string>(missionRecord, "updatedAt", "updated_at") ?? null,
      pickField<string>(missionRecord, "completedAt", "completed_at") ?? null,
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
