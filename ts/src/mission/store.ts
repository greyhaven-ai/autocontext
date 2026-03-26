/**
 * Mission SQLite storage (AC-410).
 *
 * Persists missions, steps, and verification results.
 * Uses same better-sqlite3 pattern as the main SQLiteStore.
 */

import Database from "better-sqlite3";
import { randomUUID } from "node:crypto";
import { StepStatusSchema } from "./types.js";
import type { Mission, MissionBudget, MissionStatus, MissionStep, StepStatus } from "./types.js";

export class MissionStore {
  private db: Database.Database;

  constructor(dbPath: string) {
    this.db = new Database(dbPath);
    this.db.pragma("journal_mode = WAL");
    this.db.pragma("foreign_keys = ON");
    this.createTables();
  }

  private createTables(): void {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS missions (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        goal TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        budget TEXT,
        metadata TEXT DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT,
        completed_at TEXT
      );

      CREATE TABLE IF NOT EXISTS mission_steps (
        id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL REFERENCES missions(id),
        description TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        result TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        completed_at TEXT
      );

      CREATE TABLE IF NOT EXISTS mission_verifications (
        id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL REFERENCES missions(id),
        passed INTEGER NOT NULL,
        reason TEXT NOT NULL,
        suggestions TEXT DEFAULT '[]',
        metadata TEXT DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
      );
    `);
  }

  createMission(opts: {
    name: string;
    goal: string;
    budget?: MissionBudget;
    metadata?: Record<string, unknown>;
  }): string {
    const id = `mission-${randomUUID().slice(0, 8)}`;
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
    const row = this.db.prepare("SELECT * FROM missions WHERE id = ?").get(id) as Record<string, unknown> | undefined;
    if (!row) return null;
    return {
      id: row.id as string,
      name: row.name as string,
      goal: row.goal as string,
      status: row.status as MissionStatus,
      budget: row.budget ? JSON.parse(row.budget as string) : undefined,
      metadata: JSON.parse((row.metadata as string) ?? "{}"),
      createdAt: row.created_at as string,
      updatedAt: (row.updated_at as string) ?? undefined,
      completedAt: (row.completed_at as string) ?? undefined,
    };
  }

  listMissions(status?: MissionStatus): Mission[] {
    const sql = status
      ? "SELECT * FROM missions WHERE status = ? ORDER BY created_at DESC"
      : "SELECT * FROM missions ORDER BY created_at DESC";
    const rows = (status
      ? this.db.prepare(sql).all(status)
      : this.db.prepare(sql).all()) as Array<Record<string, unknown>>;
    return rows.map((row) => ({
      id: row.id as string,
      name: row.name as string,
      goal: row.goal as string,
      status: row.status as MissionStatus,
      budget: row.budget ? JSON.parse(row.budget as string) : undefined,
      metadata: JSON.parse((row.metadata as string) ?? "{}"),
      createdAt: row.created_at as string,
      updatedAt: (row.updated_at as string) ?? undefined,
      completedAt: (row.completed_at as string) ?? undefined,
    }));
  }

  updateMissionStatus(id: string, status: MissionStatus): void {
    const completedAt = status === "completed" || status === "failed" || status === "canceled"
      ? new Date().toISOString()
      : null;
    this.db.prepare(
      "UPDATE missions SET status = ?, updated_at = datetime('now'), completed_at = ? WHERE id = ?",
    ).run(status, completedAt, id);
  }

  addStep(missionId: string, opts: { description: string }): string {
    const id = `step-${randomUUID().slice(0, 8)}`;
    this.db.prepare(
      "INSERT INTO mission_steps (id, mission_id, description, status) VALUES (?, ?, ?, 'completed')",
    ).run(id, missionId, opts.description);
    return id;
  }

  getSteps(missionId: string): MissionStep[] {
    const rows = this.db.prepare(
      "SELECT * FROM mission_steps WHERE mission_id = ? ORDER BY created_at",
    ).all(missionId) as Array<Record<string, unknown>>;
    return rows.map((row) => ({
      id: row.id as string,
      missionId: row.mission_id as string,
      description: row.description as string,
      status: (row.status as MissionStep["status"]) ?? "pending",
      result: (row.result as string) ?? undefined,
      createdAt: row.created_at as string,
      completedAt: (row.completed_at as string) ?? undefined,
    }));
  }

  updateStepStatus(id: string, status: StepStatus, result?: string): void {
    const parsedStatus = StepStatusSchema.parse(status);
    const completedAt = status === "completed" || status === "failed" ? new Date().toISOString() : null;
    this.db.prepare(
      "UPDATE mission_steps SET status = ?, result = COALESCE(?, result), completed_at = ? WHERE id = ?",
    ).run(parsedStatus, result ?? null, completedAt, id);
  }

  recordVerification(missionId: string, result: { passed: boolean; reason: string; suggestions?: string[]; metadata?: Record<string, unknown> }): void {
    const id = `verify-${randomUUID().slice(0, 8)}`;
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

  getVerifications(missionId: string): Array<{ passed: boolean; reason: string; suggestions: string[]; createdAt: string }> {
    const rows = this.db.prepare(
      "SELECT * FROM mission_verifications WHERE mission_id = ? ORDER BY created_at",
    ).all(missionId) as Array<Record<string, unknown>>;
    return rows.map((row) => ({
      passed: (row.passed as number) === 1,
      reason: row.reason as string,
      suggestions: JSON.parse((row.suggestions as string) ?? "[]"),
      createdAt: row.created_at as string,
    }));
  }

  close(): void {
    this.db.close();
  }
}
