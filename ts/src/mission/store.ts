/**
 * Mission SQLite storage (AC-410).
 *
 * Persists missions, steps, and verification results.
 * Uses same better-sqlite3 pattern as the main SQLiteStore.
 */

import Database from "better-sqlite3";
import { enableWal } from "../storage/sqlite-wal.js";
import type {
  Mission,
  MissionBudget,
  MissionBudgetUsage,
  MissionRow,
  MissionStatus,
  MissionStep,
  MissionSubgoal,
  MissionVerificationRecord,
  StepRow,
  StepStatus,
  SubgoalRow,
  SubgoalStatus,
  VerificationRow,
} from "./store-contracts.js";
import {
  buildMissionBudgetUsage,
  buildMissionCompletionTimestamp,
  buildMissionVerificationRecord,
  buildStepCompletionTimestamp,
  buildSubgoalCompletionTimestamp,
  generateMissionRecordId,
} from "./store-lifecycle-workflow.js";
import { missionFromRow, stepFromRow, subgoalFromRow } from "./store-mappers.js";
import { createMissionStoreTables } from "./store-schema-workflow.js";

export class MissionStore {
  private db: Database.Database;
  private dbPath: string;

  constructor(dbPath: string) {
    this.dbPath = dbPath;
    this.db = new Database(dbPath);
    enableWal(this.db);
    this.db.pragma("foreign_keys = ON");
    createMissionStoreTables(this.db);
  }

  createMission(opts: {
    name: string;
    goal: string;
    budget?: MissionBudget;
    metadata?: Record<string, unknown>;
  }): string {
    const id = generateMissionRecordId("mission");
    this.db.prepare(
      `INSERT INTO missions (id, name, goal, budget, metadata)
       VALUES (?, ?, ?, ?, ?)`,
    ).run(
      id,
      opts.name,
      opts.goal,
      opts.budget ? JSON.stringify(opts.budget) : null,
      JSON.stringify(opts.metadata ?? {}),
    );
    return id;
  }

  getMission(id: string): Mission | null {
    const row = this.db.prepare("SELECT * FROM missions WHERE id = ?").get(id) as MissionRow | undefined;
    if (!row) return null;
    return missionFromRow(row);
  }

  listMissions(status?: MissionStatus): Mission[] {
    const sql = status
      ? "SELECT * FROM missions WHERE status = ? ORDER BY created_at DESC"
      : "SELECT * FROM missions ORDER BY created_at DESC";
    const rows = (status
      ? this.db.prepare(sql).all(status)
      : this.db.prepare(sql).all()) as MissionRow[];
    return rows.map(missionFromRow);
  }

  updateMissionStatus(id: string, status: MissionStatus): void {
    const completedAt = buildMissionCompletionTimestamp(status);
    this.db.prepare(
      "UPDATE missions SET status = ?, updated_at = datetime('now'), completed_at = ? WHERE id = ?",
    ).run(status, completedAt, id);
  }

  addStep(missionId: string, opts: { description: string }): string {
    const id = generateMissionRecordId("step");
    this.db.prepare(
      "INSERT INTO mission_steps (id, mission_id, description, status) VALUES (?, ?, ?, 'completed')",
    ).run(id, missionId, opts.description);
    return id;
  }

  getSteps(missionId: string): MissionStep[] {
    const rows = this.db.prepare(
      "SELECT * FROM mission_steps WHERE mission_id = ? ORDER BY created_at",
    ).all(missionId) as StepRow[];
    return rows.map(stepFromRow);
  }

  updateStepStatus(id: string, status: StepStatus, result?: string): void {
    const completedAt = buildStepCompletionTimestamp(status);
    this.db.prepare(
      "UPDATE mission_steps SET status = ?, result = COALESCE(?, result), completed_at = ? WHERE id = ?",
    ).run(status, result ?? null, completedAt, id);
  }

  recordVerification(missionId: string, result: { passed: boolean; reason: string; suggestions?: string[]; metadata?: Record<string, unknown> }): void {
    const id = generateMissionRecordId("verify");
    this.db.prepare(
      "INSERT INTO mission_verifications (id, mission_id, passed, reason, suggestions, metadata) VALUES (?, ?, ?, ?, ?, ?)",
    ).run(
      id,
      missionId,
      result.passed ? 1 : 0,
      result.reason,
      JSON.stringify(result.suggestions ?? []),
      JSON.stringify(result.metadata ?? {}),
    );
  }

  getVerifications(missionId: string): MissionVerificationRecord[] {
    const rows = this.db.prepare(
      "SELECT * FROM mission_verifications WHERE mission_id = ? ORDER BY created_at",
    ).all(missionId) as VerificationRow[];
    return rows.map((row) => buildMissionVerificationRecord(row));
  }

  // -------------------------------------------------------------------------
  // Subgoals (AC-411)
  // -------------------------------------------------------------------------

  addSubgoal(missionId: string, opts: { description: string; priority?: number }): string {
    const id = generateMissionRecordId("subgoal");
    this.db.prepare(
      "INSERT INTO mission_subgoals (id, mission_id, description, priority) VALUES (?, ?, ?, ?)",
    ).run(id, missionId, opts.description, opts.priority ?? 1);
    return id;
  }

  getSubgoals(missionId: string): MissionSubgoal[] {
    const rows = this.db.prepare(
      "SELECT * FROM mission_subgoals WHERE mission_id = ? ORDER BY priority ASC, created_at ASC",
    ).all(missionId) as SubgoalRow[];
    return rows.map(subgoalFromRow);
  }

  updateSubgoalStatus(id: string, status: SubgoalStatus): void {
    const completedAt = buildSubgoalCompletionTimestamp(status);
    this.db.prepare(
      "UPDATE mission_subgoals SET status = ?, completed_at = ? WHERE id = ?",
    ).run(status, completedAt, id);
  }

  // -------------------------------------------------------------------------
  // Budget usage (AC-411)
  // -------------------------------------------------------------------------

  getBudgetUsage(missionId: string): MissionBudgetUsage {
    const mission = this.getMission(missionId);
    const stepsUsed = (this.db.prepare(
      "SELECT COUNT(*) as count FROM mission_steps WHERE mission_id = ?",
    ).get(missionId) as { count: number }).count;
    return buildMissionBudgetUsage(mission, stepsUsed);
  }

  getDbPath(): string {
    return this.dbPath;
  }

  close(): void {
    this.db.close();
  }
}
