/**
 * Mission SQLite storage (AC-410).
 *
 * Persists missions, steps, and verification results.
 * Uses same better-sqlite3 pattern as the main SQLiteStore.
 */

import Database from "better-sqlite3";
import { randomUUID } from "node:crypto";
import { StepStatusSchema, SubgoalStatusSchema } from "./types.js";
import type {
  Mission,
  MissionBudget,
  MissionStatus,
  MissionStep,
  MissionSubgoal,
  StepStatus,
  SubgoalStatus,
} from "./types.js";

// ---- Row types for better-sqlite3 query results ----

interface MissionRow {
  id: string;
  name: string;
  goal: string;
  status: string;
  budget: string | null;
  metadata: string;
  created_at: string;
  updated_at: string | null;
  completed_at: string | null;
}

interface StepRow {
  id: string;
  mission_id: string;
  description: string;
  status: string;
  result: string | null;
  error: string | null;
  tool_calls: string;
  metadata: string;
  created_at: string;
  completed_at: string | null;
  parent_step_id: string | null;
  order_index: number;
}

interface SubgoalRow {
  id: string;
  mission_id: string;
  description: string;
  priority: number;
  status: string;
  steps_json: string;
  created_at: string;
  completed_at: string | null;
}

interface VerificationRow {
  id: string;
  mission_id: string;
  step_id: string | null;
  claim: string;
  evidence: string;
  confidence: number;
  verified: number;
  metadata: string;
  created_at: string;
}

// ---- Row → domain mappers ----

function missionFromRow(row: MissionRow): Mission {
  return {
    id: row.id,
    name: row.name,
    goal: row.goal,
    status: row.status as MissionStatus,
    budget: row.budget ? JSON.parse(row.budget) as MissionBudget : undefined,
    metadata: JSON.parse(row.metadata ?? "{}"),
    createdAt: row.created_at,
    updatedAt: row.updated_at ?? undefined,
    completedAt: row.completed_at ?? undefined,
  };
}

function stepFromRow(row: StepRow): MissionStep {
  const status = StepStatusSchema.safeParse(row.status);
  return {
    id: row.id,
    missionId: row.mission_id,
    description: row.description,
    status: status.success ? status.data : ("pending" as StepStatus),
    result: row.result ?? undefined,
    createdAt: row.created_at,
    completedAt: row.completed_at ?? undefined,
  };
}

function subgoalFromRow(row: SubgoalRow): MissionSubgoal {
  const status = SubgoalStatusSchema.safeParse(row.status);
  return {
    id: row.id,
    missionId: row.mission_id,
    description: row.description,
    priority: row.priority ?? 0,
    status: status.success ? status.data : ("pending" as SubgoalStatus),
    createdAt: row.created_at,
    completedAt: row.completed_at ?? undefined,
  };
}

export class MissionStore {
  private db: Database.Database;
  private dbPath: string;

  constructor(dbPath: string) {
    this.dbPath = dbPath;
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

      CREATE TABLE IF NOT EXISTS mission_subgoals (
        id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL REFERENCES missions(id),
        description TEXT NOT NULL,
        priority INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        completed_at TEXT
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
    const completedAt = parsedStatus === "completed" || parsedStatus === "failed" || parsedStatus === "blocked" || parsedStatus === "skipped"
      ? new Date().toISOString()
      : null;
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

  getVerifications(missionId: string): Array<{
    id: string;
    passed: boolean;
    reason: string;
    suggestions: string[];
    metadata: Record<string, unknown>;
    createdAt: string;
  }> {
    const rows = this.db.prepare(
      "SELECT * FROM mission_verifications WHERE mission_id = ? ORDER BY created_at",
    ).all(missionId) as Array<Record<string, unknown>>;
    return rows.map((row) => ({
      id: row.id as string,
      passed: (row.passed as number) === 1,
      reason: row.reason as string,
      suggestions: JSON.parse((row.suggestions as string) ?? "[]"),
      metadata: JSON.parse((row.metadata as string) ?? "{}"),
      createdAt: row.created_at as string,
    }));
  }

  // -------------------------------------------------------------------------
  // Subgoals (AC-411)
  // -------------------------------------------------------------------------

  addSubgoal(missionId: string, opts: { description: string; priority?: number }): string {
    const id = `subgoal-${randomUUID().slice(0, 8)}`;
    this.db.prepare(
      "INSERT INTO mission_subgoals (id, mission_id, description, priority) VALUES (?, ?, ?, ?)",
    ).run(id, missionId, opts.description, opts.priority ?? 1);
    return id;
  }

  getSubgoals(missionId: string): MissionSubgoal[] {
    const rows = this.db.prepare(
      "SELECT * FROM mission_subgoals WHERE mission_id = ? ORDER BY priority ASC, created_at ASC",
    ).all(missionId) as Array<Record<string, unknown>>;
    return rows.map((row) => ({
      id: row.id as string,
      missionId: row.mission_id as string,
      description: row.description as string,
      priority: row.priority as number,
      status: (row.status as SubgoalStatus) ?? "pending",
      createdAt: row.created_at as string,
      completedAt: (row.completed_at as string) ?? undefined,
    }));
  }

  updateSubgoalStatus(id: string, status: SubgoalStatus): void {
    const parsedStatus = SubgoalStatusSchema.parse(status);
    const completedAt = parsedStatus === "completed" || parsedStatus === "failed" || parsedStatus === "skipped"
      ? new Date().toISOString()
      : null;
    this.db.prepare(
      "UPDATE mission_subgoals SET status = ?, completed_at = ? WHERE id = ?",
    ).run(parsedStatus, completedAt, id);
  }

  // -------------------------------------------------------------------------
  // Budget usage (AC-411)
  // -------------------------------------------------------------------------

  getBudgetUsage(missionId: string): { stepsUsed: number; maxSteps?: number; maxCostUsd?: number; exhausted: boolean } {
    const mission = this.getMission(missionId);
    const stepsUsed = (this.db.prepare(
      "SELECT COUNT(*) as count FROM mission_steps WHERE mission_id = ?",
    ).get(missionId) as { count: number }).count;

    const maxSteps = mission?.budget?.maxSteps;
    const maxCostUsd = mission?.budget?.maxCostUsd;
    const exhausted = maxSteps !== undefined ? stepsUsed >= maxSteps : false;

    return {
      stepsUsed,
      ...(maxSteps !== undefined ? { maxSteps } : {}),
      ...(maxCostUsd !== undefined ? { maxCostUsd } : {}),
      exhausted,
    };
  }

  getDbPath(): string {
    return this.dbPath;
  }

  close(): void {
    this.db.close();
  }
}
